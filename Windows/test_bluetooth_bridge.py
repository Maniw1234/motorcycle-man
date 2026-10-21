import mm_test_env  # noqa: F401 (first: the tests never touch the real settings and keys)
import http.server
import socket
import struct
import threading
import unittest
from unittest import mock

import bluetooth_bridge as bt
import motorcycle_man as app


def frame(kind, stream, payload=b""):
    return bt.HEADER.pack(kind, stream, len(payload)) + payload


def read_frame(phone):
    kind, stream, length = bt.HEADER.unpack(bt.read_exactly(phone, bt.HEADER.size))
    return kind, stream, bt.read_exactly(phone, length) if length else b""


class BluetoothBridgeTests(unittest.TestCase):
    def setUp(self):
        self.seen = []
        test = self

        class Echo(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                test.seen.append(self.client_address[0])
                body = f"page {self.path}".encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Echo)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def answer(self, phone, stream):
        # Every DATA frame of a stream until its CLOSE.
        data = b""
        while True:
            kind, number, payload = read_frame(phone)
            self.assertEqual(number, stream)
            if kind == bt.CLOSE:
                return data
            data += payload

    def test_two_requests_at_once_over_one_link_from_127_0_0_2(self):
        phone, pc = socket.socketpair()
        self.addCleanup(phone.close)
        threading.Thread(target=bt.Mux(pc, self.server.server_address[1]).run, daemon=True).start()
        phone.sendall(frame(bt.OPEN, 1) + frame(bt.OPEN, 2))
        phone.sendall(frame(bt.DATA, 2, b"GET /two HTTP/1.0\r\n\r\n") + frame(bt.DATA, 1, b"GET /one HTTP/1.0\r\n\r\n"))
        answers = {}
        while len(answers) < 2 or any(not a.endswith(b"page /one") and not a.endswith(b"page /two") for a in answers.values()):
            kind, stream, payload = read_frame(phone)
            if kind == bt.DATA:
                answers[stream] = answers.get(stream, b"") + payload
        self.assertTrue(answers[1].startswith(b"HTTP/1.0 200") and answers[1].endswith(b"page /one"))
        self.assertTrue(answers[2].endswith(b"page /two"))
        self.assertEqual(set(self.seen), {bt.SOURCE})  # the web server sees Bluetooth's own address

    def test_a_stream_to_a_closed_server_is_closed(self):
        phone, pc = socket.socketpair()
        self.addCleanup(phone.close)
        refused = mock.Mock(side_effect=ConnectionRefusedError())
        threading.Thread(target=bt.Mux(pc, 1, connect=refused).run, daemon=True).start()
        phone.sendall(frame(bt.OPEN, 7))
        self.assertEqual(read_frame(phone), (bt.CLOSE, 7, b""))

    def test_bluetooth_never_counts_as_this_pc(self):
        headers = {"Host": "127.0.0.1:8080"}
        with mock.patch.object(app, "connection_owner", return_value=1234), mock.patch.object(app, "same_windows_session", return_value=True):
            self.assertTrue(app.request_from_this_pc("127.0.0.1", 5000, headers))  # an emulator on this PC
            self.assertFalse(app.request_from_this_pc(bt.SOURCE, 5000, headers))  # a phone over Bluetooth

    def test_no_bluetooth_is_said(self):
        states = []
        bridge = bt.BluetoothBridge(8080, lambda state, detail: states.append((state, detail)))
        with mock.patch.object(bt.socket, "socket", side_effect=OSError("no adapter")):
            bridge.run()
        self.assertEqual(states, [("failed", "This PC's Bluetooth is off or missing.")])


if __name__ == "__main__":
    unittest.main()
