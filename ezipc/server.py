from asyncio import (
    AbstractEventLoop,
    AbstractServer,
    gather,
    get_event_loop,
    run,
    start_server,
    StreamReader,
    StreamWriter,
    Task,
    TimeoutError,
    wait_for,
)
from collections import Counter, Set
from datetime import datetime as dt
from functools import partial
from socket import AF_INET, SOCK_DGRAM, socket
from typing import Dict, Optional, Union

from .remote import can_encrypt, rpc_response, Remote, RemoteError, request_handler
from .util import callback_response, echo, err, P, warn


__all__ = [
    "callback_response",
    "can_encrypt",
    "echo",
    "err",
    "rpc_response",
    "P",
    "Remote",
    "RemoteError",
    "request_handler",
    "Server",
    "warn",
]


class Server:
    """The Server is the component of the Client/Server Model that waits for
    input from a Client, and then operates on it. The Server can interface with
    multiple Clients at the same time, and may even facilitate communications
    between two Clients.

    As the more passive component, the Server will spend most of its time
    waiting for Clients.

    :param str addr: IPv4 Address to listen on. If this is not supplied, and
        ``autopublish`` is not `True`, ``127.0.0.1`` will be used.
    :param int port: IP Port to listen on.
    :param bool autopublish: If this is `True`, the Server will try to
        automatically discover the Network Address of the local system. If it
        cannot be found, ``addr`` will be used as a fallback.
    :param int helpers: The number of Helper Tasks to be used by **each**
        Remote. More Helpers can be useful when receiving prompts to perform
        very await-heavy procedures, such as multiple file transfers, but when
        not in use they mostly sap memory.
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

        self.eventloop: Optional[AbstractEventLoop] = None
        self.remotes: set = set()
        self.server: Optional[AbstractServer] = None
        self.startup: dt = dt.utcnow()

        self.total_clients: int = 0
        self.total_sent: Counter = Counter(byte=0, notif=0, request=0, response=0)
        self.total_recv: Counter = Counter(byte=0, notif=0, request=0, response=0)

        self.hooks_notif = {}
        self.hooks_request = {}

    def _add_hooks(self, *_a, **_kw):
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
            return func
        else:
            # Function NOT provided. Return a Decorator.
            return partial(self.hook_notif, method)

            # def hook(func_):
            #     self.hooks_notif[method] = func_
            #     return func_
            #
            # return hook

    def hook_request(self, method: str, func=None):
        """Signal to the Remote that `func` is waiting for Requests of the
            provided `method` value.
        """
        if func:
            # Function provided. Hook it directly.
            self.hooks_request[method] = func
            return func
        else:
            # Function NOT provided. Return a Decorator.
            return partial(self.hook_request, method)

            # def hook(func_):
            #     self.hooks_request[method] = func_
            #     return func_
            #
            # return hook

    async def broadcast(
        self,
        meth: str,
        params: Union[dict, list] = None,
        default=None,
        *,
        callback=None,
        **kw,
    ) -> Dict[Remote, Task]:
        if not self.remotes:
            return {}

        reqs = {
            r: self.eventloop.create_task(
                r.request_wait(meth, params, default, callback=callback, **kw)
            )
            for r in self.remotes
        }
        return reqs

    async def broadcast_wait(
        self,
        meth: str,
        params: Union[dict, list] = None,
        default=None,
        *,
        callback=None,
        **kw,
    ):
        reqs = await self.broadcast(meth, params, default, callback=callback, **kw)

        wins = await gather(*reqs.values(), return_exceptions=True)
        total = len(reqs)
        wincount = total - wins.count(None)

        echo("win", f"Messages successfully Broadcast: {wincount}/{total}")
        return list(zip(reqs.keys(), wins))

    def drop(self, remote: Remote):
        self.total_sent.update(remote.total_sent)
        self.total_recv.update(remote.total_recv)
        self.remotes.remove(remote)

    async def encrypt_remote(self, remote: Remote):
        echo("info", f"Starting Secure Connection with {remote}...")
        try:
            if await wait_for(remote.enable_rsa(), 10):
                echo("win", f"Secure Connection established with {remote}.")
            else:
                warn(f"Failed to establish Secure Connection with {remote}.")
        except TimeoutError:
            warn(f"Encryption Request to {remote} timed out.")

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
            f"Incoming Connection from Client at "
            f"`{str_out.get_extra_info('peername', ('Unknown Address', 0))[0]}`.",
        )
        remote = Remote(self.eventloop, str_in, str_out)
        self.total_clients += 1

        # Update the Client Hooks with our own.
        remote.hooks_notif.update(self.hooks_notif)
        # remote.hooks_request.update(self.hooks_request)
        remote.hooks_request = self.hooks_request
        remote.startup = self.startup

        self.remotes.add(remote)
        echo("diff", f"Client at {remote.host} has been assigned UUID {remote.id}.")

        # Encrypt the Remote Connection.
        rsa = self.eventloop.create_task(self.encrypt_remote(remote))
        try:
            # Run the Remote Loop Coroutine. While this runs, messages received
            #   from the Remote Client are added to the Queue of the Remote
            #   object. This Coroutine also dispatches other Tasks to handle the
            #   received messages.
            await remote.loop(self.helpers)

        finally:
            try:
                # Try not to drop the remote before the RSA Handshake is done.
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

        echo("info", f"Running Server on {self.addr}:{self.port}")
        self.server = await start_server(
            self.open_connection, self.addr, self.port, loop=self.eventloop
        )
        echo("win", "Ready to begin accepting Requests.")
        await self.server.serve_forever()

    def start(self, *a, **kw):
        """Run alone and do nothing else. For very simple implementations that
            do not need to do anything else at the same time.
        """
        self._add_hooks(*a, **kw)

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
                    f"Served {self.total_clients} Clients in"
                    f" {str(dt.utcnow() - self.startup)[:-7]}.",
                )
                echo("info", "Sent:")
                echo(
                    "tab",
                    [f"> {v} {k.capitalize()}s" for k, v in self.total_sent.items()],
                )
                echo("info", "Received:")
                echo(
                    "tab",
                    [f"> {v} {k.capitalize()}s" for k, v in self.total_recv.items()],
                )
            except Exception:
                return
