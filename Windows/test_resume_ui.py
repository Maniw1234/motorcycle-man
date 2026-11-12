import mm_test_env  # noqa: F401 (first: the tests never touch the real settings and keys)
import http.client
import http.server
import json
import os
import re
import tempfile
import threading
import time
import unittest
from unittest import mock

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QMenu

import motorcycle_man as app

QT = QApplication.instance() or QApplication([])
TOKEN = "resume-ui-test-token"
WORDS = {"stop": "You said stop.", "failed": "A step didn't work.", "waiting_yes": "Waiting for your yes to send."}


class FakeJournal:
    # TaskJournal's offer/for_ui as the design gives them; nothing on disk.
    def __init__(self, *entries):
        self.entries = list(entries)

    def offer(self):
        return dict(self.entries[-1]) if self.entries else None

    def for_ui(self, entry, send_waiting=False):
        held = send_waiting and entry["why"] == "waiting_yes"
        return {"id": entry["id"], "state": entry["state"], "why": entry["why"], "words": WORDS[entry["why"]],
                "step": entry["step"], "request": entry["request"][:80], "ask": "Yes, send it" if held else "Resume task",
                "title": "the journal's own line, which stays on the PC"}


def entry(task_id="a1b2c3d4", state="paused", why="stop", step=3, request="send Dana the photos"):
    return {"id": task_id, "state": state, "why": why, "step": step, "request": request}


