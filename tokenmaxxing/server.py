"""Authenticated loopback-only HTTP; static assets never contain secrets."""

from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
import secrets
import sqlite3
import sys
from urllib.parse import parse_qsl, urlsplit
import webbrowser

from . import pilot_api
from .pilot_store import Store
from .records import NormalizationError, encode, loads

UI = Path(__file__).parent / "ui"
CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; "
       "img-src 'none'; font-src 'none'; object-src 'none'; base-uri 'none'; "
       "frame-ancestors 'none'; form-action 'none'")
ASSETS = {"/": ("index.html", "text/html; charset=utf-8"),
          "/app.js": ("app.js", "text/javascript; charset=utf-8"),
          "/style.css": ("style.css", "text/css; charset=utf-8")}
for module in ("dom", "import", "history", "profile", "outcomes"):
    ASSETS["/" + module + ".js"] = (module + ".js", "text/javascript; charset=utf-8")


class Handler(BaseHTTPRequestHandler):
    server_version = "Tokenmaxxing"
    sys_version = ""

    def setup(self):
        super().setup()
        self.connection.settimeout(10)

    def log_message(self, *_args):
        pass  # No source paths, query text, credentials, or bodies in access logs.

    def send_error(self, code, message=None, explain=None):
        self.respond(code, {"error": "http_request_rejected"})

    def respond(self, status, data, mime="application/json; charset=utf-8"):
        raw = data if isinstance(data, bytes) else encode(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Content-Security-Policy", CSP)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        self.wfile.write(raw)

    def handle_request(self, write=False):
        try:
            if self.headers.get_all("Host") != [self.server.authority]:
                return self.respond(403, {"error": "host_rejected"})
            origins = self.headers.get_all("Origin", [])
            if (origins and origins != [self.server.origin]) or (write and origins != [self.server.origin]):
                return self.respond(403, {"error": "origin_rejected"})
            if self.headers.get("Sec-Fetch-Site") not in (None, "same-origin", "none"):
                return self.respond(403, {"error": "cross_site_rejected"})
            parsed = urlsplit(self.path)
            if parsed.scheme or parsed.netloc or parsed.fragment:
                raise NormalizationError("invalid_request_target")
            if parsed.path in ASSETS and not write and not parsed.query:
                name, mime = ASSETS[parsed.path]
                return self.respond(200, (UI / name).read_bytes(), mime)
            authorization = self.headers.get_all("Authorization", [])
            if len(authorization) != 1 or not secrets.compare_digest(authorization[0], "Bearer " + self.server.token):
                return self.respond(401, {"error": "authorization_required"})
            pairs = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True, max_num_fields=20)
            query = dict(pairs)
            if len(query) != len(pairs):
                raise NormalizationError("duplicate_query_fields")
            if write:
                if parsed.query:
                    raise NormalizationError("write_query_not_allowed")
                if self.headers.get_all("Content-Type") != ["application/json"]:
                    return self.respond(415, {"error": "json_content_type_required"})
                lengths = self.headers.get_all("Content-Length", [])
                if self.headers.get_all("Transfer-Encoding") or len(lengths) != 1 or not lengths[0].isascii() or not lengths[0].isdecimal():
                    raise NormalizationError("content_length_required")
                size = int(lengths[0])
                # Transport/form limit only; never a source ingestion size cap.
                if not 0 < size <= 1024 * 1024:
                    return self.respond(413, {"error": "request_body_limit_1_mib"})
                raw = self.rfile.read(size)
                if len(raw) != size:
                    raise NormalizationError("incomplete_request_body")
                data = loads(raw.decode("utf-8"))
                result = pilot_api.post(self.server.store, parsed.path, data)
            else:
                result = pilot_api.get(self.server.store, parsed.path, query)
            self.respond(200, result)
        except NormalizationError as error:
            self.respond(404 if str(error) in ("route_not_found", "session_not_found", "evidence_not_found") else 400,
                         {"error": str(error)})
        except (ValueError, UnicodeError, TypeError, KeyError, RecursionError):
            self.respond(400, {"error": "invalid_request"})
        except (OSError, sqlite3.Error, MemoryError, OverflowError):
            self.respond(500, {"error": "local_io_or_store_error"})
        except Exception:
            self.respond(500, {"error": "internal_error"})

    def do_GET(self):
        self.handle_request()

    def do_POST(self):
        self.handle_request(True)


class PilotServer(HTTPServer):
    def __init__(self, store, port=0):
        if type(port) is not int or not 0 <= port <= 65535:
            raise NormalizationError("invalid_loopback_port")
        self.store = store
        self.token = secrets.token_urlsafe(32)
        super().__init__(("127.0.0.1", port), Handler)
        self.authority = "127.0.0.1:" + str(self.server_port)
        self.origin = "http://" + self.authority

    def handle_error(self, *_args):
        pass  # Never dump request data or tracebacks into launcher logs.


def serve(root, allow_source=(), allow_artifact=(), port=8765, open_browser=False):
    store = Store(root, allow_source, allow_artifact)
    server = None
    try:
        server = PilotServer(store, port)
        url = server.origin + "/#token=" + server.token
        print(encode({"origin": server.origin, "status": "ready", "store_version": 1}), flush=True)
        if open_browser:
            if not webbrowser.open(url):
                print("Browser did not open. Restart in an interactive terminal to see the private launch URL.", file=sys.stderr)
        elif sys.stdout.isatty():
            print("Private launch URL (do not share): " + url, flush=True)
        else:
            print("Use --open-browser or an interactive terminal for private authorization.", file=sys.stderr)
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        if server is not None:
            server.server_close()
        store.close()
