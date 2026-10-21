import mm_test_env  # noqa: F401 (first: the tests never touch the real settings and keys)
import io
import json
import os
import sys
import threading
import time
import unittest
from unittest import mock

from PIL import Image
from PySide6.QtWidgets import QApplication

import motorcycle_man as app
import pc_windows
from test_top_picks import PhoneServer

QT = QApplication.instance() or QApplication([])

NOTES = {"hwnd": 4242, "title": "Notes.txt - Notepad", "path": r"C:\Windows\notepad.exe", "minimized": False, "front": True}
SONG = {"hwnd": 777, "title": "Song - Spotify", "path": r"C:\Apps\Spotify.exe", "minimized": True, "front": False}


class WindowListTests(unittest.TestCase):
    def test_this_pcs_windows_and_an_icon(self):
        for window in pc_windows.taskbar_windows(os.getpid()):
            self.assertEqual(set(window), {"hwnd", "title", "path", "minimized", "front"})
            self.assertTrue(window["title"])
        icon = Image.open(io.BytesIO(pc_windows.icon_png(sys.executable)))
        self.assertEqual((icon.format, icon.size, icon.mode), ("PNG", (64, 64), "RGBA"))
        self.assertIsNone(pc_windows.icon_png(""))


class WindowsPanelTests(PhoneServer):
    def setUp(self):
        super().setUp()
        for patcher in (mock.patch.object(app.pc_windows, "taskbar_windows", return_value=[NOTES, SONG]),
                        mock.patch.object(app, "bring_to_front"), mock.patch.object(app.pc_windows, "minimize"),
                        mock.patch.object(app.pc_windows, "maximize"), mock.patch.object(app.pc_windows, "ask_to_close"),
                        mock.patch.object(app.time, "sleep")):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_the_list(self):
        answer = self.get_json("/windows")
        notes, song = answer["windows"]
        self.assertEqual((notes["id"], notes["title"], notes["app"], notes["front"]), ("4242", "Notes.txt - Notepad", "Notepad", True))
        self.assertEqual((song["app"], song["minimized"]), ("Spotify", True))
        self.assertNotIn("path", notes)  # the phone gets a key for the icon, not where the program is
        with mock.patch.object(app.pc_windows, "icon_png", return_value=b"\x89PNG icon") as icon:
            self.assertEqual(self.get(f"/window_icon?k={notes['icon']}"), (200, b"\x89PNG icon"))
            self.get(f"/window_icon?k={notes['icon']}")
        icon.assert_called_once_with(NOTES["path"])  # made once
        self.assertEqual(self.get("/window_icon?k=guessed")[0], 404)
        self.assertEqual(self.get("/windows", token=False)[0], 403)
        for needed in ('id="windowsModal"', "fromMenu(openWindows)", "function showWindows"):
            self.assertIn(needed, app.PHONE_PAGE)

    def test_switch_minimize_maximize_close(self):
        self.assertEqual(self.get_json("/windows?do=front&id=777")["say"], "Switched to Song - Spotify.")
        app.bring_to_front.assert_called_once_with(777)
        self.assertEqual(self.get_json("/windows?do=min&id=4242")["say"], "Minimized Notes.txt - Notepad.")
        app.pc_windows.minimize.assert_called_once_with(4242)
        self.get_json("/windows?do=max&id=4242")
        app.pc_windows.maximize.assert_called_once_with(4242)
        self.assertEqual(self.get_json("/windows?do=close&id=4242")["say"], "Asked Notes.txt - Notepad to close.")
        app.pc_windows.ask_to_close.assert_called_once_with(4242)
        self.assertEqual(self.get_json("/windows?do=close&id=1")["say"], "That window isn't open any more.")
        self.assertEqual(app.pc_windows.ask_to_close.call_count, 1)


class PhoneInboxTests(PhoneServer):
    def setUp(self):
        super().setUp()
        patcher = mock.patch.object(app, "PHONE_INBOX", app.PhoneInbox())
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_the_phone_waits_for_something_to_do(self):
        self.assertFalse(app.PHONE_INBOX.reachable())
        self.assertIn("isn't listening", app.ring_reply("find my phone"))
        answers = []
        waiting = threading.Thread(target=lambda: answers.append(self.get_json("/phone_inbox?wait=5")))
        started = time.monotonic()
        waiting.start()
        deadline = time.monotonic() + 3
        while not app.PHONE_INBOX.waiting and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(app.PHONE_INBOX.reachable())  # a phone is waiting
        self.assertIn("Ringing your phone", app.ring_reply("Where's my phone?"))
        waiting.join(5)
        self.assertEqual(answers, [{"do": [{"kind": "ring"}]}])
        self.assertLess(time.monotonic() - started, 3)  # at once, not after the wait
        self.assertEqual(self.get_json("/phone_inbox?wait=0.1"), {"do": []})
        self.assertTrue(app.PHONE_INBOX.reachable())  # it asked a moment ago
        self.assertIsNone(app.ring_reply("find my phone bill"))
        self.assertEqual(self.get("/phone_inbox?wait=0", token=False)[0], 403)

    def test_an_answer_from_the_pc_and_what_became_of_it(self):
        app.PHONE_INBOX.put({"kind": "reply", "id": "n7", "text": "On my way"})
        self.assertEqual(self.get_json("/phone_inbox?wait=0"), {"do": [{"kind": "reply", "id": "n7", "text": "On my way"}]})
        self.get_json("/phone_inbox?wait=0&done=n7:1")
        self.bridge.phone_done_sig.emit.assert_called_once_with("n7", True)
        self.get_json("/phone_inbox?wait=0&done=n7;drop:1")  # nonsense is left alone
        self.assertEqual(self.bridge.phone_done_sig.emit.call_count, 1)

    def test_messages_that_can_be_answered_get_a_card(self):
        with mock.patch.object(app, "pc_takes_notices", return_value=True):
            self.assertEqual(self.post("/phone_notice", json.dumps({"app": "WhatsApp", "title": "Mom", "text": "Coming?",
                                                                    "id": "n3", "reply": True}).encode())[0], 200)
            self.post("/phone_notice", json.dumps({"app": "Clock", "title": "Alarm", "text": "7:00"}).encode())
        self.bridge.phone_notice_sig.emit.assert_called_once_with({"id": "n3", "app": "WhatsApp", "title": "Mom", "text": "Coming?"})
        self.bridge.notice_sig.emit.assert_called_once_with("📱 Clock · Alarm: 7:00", 9000)

    def test_the_reply_card(self):
        card = app.PhoneReplyCard()
        self.addCleanup(card.deleteLater)
        with mock.patch.object(card, "show"), mock.patch.object(card, "raise_"):
            card.show_notice({"id": "n3", "app": "WhatsApp", "title": "Mom", "text": "Coming?"})
        self.assertEqual((card.app.text(), card.title.text(), card.text.text()), ("📱 WhatsApp", "Mom", "Coming?"))
        card.field.setText("  On my way  ")
        card.send()
        self.assertEqual(app.PHONE_INBOX.take(0), [{"kind": "reply", "id": "n3", "text": "On my way"}])
        self.assertFalse(card.button.isEnabled())
        with mock.patch.object(card, "isVisible", return_value=True):
            card.done("n9", True)  # another message's
            self.assertNotIn("Sent", card.status.text())
            card.done("n3", True)
        self.assertEqual(card.status.text(), "Sent from your phone ✓")


if __name__ == "__main__":
    unittest.main()