class TempAppData(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.journal = FakeJournal(entry())
        for patcher in (mock.patch.dict(os.environ, {"APPDATA": self.temp.name}),
                        mock.patch.object(app, "TASKS", self.journal),
                        mock.patch.dict(app.PENDING_SEND, {"request": "", "time": 0.0, "task": ""})):
            patcher.start()
            self.addCleanup(patcher.stop)


class PhonePageResumeTests(unittest.TestCase):
    def test_the_phone_page_offers_to_pick_up_a_paused_task(self):
        page = app.ANDROID_WEB_CLIENT
        for piece in ('.chip[data-state="paused"]', '.chip[data-state="stuck"]', '<div id="resumeBar"',
                      'onclick="resumeTask()">Resume task</button>', 'onclick="forgetPaused()">Forget it</button>',
                      "fetch(requestUrl('/resume', {id: task.id}))", "requestUrl('/forget_task', {id: task.id})",
                      "showPaused(state.task || null);", "forgottenIds.add(task.id);", "quietSince = 0;"):
            self.assertIn(piece, page)
        bar = re.search(r"#resumeBar \{[^}]*\}", page).group(0)
        self.assertIn("margin: 0 auto", bar)  # not translateX(-50%): @keyframes pop sets transform
        self.assertNotIn("translateX", bar)
        self.assertEqual(page.count("innerHTML"), 1)  # its words go in with textContent
        self.assertIn("/resume", app.PHONE_REQUESTS)
        self.assertIn("/forget_task", app.PHONE_REQUESTS)


class ResumeOfferTests(TempAppData):
    def test_offered_only_between_tasks(self):
        self.assertEqual(app.resume_offer_for_ui()["id"], "a1b2c3d4")
        app.TASK_LOCK.acquire()
        try:
            self.assertIsNone(app.resume_offer_for_ui())
        finally:
            app.TASK_LOCK.release()
        self.journal.entries.clear()
        self.assertIsNone(app.resume_offer_for_ui())

    def test_yes_send_it_only_while_the_held_send_waits(self):
        self.journal.entries[:] = [entry(why="waiting_yes")]
        self.assertEqual(app.resume_offer_for_ui()["ask"], "Resume task")  # nothing held (as after a restart)
        app.PENDING_SEND.update(request="send Dana the photos", time=time.time(), task="e5f6a7b8")
        self.assertEqual(app.resume_offer_for_ui()["ask"], "Resume task")  # another task's held send
        app.PENDING_SEND.update(task="a1b2c3d4")
        self.assertEqual(app.resume_offer_for_ui()["ask"], "Yes, send it")
        app.PENDING_SEND.update(time=time.time() - app.SEND_HOLD_SECONDS - 1)
        self.assertEqual(app.resume_offer_for_ui()["ask"], "Resume task")

    def test_a_broken_journal_never_breaks_the_status_check(self):
        broken = mock.Mock()
        broken.offer.side_effect = RuntimeError("bad")
        with mock.patch.object(app, "TASKS", broken), mock.patch.dict(app.RESUME_OFFER_TROUBLE, {"logged": False}), \
                self.assertLogs("motorcycle_man", "WARNING") as logs:
            self.assertIsNone(app.resume_offer_for_ui())
            self.assertIsNone(app.resume_offer_for_ui())
            self.assertIsNone(app.phone_status()["task"])
        self.assertEqual(len(logs.records), 1)  # once, not with every check

    def test_the_line_the_window_shows(self):
        self.assertEqual(app.paused_line(app.resume_offer_for_ui()), "Paused at step 3: “send Dana the photos”")
        self.assertEqual(app.paused_line({"state": "stuck", "step": 0, "request": "x"}), "Stuck: “x”")

    def test_the_real_journals_words_go_out_as_the_wire_has_them(self):
        journal = app.task_journal.TaskJournal(lambda: os.path.join(self.temp.name, "tasks.json"))
        journal.pause(journal.start("send Dana the photos"), "paused", "stop")
        with mock.patch.object(app, "TASKS", journal):
            task = app.resume_offer_for_ui()
        self.assertEqual(set(task), {"id", "state", "why", "words", "step", "request", "ask"})
        self.assertEqual((task["request"], task["words"], task["ask"]),
                         ("send Dana the photos", "You said stop.", "Resume task"))


class ResumeRouteTests(TempAppData):
    def setUp(self):
        super().setUp()
        for patcher in (mock.patch.object(app, "PHONE_SESSION_TOKEN", TOKEN),
                        mock.patch.object(app, "resume_task", return_value="Done!"),
                        mock.patch.object(app, "forget_task", return_value="Okay, forgotten.")):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.bridge = mock.Mock()
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), app.make_phone_handler(self.bridge, 1000, 800))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.port = server.server_address[1]

    def get(self, path: str, token: str = TOKEN):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        connection.request("GET", f"{path}{'&' if '?' in path else '?'}token={token}")
        reply = connection.getresponse()
        data = reply.read()
        connection.close()
        return reply.status, data

    def test_status_carries_the_paused_task(self):
        status, data = self.get("/status")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(data)["task"], {"id": "a1b2c3d4", "state": "paused", "why": "stop",
                                                    "words": "You said stop.", "step": 3,
                                                    "request": "send Dana the photos", "ask": "Resume task"})
        self.journal.entries.clear()
        self.assertIsNone(json.loads(self.get("/status")[1])["task"])

    def test_resume_and_forget_from_the_phone(self):
        status, data = self.get("/resume?id=a1b2c3d4")
        self.assertEqual((status, json.loads(data)), (200, {"reply": "Done!"}))
        app.resume_task.assert_called_once_with(self.bridge, 1000, 800, task_id="a1b2c3d4", source="phone")
        status, data = self.get("/forget_task?id=a1b2c3d4")
        self.assertEqual(json.loads(data), {"ok": True, "reply": "Okay, forgotten."})
        app.forget_task.assert_called_once_with("a1b2c3d4")
        self.bridge.status_sig.emit.assert_called_with("Status: Ready!")  # the window looks again

    def test_only_with_the_links_secret(self):
        self.assertEqual(self.get("/resume?id=a1b2c3d4", token="wrong")[0], 403)
        self.assertEqual(self.get("/forget_task?id=a1b2c3d4", token="wrong")[0], 403)
        app.resume_task.assert_not_called()
        app.forget_task.assert_not_called()


