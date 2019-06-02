from asyncio import (
    AbstractEventLoop,
    AbstractServer,
    get_event_loop,
    run,
    start_server,
    StreamReader,
    StreamWriter,
    TimeoutError,
    wait_for,
)
from collections import Counter, Set
from datetime import datetime as dt
from socket import AF_INET, SOCK_DGRAM, socket
from typing import Union

from .remote import can_encrypt, handled, Remote, RemoteError, request_handler
from .util import callback_response, echo, err, P, warn


class Server:
    """The Server is the component of the Client/Server Model that waits for
        input from a Client, and then operates on it. The Server can interface
        with multiple Clients at the same time, and may even facilitate
        communications between two Clients.

    As the more passive component, the Server will spend most of its time waiting.
    """

    def __init__(self, addr: str = "", port: int = 9002, autopublish=False, helpers=5):
        if autopublish:
            # Override the passed parameter and try to autofind the address.
            sock = socket(AF_INET, SOCK_DGRAM)
            try:
                sock.connect(("10.255.255.255", 1))
                addr = sock.getsockname()[0]
            except (InterruptedError, OSError):
                warn("Failed to autoconfigure IP address.")
            finally:
                sock.close()

        if not addr:
            # No Address specified, and autoconfig failed or was not enabled;
            #   Fall back to localhost.
            addr = "127.0.0.1"

        self.addr: str = addr
        self.port: int = port
        self.helpers = helpers

        self.eventloop: AbstractEventLoop = None
        self.remotes: set = set()
        self.server: AbstractServer = None
        self.startup: dt = dt.utcnow()

        self.total_clients: int = 0
        self.total_sent: Counter = Counter(byte=0, notif=0, request=0, response=0)
        self.total_recv: Counter = Counter(byte=0, notif=0, request=0, response=0)

        self.hooks_notif = {}
        self.hooks_request = {}

    def _setup(self, *_a, **_kw):
        """Execute all prerequisites to running, before running. Meant to be
            overwritten by Subclasses.
        """

        @self.hook_request("TIME")
        async def cb_time(data, conn: Remote):
            await conn.respond(
                data.get("id", "0"), res={"startup": self.startup.timestamp()}
            )

    def hook_notif(self, method: str, func=None):
        """Signal to the Remote that `func` is waiting for Notifications of the
            provided `method` value.
        """
        if func:
            # Function provided. Hook it directly.
            self.hooks_notif[method] = func
        else:
            # Function NOT provided. Return a Decorator.
            def hook(func_):
                self.hooks_notif[method] = func_

            return hook

    def hook_request(self, method: str, func=None):
        """Signal to the Remote that `func` is waiting for Requests of the
            provided `method` value.
        """
        if func:
            # Function provided. Hook it directly.
            self.hooks_request[method] = func
        else:
            # Function NOT provided. Return a Decorator.
            def hook(func_):
                self.hooks_request[method] = func_

            return hook

    async def broadcast(
        self, meth: str, params: Union[dict, list] = None, cb_broadcast=None
    ):
        if not self.remotes:
            return

        @callback_response
        def cb_confirm(data, remote):
            if data:
                echo("tab", "Broadcast '{}' received by {}.".format(meth, remote))
            else:
                warn("Broadcast '{}' NOT received by {}.".format(meth, remote))

        reqs = []
        for remote_ in self.remotes:
            reqs.append(
                (
                    remote_,
                    await remote_.request(
                        meth, params, callback=cb_broadcast or cb_confirm
                    ),
                )
            )

        for remote_, request in reqs:
            try:
                await wait_for(request, 10)
            except Exception:
                warn("{} timed out.".format(remote_))
                self.drop(remote_)

    def drop(self, remote: Remote):
        self.total_sent.update(remote.total_sent)
        self.total_recv.update(remote.total_recv)
        self.remotes.remove(remote)

    async def encrypt_remote(self, remote: Remote):
        echo("info", "Starting Secure Connection with {}...".format(remote))
        try:
            if await wait_for(remote.enable_rsa(), 10):
                echo("win", "Secure Connection established with {}.".format(remote))
            else:
                warn("Failed to establish Secure Connection with {}.".format(remote))
        except TimeoutError:
            warn("Encryption Request to {} timed out.".format(remote))

    async def terminate(self, reason: str = "Server Closing"):
        for remote in self.remotes:
            await remote.terminate(reason)
        self.remotes: Set[Remote] = set()
        if self.server.is_serving():
            self.server.close()
            await self.server.wait_closed()
        echo("dcon", "Server closed.")

    async def open_connection(self, str_in: StreamReader, str_out: StreamWriter):
        """Callback executed by AsyncIO when a Client contacts the Server."""
        echo(
            "con",
            "Incoming Connection from Client at `{}`.".format(
                str_out.get_extra_info("peername", ("Unknown Address", 0))[0]
            ),
        )
        remote = Remote(self.eventloop, str_in, str_out)
        self.total_clients += 1

        # Update the Client Hooks with our own.
        remote.hooks_notif.update(self.hooks_notif)
        remote.hooks_request.update(self.hooks_request)
        remote.startup = self.startup

        self.remotes.add(remote)
        echo(
            "diff",
            "Client at {} has been assigned UUID {}.".format(remote.host, remote.id),
        )

        rsa = self.eventloop.create_task(self.encrypt_remote(remote))
        try:
            await remote.loop(self.helpers)

        finally:
            try:
                await wait_for(rsa, 3)
            except TimeoutError:
                pass
            self.drop(remote)

    async def run(self, loop=None):
        """Server Coroutine. Does not setup or wrap the Server. Intended for use
            in instances where other things must be done, and the Server needs
            to be run properly asynchronously.
        """
        self.eventloop = loop or get_event_loop()

        echo("info", "Running Server on {}:{}".format(self.addr, self.port))
        self.server = await start_server(
            self.open_connection, self.addr, self.port, loop=self.eventloop
        )
        echo("win", "Ready to begin accepting Requests.")
        await self.server.serve_forever()

    def start(self, *a, **kw):
        """Run alone and do nothing else. For very simple implementations that
            do not need to do anything else at the same time.
        """
        self._setup(*a, **kw)

        try:
            run(self.run())
        except KeyboardInterrupt:
            err("INTERRUPTED. Server closing...")
            run(self.terminate("Server Interrupted"))
        except Exception as e:
            err("Server closing due to unexpected", e)
            run(self.terminate("Fatal Server Error"))
        else:
            echo("dcon", "Server closing...")
            run(self.terminate())
        finally:
            try:
                echo(
                    "info",
                    "Served {} Clients in {}.".format(
                        self.total_clients, str(dt.utcnow() - self.startup)[:-7]
                    ),
                )
                echo("info", "Sent:")
                echo(
                    "tab",
                    [
                        "> {} {}s".format(v, k.capitalize())
                        for k, v in self.total_sent.items()
                    ],
                )
                echo("info", "Received:")
                echo(
                    "tab",
                    [
                        "> {} {}s".format(v, k.capitalize())
                        for k, v in self.total_recv.items()
                    ],
                )
            except Exception:
                return
