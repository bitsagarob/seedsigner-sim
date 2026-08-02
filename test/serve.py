"""
Static server that sends the two headers the wallet cannot run without.

Without COOP same-origin and COEP require-corp the page is not cross-origin
isolated, SharedArrayBuffer is not constructible, and the wallet hangs before it
draws anything: both the keyboard and the camera reach the worker through one.
A plain `python3 -m http.server` therefore does not work for this page at all.

Several roots can be given and the first one holding a file wins. That is how a
checkout is served without being copied anywhere first: the page and its scripts
live in src/web, the Python shims the worker fetches live in src/shims, and the
two big build outputs (wallet.zip and the Pyodide runtime) are downloaded into
somewhere else again. Overlaying them means no staging directory to keep in sync
and no stale copy to serve by accident.

Usage: python3 serve.py [--port N] [--host H] ROOT [ROOT ...]
"""

import argparse
import os
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer


class IsolatedHandler(SimpleHTTPRequestHandler):
    roots = ()

    # Python only learned .wasm in 3.11, and Pyodide starts with
    # WebAssembly.instantiateStreaming, which rejects anything that does not
    # arrive as application/wasm. Older interpreters would serve it as
    # octet-stream and the wallet would never boot.
    extensions_map = {
        **SimpleHTTPRequestHandler.extensions_map,
        ".wasm": "application/wasm",
        ".mjs": "text/javascript",
        ".json": "application/json",
    }

    def translate_path(self, path):
        # The base class does the security-relevant work -- stripping the query,
        # unquoting, and dropping '..' -- against self.directory, so point it at
        # each root in turn rather than reimplementing any of that here.
        for root in self.roots:
            self.directory = root
            candidate = super().translate_path(path)
            if os.path.exists(candidate):
                return candidate
        self.directory = self.roots[0]
        return super().translate_path(path)  # 404 from the first root

    def end_headers(self):
        self.send_header("Cross-Origin-Opener-Policy", "same-origin")
        self.send_header("Cross-Origin-Embedder-Policy", "require-corp")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        # A test that passes against a cached copy of the file it is meant to be
        # testing has proved nothing.
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, *args):
        pass


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("roots", nargs="+", help="directories to overlay, first match wins")
    parser.add_argument("--port", type=int, default=int(os.environ.get("SIM_PORT", "8770")))
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()

    roots = [os.path.abspath(r) for r in args.roots]
    for root in roots:
        if not os.path.isdir(root):
            print(f"no such directory: {root}", file=sys.stderr)
            return 2

    IsolatedHandler.roots = tuple(roots)
    print(f"serving on http://{args.host}:{args.port}", flush=True)
    for root in roots:
        print(f"  {root}", flush=True)
    ThreadingHTTPServer((args.host, args.port), IsolatedHandler).serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