class WindowResumeTests(TempAppData):
    def window(self):
        self.resume_cmd = mock.Mock()
        window = app.ControlCenterWindow("127.0.0.1", app.Bridge(), lambda text: None, token="test-token",
                                         resume_cmd=self.resume_cmd)
        window.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
        window.tray = mock.Mock()
        self.addCleanup(window.deleteLater)
        return window

    def test_paused_between_tasks_with_a_resume_button(self):
        window = self.window()
        self.assertEqual(window.pill.text(), "● Paused")
        self.assertEqual(window.status_lbl.text(), "Paused at step 3: “send Dana the photos”  ·  You said stop.")
        self.assertEqual(window.status_lbl.textFormat(), Qt.TextFormat.PlainText)
        self.assertFalse(window.resume_btn.isHidden())
        self.assertEqual(window.resume_btn.text(), "Resume task")
        window.resume_btn.click()
        self.resume_cmd.assert_called_once_with("a1b2c3d4")
        self.assertTrue(window.resume_btn.isHidden())
        window.show_status("Status: Step 4 - analyzing screen...")
        self.assertEqual(window.pill.text(), "● Working")
        self.assertTrue(window.resume_btn.isHidden())

    def test_stuck_and_then_forgotten(self):
        self.journal.entries[:] = [entry(state="stuck", why="failed")]
        window = self.window()
        self.assertEqual(window.pill.text(), "● Stuck")
        self.assertTrue(window.status_lbl.text().startswith("Stuck at step 3"))
        window.status_lbl.setText("Gemini saved")
        window.refresh_resume()
        self.assertEqual(window.status_lbl.text(), "Gemini saved")  # nothing changed: its words stay
        self.journal.entries.clear()
        window.refresh_resume()
        self.assertEqual(window.pill.text(), "● Ready")
        self.assertTrue(window.resume_btn.isHidden())

    def test_no_button_without_a_way_to_resume(self):
        window = app.ControlCenterWindow("127.0.0.1", app.Bridge(), lambda text: None, token="test-token")
        self.addCleanup(window.deleteLater)
        self.assertEqual(window.pill.text(), "● Paused")
        self.assertTrue(window.resume_btn.isHidden())


class TrayResumeTests(TempAppData):
    def test_resume_and_forget_in_the_tray(self):
        self.journal.entries[:] = [entry(request="open Tom & Jerry on YouTube and play the first video please")]
        menu, bridge = QMenu(), mock.Mock()
        done = threading.Event()
        with mock.patch.object(app, "resume_task", side_effect=lambda *a, **k: done.set()) as resume, \
                mock.patch.object(app, "forget_task", return_value="Okay, forgotten.") as forget:
            app.fill_tray_menu(menu, mock.Mock(), bridge, 1000, 800)
            titles = [a.text() for a in menu.actions() if not a.isSeparator()]
            stop = titles.index("Stop what it's doing")
            self.assertEqual(titles[stop - 2:stop], ["▶ Resume: “open Tom && Jerry on YouTube and play…” (step 3)",
                                                     "Forget the paused task"])
            menu.actions()[[a.text() for a in menu.actions()].index(titles[stop - 2])].trigger()
            self.assertTrue(done.wait(5))
            resume.assert_called_once_with(bridge, 1000, 800, task_id="a1b2c3d4", source="tray")
            next(a for a in menu.actions() if a.text() == "Forget the paused task").trigger()
            end = time.monotonic() + 5
            while not bridge.say_sig.emit.called and time.monotonic() < end:
                time.sleep(0.02)
        forget.assert_called_once_with("a1b2c3d4")
        bridge.say_sig.emit.assert_called_once_with("Okay, forgotten.", 4000)

    def test_a_held_send_says_what_it_does(self):
        self.journal.entries[:] = [entry(why="waiting_yes")]
        app.PENDING_SEND.update(request="send Dana the photos", time=time.time(), task="a1b2c3d4")
        menu = QMenu()
        app.fill_tray_menu(menu, mock.Mock(), mock.Mock(), 1000, 800)
        self.assertIn("▶ Yes, send it: “send Dana the photos”", [a.text() for a in menu.actions()])

    def test_nothing_new_without_a_paused_task(self):
        self.journal.entries.clear()
        menu = QMenu()
        app.fill_tray_menu(menu, mock.Mock(), mock.Mock(), 1000, 800)
        titles = [a.text() for a in menu.actions() if not a.isSeparator()]
        self.assertEqual(titles[-2:], ["Stop what it's doing", "Quit Motorcycle Man"])
        self.assertFalse(any(t.startswith("▶") or t.startswith("Forget") for t in titles))


if __name__ == "__main__":
    unittest.main()
