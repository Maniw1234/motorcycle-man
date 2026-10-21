import mm_test_env  # noqa: F401 (first: the tests never touch the real settings and keys)
import http.client
import threading
import time
import unittest
from unittest import mock

from PIL import Image

import motorcycle_man as app
import pc_capture


class ScreenSourceTests(unittest.TestCase):
    def test_duplication_when_windows_offers_it(self):
        frames = iter([True, False])
        picture = Image.new("RGB", (8, 8), (1, 2, 3))
        duplication = mock.Mock(latest=picture, **{"wait.side_effect": lambda ms: next(frames)})
        source = pc_capture.ScreenSource(duplication=lambda: duplication)
        self.assertTrue(source.wait_for_change(0.1))
        self.assertIs(source.latest, picture)
        self.assertEqual(source.method, "duplication")
        self.assertFalse(source.wait_for_change(0.1))  # nothing new: no picture made

    def test_the_plain_way_when_it_isnt(self):
        shots = [Image.new("RGB", (64, 64), (0, 0, 0)), Image.new("RGB", (64, 64), (0, 0, 0)), Image.new("RGB", (64, 64), (9, 9, 9))]
        source = pc_capture.ScreenSource(duplication=mock.Mock(side_effect=OSError("no duplication")), grab=lambda: shots.pop(0))
        self.assertTrue(source.wait_for_change(0.01))  # the first picture
        self.assertFalse(source.wait_for_change(0.01))  # the same screen
        self.assertTrue(source.wait_for_change(0.01))  # it changed
        self.assertEqual(source.method, "grab")

    def test_lost_duplication_is_opened_again(self):
        broken = mock.Mock(**{"wait.side_effect": OSError("access lost")})
        working = mock.Mock(latest=Image.new("RGB", (4, 4)), **{"wait.return_value": True})
        made = iter([broken, working])
        source = pc_capture.ScreenSource(duplication=lambda: next(made))
        self.assertFalse(source.wait_for_change(0.01))  # lost (the lock screen, a new resolution): let go
        broken.close.assert_called_once_with()
        time.sleep(0.55)
        self.assertTrue(source.wait_for_change(0.01))  # and opened again a moment later

    def test_a_real_picture_of_this_screen(self):
        source = pc_capture.ScreenSource()
        self.addCleanup(source.close)
        deadline = time.monotonic() + 5
        while source.latest is None and time.monotonic() < deadline:
            source.wait_for_change(0.2)
        self.assertIsNotNone(source.latest)  # reads the screen only; nothing is kept or shown
        self.assertEqual(source.latest.mode, "RGB")
        self.assertIsNotNone(source.latest.getbbox())  # never the black first copy


class ScreenFeedTests(unittest.TestCase):
    def test_another_part_is_made_at_once_from_the_last_picture(self):
        made = []

        class Screen:
            latest = Image.new("RGB", (100, 50))

            def wait_for_change(self, seconds):
                time.sleep(min(seconds, 0.05))
                return False  # the screen doesn't change

            def close(self):
                pass

        def picture(region, width, quality, shot=None):
            made.append((region, width, quality))
            return repr((region, width)).encode(), {"region": region, "screen": (100, 50)}
        feed = app.ScreenFeed(Screen())
        with mock.patch.object(app, "screen_picture", side_effect=picture):
            whole, fresh = feed.ask((0, 0, 1, 1), 900, 60, "", "", lambda: "0,0", 2)
            self.assertTrue(fresh)
            started = time.monotonic()
            close_up, fresh = feed.ask((0.5, 0.5, 1, 1), 1280, 60, whole["tag"], "0,0", lambda: "0,0", 2)
            self.assertTrue(fresh)
            self.assertLess(time.monotonic() - started, 0.2)  # zoomed in: no waiting for the screen to change
        self.assertEqual([m[0] for m in made], [(0, 0, 1, 1), (0.5, 0.5, 1, 1)])


class OpenConnectionTests(unittest.TestCase):
    TOKEN = "smoother-test-token"

    def setUp(self):
        patcher = mock.patch.object(app, "PHONE_SESSION_TOKEN", self.TOKEN)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.server = app.PhoneServer(("127.0.0.1", 0), app.make_phone_handler(mock.Mock(), 2560, 1600))
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def test_answers_with_a_length_keep_the_connection(self):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=5)
        self.addCleanup(connection.close)
        for _ in range(3):  # three status checks over one connection
            connection.request("GET", f"/status?token={self.TOKEN}")
            reply = connection.getresponse()
            self.assertEqual(reply.status, 200)
            self.assertIsNone(reply.getheader("Connection"))
            reply.read()
        first_socket = connection.sock
        connection.request("GET", f"/history?token={self.TOKEN}")
        connection.getresponse().read()
        self.assertIs(connection.sock, first_socket)  # still the same connection

    def test_answers_without_a_length_close(self):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=5)
        self.addCleanup(connection.close)
        connection.request("GET", "/status")  # no token: the page for connecting, which says no length
        reply = connection.getresponse()
        self.assertEqual((reply.status, reply.getheader("Connection")), (403, "close"))
        reply.read()
        connection.request("POST", f"/clipboard?token={self.TOKEN}", body=b'{"text": "x"}',
                           headers={"Content-Type": "application/json"})
        reply = connection.getresponse()
        self.assertEqual(reply.getheader("Connection"), "close")  # a POST always closes
        reply.read()


class CrashLogTests(unittest.TestCase):
    def test_the_crash_log_stays_small(self):
        import os
        import tempfile
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "crash.log")
            with open(path, "w", encoding="utf-8") as f:
                f.write("old line\n" * 60000)  # about 540 KB of Windows' harmless COM notes
            app.trim_crash_log(path)
            self.assertLess(os.path.getsize(path), app.CRASH_LOG_KEEP + 1000)
            with open(path, encoding="utf-8") as f:
                self.assertTrue(f.read().endswith("old line\n"))  # the newest kept


if __name__ == "__main__":
    unittest.main()
