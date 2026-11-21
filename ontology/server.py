import json
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def serve(store, port):
    class Handler(SimpleHTTPRequestHandler):
        def respond(self, fn):
            try:
                body, status = json.dumps(fn(), default=str), 200
            except (ValueError, KeyError, TypeError) as exc:
                body, status = json.dumps({"error": str(exc)}), 400
            payload = body.encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self):
            if self.path.startswith("/api/batch/"):
                self.respond(lambda: store.changes(int(self.path.rsplit("/", 1)[-1])))
            else:
                super().do_GET()

        def do_POST(self):
            def mutate():
                if self.headers.get_content_type() != "application/json":
                    raise ValueError("Expected application/json")
                origin = self.headers.get("Origin")
                if origin and origin != f"http://127.0.0.1:{port}":
                    raise ValueError("Invalid origin")
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 2048:
                    raise ValueError("Invalid request length")
                data = json.loads(self.rfile.read(length))
                if self.path == "/api/decide":
                    store.decide(int(data["id"]), data["decision"])
                    return {"ok": True}
                if self.path == "/api/publish":
                    return {"version": store.publish(int(data["batch"]))}
                raise ValueError("Unknown operation")

            self.respond(mutate)

    root = Path(__file__).resolve().parent.parent / "web" / "dist"
    if not (root / "index.html").is_file():
        raise ValueError("Build the review UI first with npm ci --prefix web && npm run build --prefix web")
    server = ThreadingHTTPServer(("127.0.0.1", port), partial(Handler, directory=str(root)))
    print(f"Review UI at http://127.0.0.1:{port}", flush=True)
    with server:
        server.serve_forever()
