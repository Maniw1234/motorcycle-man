import mm_test_env  # noqa: F401 (first: the tests never touch the real settings and keys)
import http.client
import http.server
import os
import tempfile
import threading
import time
import unittest
from unittest import mock

from PySide6.QtWidgets import QApplication, QMenu

import motorcycle_man as app

QT = QApplication.instance() or QApplication([])


class StickerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        for patcher in (mock.patch.dict(os.environ, {"APPDATA": self.temp.name}), mock.patch.dict(app.STICKER_RUNS, clear=True)):
            patcher.start()
            self.addCleanup(patcher.stop)
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), app.make_phone_handler(mock.Mock(), 1000, 800))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.port = server.server_address[1]

    def scan(self, secret):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        connection.request("GET", f"/do?s={secret}")  # no link secret: a sticker carries its own
        reply = connection.getresponse()
        page = reply.read().decode("utf-8")
        connection.close()
        return reply.status, page

    def test_a_sticker_runs_only_its_routine_and_can_be_taken_back(self):
        routine = app.ROUTINES.save("Good morning", ["open gmail"])
        secret = app.make_sticker(routine)
        self.assertGreaterEqual(len(secret), 16)
        with mock.patch.object(app, "run_routine") as run:
            status, page = self.scan(secret)
            self.assertEqual(status, 200)
            self.assertIn("Good morning", page)
            deadline = time.monotonic() + 3
            while not run.called and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertEqual(run.call_args.args[0]["id"], routine["id"])
            self.assertEqual(self.scan(secret)[0], 429)  # a second scan right away doesn't run it twice
            self.assertEqual(self.scan("guessed-secret")[0], 404)
            app.drop_sticker(secret)
            app.STICKER_RUNS.clear()
            self.assertEqual(self.scan(secret)[0], 404)
            gone = app.make_sticker(routine)
            app.ROUTINES.delete(routine["id"])
            self.assertEqual(self.scan(gone)[0], 410)
        self.assertEqual(run.call_count, 1)

    def test_a_sticker_s_link_and_picture(self):
        self.assertEqual(app.sticker_link("abc", "http://192.168.1.20:8080"), "http://192.168.1.20:8080/do?s=abc")
        app.save_setting("ngrok_domain", "bike-example.ngrok-free.app")
        app.save_key("ngrok_authtoken", "x" * 30)
        self.assertEqual(app.sticker_link("abc", "http://192.168.1.20:8080"), "https://bike-example.ngrok-free.app/do?s=abc")
        picture = app.sticker_picture("https://bike-example.ngrok-free.app/do?s=abc", "Good morning")
        self.assertEqual((picture.width(), picture.height()), (600, 780))
        self.assertEqual(picture.pixelColor(300, 400).name() in ("#000000", "#ffffff", "#0b1026"), True)  # the QR code is there

    def test_the_tray_menu(self):
        app.ROUTINES.save("Good morning", ["open gmail"])
        app.REMINDERS.add("Stretch", time.time() + 600)
        menu, bridge = QMenu(), mock.Mock()
        app.fill_tray_menu(menu, mock.Mock(), bridge, 1000, 800)
        titles = [action.text() for action in menu.actions() if not action.isSeparator()]
        self.assertEqual(titles, ["Open Motorcycle Man", "Guide: everything I can do", "Talk  (Ctrl+Alt+M)", "Routines", "Reminders", "Paint job",
                                  "Find my phone", "Guard my PC while I'm away", "Quiet", "Stop what it's doing",
                                  "Quit Motorcycle Man"])
        with mock.patch.object(app, "ring_phone", return_value="Ringing your phone.") as ring:
            next(a for a in menu.actions() if a.text() == "Find my phone").trigger()
        ring.assert_called_once_with()
        bridge.say_sig.emit.assert_called_once_with("Ringing your phone.", 8000)
        submenu = {a.text(): a.menu() for a in menu.actions() if a.menu()}
        self.assertEqual([a.text() for a in submenu["Routines"].actions()], ["Good morning"])
        self.assertTrue(submenu["Reminders"].actions()[0].text().endswith(": Stretch"))
        self.assertEqual(len(submenu["Paint job"].actions()), len(app.paint_jobs.PAINTS))


if __name__ == "__main__":
    unittest.main()
