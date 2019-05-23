import asyncio


def client_test():
    from .client import Client

    async def go(_client):
        """One of the "main" Coroutines provided to `run_through()`. After the
            Client finishes its setup, it will call all Coroutines passed to
            `run_through()` in sequence, passing itself in; Thus, this Coroutine
            receives the Client and can operate it. Any Exceptions raised here
            will be caught by `run_through()`.
        """
        async def receive(data, conn):
            """Response handler Coroutine. Assigned to wait for a Response with
                a certain UUID, and called by the Listener when a Response with
                that UUID is received.
            """
            print("    RESPONSE from {}: {}".format(conn.id, data.get("result") or data.get("error")))

        print("Sending Requests...")

        for i in ["aaaa", "zxcv", "qwert", "wysiwyg"]:
            await asyncio.sleep(1)
            if not _client.alive:
                return

            # print("Sending...")
            # Send a Ping Request to the Server.
            uuid, ts = await _client.con.request("PING", [i])
            # Then, take the received UUID and pass it through the Response Hook
            #   of the Client Connection, specifying `receive()` as the Coro
            #   that will handle the Response.
            _client.con.hook_response(uuid.hex, receive)

            # print("Request sent.")

        # After the final line of the final Coroutine, the Client will end. One
        #   should take care that time is allotted to handle any Responses that
        #   may still be en route.
        await asyncio.sleep(1)

    Client().run_through(go)
    # `run_through()` may take any number of Coroutines as Arguments. They will
    #   be awaited, with the Client passed, sequentially.


def server_test():
    from .server import Server
    # If the Server will do nothing other than listen, it requires no more than
    #   this single call.
    Server().start()
