import mm_test_env  # noqa: F401 (first: the tests never touch the real settings and keys)
import http.client
import http.server
import json
import os
import re
import tempfile
import threading
import unittest
from unittest import mock

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

import motorcycle_man as app

QT = QApplication.instance() or QApplication([])
TOKEN = "menu-token"


def block(page: str, start: str, end: str) -> str:
    at = page.index(start)
    return page[at:page.index(end, at)]


class MenuTests(unittest.TestCase):
    def setUp(self):
        self.page = app.phone_page()
        self.menu = block(self.page, 'id="menuModal"', 'id="prefsModal"')
        self.prefs = block(self.page, 'id="prefsModal"', 'id="remoteModal"')

    def test_the_menu_is_in_sections(self):
        titles = re.findall(r'<div class="menu-part">([A-Z ]+)</div>', self.menu)
        self.assertEqual(titles, ["MOST USED", "USE THE PC", "FILES AND MUSIC", "YOUR DAY", "THE PC", "HELP AND SETTINGS"])
        tiles = re.findall(r'onclick="fromMenu\((\w+)\)"><svg', self.menu)
        self.assertEqual(tiles, ["openScreen", "openPad", "openRemote", "openWindows", "openSlides", "openGame",
                                 "openSend", "openFiles", "openFind", "openMusic",
                                 "openReminders", "openRoutines", "openLists", "openBrief", "openFocus",
                                 "openHealth", "openPower", "openSettings", "openScreenTime", "openHistory",
                                 "openGuide", "openPrefs"])
        self.assertIn("<span>Quick settings</span>", self.menu)
        self.assertIn('aria-label="Settings"', self.menu)  # the gear at the top

    def test_switches_and_setup_are_in_settings_not_the_menu(self):
        for thing in ("toggleTalkBack", "toggleWatch", "toggleNotices", "smsSettings", "choosePc", "openPaint",
                      "openSiriGuide", "toggleFullscreen"):
            self.assertNotIn(thing, self.menu)
            self.assertIn(thing, self.prefs)
        for part in ("THIS PHONE", "ON THE PC", "LOOK", "CONNECTION", "HELP"):
            self.assertIn(f'<div class="send-part">{part}</div>', self.prefs)

    def test_every_pc_setting_has_a_switch(self):
        self.assertEqual(sorted(re.findall(r'data-pref="(\w+)"', self.prefs)), sorted(app.PHONE_PREFS))


class PrefsRouteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        for patcher in (mock.patch.dict(os.environ, {"APPDATA": self.temp.name}),
                        mock.patch.object(app, "PHONE_SESSION_TOKEN", TOKEN)):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.bridge = mock.Mock()
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), app.make_phone_handler(self.bridge, 2560, 1600))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.port = server.server_address[1]

    def get(self, query: str, token: str = TOKEN):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        connection.request("GET", f"/prefs?token={token}{query}")
        reply = connection.getresponse()
        data = reply.read()
        connection.close()
        return reply.status, data

    def test_the_pc_settings_as_they_stand(self):
        status, data = self.get("")
        self.assertEqual(status, 200)
        answer = json.loads(data)
        self.assertEqual(answer["prefs"], app.PHONE_PREFS)  # the defaults, on a new PC
        self.assertTrue(answer["pc"])
        self.bridge.prefs_sig.emit.assert_not_called()

    def test_a_switch_from_the_phone(self):
        status, data = self.get("&set=quiet&on=1")
        self.assertTrue(json.loads(data)["prefs"]["quiet"])
        self.assertTrue(app.load_settings()["quiet"])
        self.bridge.prefs_sig.emit.assert_called_once_with("quiet", True)
        self.get("&set=engine_sounds&on=0")
        self.assertFalse(app.load_settings()["engine_sounds"])

    def test_only_its_own_settings(self):
        self.get("&set=ai_provider&on=1")
        self.get("&set=tour_seen&on=0")
        self.assertNotIn("ai_provider", app.load_settings())
        self.bridge.prefs_sig.emit.assert_not_called()
        status, data = self.get("&set=quiet&on=1", token="wrong")
        self.assertEqual(status, 403)
        self.assertNotIn("quiet", app.load_settings())


class WindowFollowsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        patcher = mock.patch.dict(os.environ, {"APPDATA": self.temp.name})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_the_window_switch_follows_the_phone(self):
        window = app.ControlCenterWindow("127.0.0.1", app.Bridge(), lambda text: None, token="test-token")
        window.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
        self.addCleanup(window.deleteLater)
        self.assertEqual(set(window.toggles), {"engine_sounds", "voice_replies", "wake_word", "start_with_windows", "quiet"})
        app.save_setting("quiet", True)  # what /prefs does first
        window.pref_changed("quiet", True)
        self.assertTrue(window.toggles["quiet"][0].isChecked())
        with mock.patch.object(app, "ENGINE") as engine:  # no real engine sound in a test
            window.toggles["engine_sounds"][0].setChecked(False)  # off in the window
            app.save_setting("engine_sounds", True)  # then on from the phone
            window.pref_changed("engine_sounds", True)
        engine.play.assert_called_once_with("blip")  # what turning it on in the window does too
        self.assertTrue(app.load_settings()["engine_sounds"])
        window.pref_changed("brief_morning", False)  # not a switch in the window: nothing to follow


if __name__ == "__main__":
    unittest.main()
