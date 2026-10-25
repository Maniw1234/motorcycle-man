import mm_test_env  # noqa: F401 (first: the tests never touch the real settings and keys)
import http.server
import io
import os
import socket
import socketserver
import tempfile
import threading
import unittest
import urllib.request
from unittest import mock

import motorcycle_man as app

PAGE = ("A long and ordinary public web page about motorcycles. " * 10).encode()
ROUTER = ("ROUTER ADMIN PAGE: the Wi-Fi password and every device at home. " * 10).encode()


def serve(host: str, routes: dict):
    # A small web server on host; routes: {path: (status, body or redirect address)}. Returns (port, hits, stop).
    hits = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            hits.append(self.path)
            status, what = routes.get(self.path, (404, b""))
            self.send_response(status)
            if status in (301, 302, 307):
                self.send_header("Location", what)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(what)))
            self.end_headers()
            self.wfile.write(what)

    class Server(http.server.ThreadingHTTPServer):
        def server_bind(self):  # without looking up the address's name, which takes 5 s for 127.0.0.2
            socketserver.TCPServer.server_bind(self)
            self.server_name, self.server_port = self.server_address[:2]

    server = Server((host, 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server.server_address[1], hits, lambda: (server.shutdown(), server.server_close())


class ResearchStaysOnTheInternetTests(unittest.TestCase):
    # 127.0.0.2 stands in for the internet and 127.0.0.1 for the home network (a router's page).
    def setUp(self):
        real = app.global_address
        for patcher in (mock.patch.object(app, "global_address", lambda a: str(a) == "127.0.0.2" or real(a)),
                        mock.patch.object(urllib.request, "getproxies", lambda: {})):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.router_port, self.router_hits, stop = serve("127.0.0.1", {"/": (200, ROUTER)})
        self.addCleanup(stop)
        self.port, self.hits, stop = serve("127.0.0.2", {
            "/page": (200, PAGE),
            "/hop": (302, "/page"),
            "/trap": (302, f"http://127.0.0.1:{self.router_port}/"),
            "/trap-by-name": (302, f"http://localhost:{self.router_port}/"),
        })
        self.addCleanup(stop)

    def url(self, path):
        return f"http://127.0.0.2:{self.port}{path}"

    def test_ordinary_pages_and_redirects_still_work(self):
        self.assertIn("motorcycles", app.read_web_page(self.url("/page")))
        self.assertIn("motorcycles", app.read_web_page(self.url("/hop")))  # public to public is fine

    def test_a_redirect_into_the_home_network_is_refused(self):
        # The deep test's attack: a public link that bounces to the router's page.
        for path in ("/trap", "/trap-by-name"):
            self.assertEqual(app.read_web_page(self.url(path)), "", path)
        self.assertEqual(self.router_hits, [])  # the router was never even asked
        self.assertEqual(app.read_web_pages([self.url("/trap"), self.url("/page")]).keys(), {self.url("/page")})

    def test_a_name_that_changes_to_a_home_address_is_refused(self):
        # DNS rebinding: public when checked, the router a moment later.
        real, answers = socket.getaddrinfo, iter(["127.0.0.2", "127.0.0.1", "127.0.0.1"])

        def resolve(host, port, *args, **kwargs):
            if host == "rebind.mm-test":
                return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (next(answers), port))]
            return real(host, port, *args, **kwargs)

        with mock.patch.object(socket, "getaddrinfo", resolve):
            self.assertEqual(app.read_web_page(f"http://rebind.mm-test:{self.router_port}/"), "")
        self.assertEqual(self.router_hits, [])  # connected, saw it was home, and left before asking anything

    def test_private_addresses_are_still_refused_outright(self):
        for url in (f"http://127.0.0.1:{self.router_port}/", "http://192.168.1.1/", "http://10.0.0.1/", "file:///C:/x"):
            self.assertEqual(app.read_web_page(url), "", url)
        self.assertEqual(self.router_hits, [])

    def test_through_a_proxy_the_names_are_still_checked(self):
        with mock.patch.object(urllib.request, "getproxies", lambda: {"http": "http://127.0.0.1:9"}):
            handlers = {type(h) for h in app.public_web_opener().handlers}
        self.assertIn(app.PublicRedirects, handlers)
        self.assertNotIn(app.PublicHTTPHandler, handlers)  # the proxy is what's reached, so the address isn't checked
        handlers = {type(h) for h in app.public_web_opener().handlers}
        self.assertTrue({app.PublicRedirects, app.PublicHTTPHandler, app.PublicHTTPSHandler} <= handlers)


class Racing(io.RawIOBase):
    # The phone's upload, which waits at the start until every other upload has started too.
    def __init__(self, data: bytes, everyone: threading.Barrier):
        self.data, self.everyone, self.started = io.BytesIO(data), everyone, False

    def read(self, size=-1):
        if not self.started:
            self.started = True
            self.everyone.wait(10)
        return self.data.read(size)


class SameNameUploadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = self.temp.name  # exists, so nothing goes to the real Downloads

    def test_32_uploads_of_the_same_name_at_once(self):
        # The deep test's race: 32 at the same instant; 2 used to fail with a 500.
        everyone, saved, failed = threading.Barrier(32), [], []

        def upload(i):
            body = f"upload number {i} ".encode() * 50
            try:
                saved.append((app.receive_phone_file(Racing(body, everyone), len(body), "photo.jpg", self.folder), body))
            except Exception as exc:  # noqa: BLE001
                failed.append(exc)

        workers = [threading.Thread(target=upload, args=(i,)) for i in range(32)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(30)
        self.assertEqual(failed, [])
        self.assertEqual(len({path for path, _ in saved}), 32)  # 32 files, 32 names
        for path, body in saved:
            with open(path, "rb") as f:
                self.assertEqual(f.read(), body)  # each one whole and its own
        names = sorted(os.listdir(self.folder))
        self.assertEqual(len(names), 32)
        self.assertIn("photo.jpg", names)
        self.assertIn("photo (31).jpg", names)
        self.assertFalse([n for n in names if n.endswith(".receiving")])

    def test_a_taken_name_is_never_overwritten(self):
        for name, text in (("notes.txt", b"mine"), ("notes (1).txt", b"also mine")):
            with open(os.path.join(self.folder, name), "wb") as f:
                f.write(text)
        source = os.path.join(self.folder, "arriving")
        with open(source, "wb") as f:
            f.write(b"new")
        path = app.move_to_unused_name(source, self.folder, "notes.txt")
        self.assertEqual(os.path.basename(path), "notes (2).txt")
        with open(os.path.join(self.folder, "notes.txt"), "rb") as f:
            self.assertEqual(f.read(), b"mine")
        self.assertFalse(os.path.exists(source))

    def test_a_cut_short_upload_leaves_nothing(self):
        with self.assertRaises(ConnectionError):
            app.receive_phone_file(io.BytesIO(b"half"), 100, "big.zip", self.folder)
        self.assertEqual(os.listdir(self.folder), [])


if __name__ == "__main__":
    unittest.main()
