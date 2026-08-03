"""
Two pieces of plumbing the live tutorial test needs, and nothing else does.

**The origin.** Bitsaga Signet's API allows exactly one browser origin,
https://bitsaga.be, which is where this page is served from in production. A
test page on 127.0.0.1 is a different origin and the browser refuses the answer
before any of our code sees it. So the test serves the working tree *at that
origin*: Playwright intercepts https://bitsaga.be/... and answers it from the
local server, with the two isolation headers the wallet cannot run without. The
page is this checkout, the origin is the real one, and every request to
signet.bitsaga.be goes to the real host over the real network.

**The broadcast.** The public API has no endpoint that sends a transaction, so
the tutorial's last call, POST /api/broadcast, has nowhere to land yet. Until
one exists (see docs/SIGNET-API.md for the endpoint to add), the test answers
that one request itself, by handing the transaction to Fulcrum over the tailnet
with the same Electrum call the endpoint would use. The transaction really is
broadcast, to the real chain, and the confirmation that follows is read back
from the public API like everything else.

Nothing here touches the page's own code: the coordinator posts to the same URL
either way.
"""

import json
import os
import socket

LOCAL = "http://127.0.0.1"
SITE = "https://bitsaga.be"
API = "https://signet.bitsaga.be/api"

# Fulcrum, which wallets speak to and which broadcasting is one call to. It does
# not listen on anything public, on purpose, so where it does listen is not
# written down here: set SIGNET_ELECTRUM=host:port to run this file at all.
ELECTRUM = os.environ.get("SIGNET_ELECTRUM", "")

ISOLATION = {
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Embedder-Policy": "require-corp",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Cache-Control": "no-store",
}


def electrum(method, params, timeout=20):
    """One Electrum call on one short-lived connection."""
    if not ELECTRUM:
        raise RuntimeError("set SIGNET_ELECTRUM=host:port to reach the Electrum server; "
                           "it is not on any public address")
    host, _, port = ELECTRUM.partition(":")
    with socket.create_connection((host, int(port or 50001)), timeout) as sock:
        sock.settimeout(timeout)
        sock.sendall(json.dumps({"id": 0, "method": method, "params": params}).encode() + b"\n")
        buffer = b""
        while b"\n" not in buffer:
            chunk = sock.recv(65536)
            if not chunk:
                raise RuntimeError("the Electrum server closed the connection")
            buffer += chunk
    reply = json.loads(buffer.split(b"\n", 1)[0].decode())
    if "error" in reply:
        raise RuntimeError(str(reply["error"]))
    return reply["result"]


def serve_at(context, port, origin):
    """Answer <origin>/... from the local server, with the isolation headers."""

    def handler(route, request):
        path = request.url[len(origin):] or "/"
        if path.startswith("/seedsigner-simulator/"):
            path = path[len("/seedsigner-simulator"):]
        response = route.fetch(url=f"{LOCAL}:{port}{path}")
        headers = dict(response.headers)
        headers.update(ISOLATION)
        route.fulfill(response=response, headers=headers)

    context.route(f"{origin}/**", handler)


def serve_site_at_real_origin(context, port):
    """The live run: the page at the one origin Bitsaga Signet's API allows."""
    serve_at(context, port, SITE)


def answer_broadcast(context, seen):
    """Stand in for the endpoint that does not exist yet."""

    def handler(route, request):
        try:
            raw = json.loads(request.post_data)["tx"]
            txid = electrum("blockchain.transaction.broadcast", [raw])
            seen.append(txid)
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps({"ok": True, "txid": txid}),
                          headers={"Access-Control-Allow-Origin": SITE})
        except Exception as exc:                      # noqa: BLE001 - reported, not swallowed
            route.fulfill(status=502, content_type="application/json",
                          body=json.dumps({"error": str(exc)}),
                          headers={"Access-Control-Allow-Origin": SITE})

    context.route(f"{API}/broadcast", handler)
