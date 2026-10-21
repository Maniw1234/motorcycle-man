import mm_test_env  # noqa: F401 (first: the tests never touch the real settings and keys)
import http.client
import http.server
import json
import os
import tempfile
import threading
import time
import unittest
import urllib.parse
from unittest import mock

from PySide6.QtWidgets import QApplication

import motorcycle_man as app

QT = QApplication.instance() or QApplication([])

TOKEN = "top-picks-token"


class PhoneServer(unittest.TestCase):
    # The real phone server on 127.0.0.1, with its link's secret and a stand-in settings folder.
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.power_now, self.warned = mock.Mock(), []  # never the PC's real power
        self.power = app.pc_power.PowerTimer(self.power_now, self.warned.append)
        self.addCleanup(self.power.cancel)
        for patcher in (mock.patch.dict(os.environ, {"APPDATA": self.temp.name}),
                        mock.patch.object(app, "PHONE_SESSION_TOKEN", TOKEN), mock.patch.dict(app.PHONE_CONTACT),
                        mock.patch.object(app, "POWER", self.power), mock.patch.object(app, "power_now", self.power_now)):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.bridge = mock.Mock()
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), app.make_phone_handler(self.bridge, 2560, 1600))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.port = server.server_address[1]

    def get(self, path, token=True):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        connection.request("GET", path + (("&" if "?" in path else "?") + f"token={TOKEN}" if token else ""))
        reply = connection.getresponse()
        data = reply.read()
        connection.close()
        return reply.status, data

    def post(self, path, body: bytes, token=True):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        connection.request("POST", path + (("&" if "?" in path else "?") + f"token={TOKEN}" if token else ""), body,
                           {"Content-Type": "application/json"})
        reply = connection.getresponse()
        data = reply.read()
        connection.close()
        return reply.status, data

    def get_json(self, path):
        status, data = self.get(path)
        self.assertEqual(status, 200, data)
        return json.loads(data)


class TouchpadTests(PhoneServer):
    def test_moves_clicks_scrolls_and_typing(self):
        with mock.patch.object(app.Win32Input.user32, "mouse_event") as mouse, \
                mock.patch.object(app.Win32Input, "click") as click, mock.patch.object(app.Win32Input, "scroll") as scroll, \
                mock.patch.object(app.Win32Input, "press_key") as key, mock.patch.object(app.Win32Input, "type_unicode_text") as typed, \
                mock.patch.object(app.Win32Input, "mouse_down") as down, mock.patch.object(app.Win32Input, "mouse_up") as up:
            self.assertEqual(self.get_json("/pad?do=move&dx=12&dy=-5000"), {"ok": True})
            mouse.assert_called_once_with(app.MOUSEEVENTF_MOVE, 12, -600, 0, 0)  # held to a sane jump
            for action in ("click", "right", "double"):
                self.get_json(f"/pad?do={action}")
            self.assertEqual(click.call_args_list, [mock.call(button="left", clicks=1), mock.call(button="right", clicks=1),
                                                    mock.call(button="left", clicks=2)])
            self.get_json("/pad?do=down")
            self.get_json("/pad?do=up")
            self.assertEqual((down.call_count, up.call_count), (1, 1))
            self.get_json("/pad?do=scroll&n=-99")
            scroll.assert_called_once_with(-20)
            self.get_json("/pad?do=back&n=3")
            self.get_json("/pad?do=key&key=enter")
            self.assertEqual(self.get_json("/pad?do=key&key=f4"), {"ok": False})  # only the pad's keys
            self.get_json("/pad?do=type&text=" + urllib.parse.quote("héllo"))
            self.assertEqual(key.call_args_list, [mock.call("backspace", presses=3), mock.call("enter")])
            typed.assert_called_once_with("héllo")
        self.assertEqual(self.get("/pad?do=click", token=False)[0], 403)
        for needed in ('id="padView"', "function padFlush", "fromMenu(openPad)"):
            self.assertIn(needed, app.PHONE_PAGE)


class SlidesTests(PhoneServer):
    def test_keys_and_the_laser(self):
        self.assertEqual(self.get_json("/slides?do=hello"), {"ok": True, "aspect": 1.6})
        with mock.patch.object(app.Win32Input, "hotkey") as keys, mock.patch.object(app, "window_title", return_value="Deck - PowerPoint"):
            for action in ("start", "next", "prev", "black", "white", "end"):
                self.assertEqual(self.get_json(f"/slides?do={action}"), {"ok": True})
            self.assertEqual(self.get_json("/slides?do=shutdown"), {"ok": False})
        self.assertEqual([c.args[0] for c in keys.call_args_list], [["f5"], ["right"], ["left"], ["b"], ["w"], ["esc"]])
        with mock.patch.object(app.Win32Input, "hotkey") as keys, \
                mock.patch.object(app, "window_title", return_value="Deck - Google Slides - Google Chrome"):
            self.get_json("/slides?do=start")
        keys.assert_called_once_with(["ctrl", "shift", "f5"])  # F5 would reload the page
        self.get_json("/slides?do=laser&x=0.25&y=7")
        self.bridge.laser_sig.emit.assert_called_with(0.25, 1.0)  # kept on the screen
        self.assertEqual(self.get_json("/slides?do=laser&x=left&y=0"), {"ok": False})
        self.get_json("/slides?do=laser_off")
        self.bridge.laser_sig.emit.assert_called_with(-1.0, -1.0)
        self.assertEqual(self.get("/slides?do=next", token=False)[0], 403)
        for needed in ('id="slidesView"', 'id="laserArea"', "fromMenu(openSlides)", "function laserSend"):
            self.assertIn(needed, app.PHONE_PAGE)

    def test_the_dot_follows_and_fades(self):
        dot = app.LaserDot()
        self.addCleanup(dot.deleteLater)
        area = app.presenting_screen().geometry()
        with mock.patch.object(dot, "show"), mock.patch.object(dot, "hide") as hide:
            dot.point(0.5, 0.5)
            self.assertEqual((dot.at.x(), dot.at.y()), (area.x() + area.width() / 2, area.y() + area.height() / 2))
            dot.point(1.0, 1.0)
            dot.frame()
            self.assertGreater(dot.at.x(), area.x() + area.width() / 2)  # gliding to the new place
            self.assertLess(dot.at.x(), area.x() + area.width())
            dot.point(-1, -1)
            for _ in range(20):
                dot.frame()
            hide.assert_called()
            self.assertEqual(dot.fade, 0.0)
            dot.point(0.1, 0.1)
            dot.heard -= app.LaserDot.QUIET + 1  # the phone went quiet
            dot.frame()
            self.assertFalse(dot.on)


class PowerTests(PhoneServer):
    def test_the_power_panel(self):
        wake = {"mac": "44:38:E8:00:00:01", "broadcast": "192.168.8.255", "card": "Wi-Fi card", "can_wake": False}
        with mock.patch.object(app.pc_power, "wake_info", return_value=wake) as info:
            answer = self.get_json("/power?do=status")
            self.assertEqual(answer["wake"], {"here": True, "card": "Wi-Fi card", "can_wake": False})  # no card address here
            self.assertEqual(answer["power"]["action"], None)
            info.assert_called_with("127.0.0.1")
            self.assertEqual(self.get_json("/power?do=wake_info"), wake)
        answer = self.get_json("/power?do=set&action=restart&minutes=30")
        self.assertEqual(answer["power"]["action"], "restart")
        self.assertIn("Restarting in 30 minutes", answer["power"]["say"])
        self.assertEqual(self.get_json("/power?do=cancel")["say"], "Okay, the PC won't restart.")
        answer = self.get_json("/power?do=set&action=shutdown&minutes=0")  # now: after a minute's warning
        self.assertIn("Shutting down in 1 minute", answer["power"]["say"])
        self.assertEqual(len(self.warned), 1)
        self.get_json("/power?do=cancel")
        self.assertEqual(self.get_json("/power?do=set&action=lock&minutes=0")["say"], "Locked the PC.")
        end = time.monotonic() + 3
        while not self.power_now.called and time.monotonic() < end:
            time.sleep(0.02)
        self.power_now.assert_called_once_with("lock")
        self.assertEqual(self.get_json("/power?do=set&action=format&minutes=5")["power"]["action"], None)
        self.assertEqual(self.get("/power?do=status", token=False)[0], 403)
        for needed in ('id="powerModal"', "fromMenu(openPower)", "function setPower"):
            self.assertIn(needed, app.PHONE_PAGE)

    def test_said_typed_or_sent(self):
        bridge = mock.Mock()
        self.assertIsNone(app.power_reply("open notepad", bridge))
        self.assertIn("Shutting down in 30 minutes", app.power_reply("shut down in 30 minutes", bridge))
        self.assertEqual(self.power.status()["action"], "shutdown")
        self.assertEqual(app.power_reply("cancel shutdown", bridge), "Okay, the PC won't shut down.")
        self.assertIn("When should the PC restart?", app.power_reply("restart in banana", bridge))
        app.GUEST_COMMAND.name = "guest-1"
        self.addCleanup(setattr, app.GUEST_COMMAND, "name", None)
        with mock.patch.object(app, "ask_user_to_confirm", return_value=False) as ask:
            self.assertEqual(app.power_reply("restart in 10 minutes", bridge), "Okay, I won't.")
        self.assertIn("A guest wants the PC to restart", ask.call_args.args[1])
        self.assertEqual(self.power.status()["action"], None)
        with mock.patch.object(app, "ask_user_to_confirm", return_value=True):
            self.assertIn("Restarting in 10 minutes", app.power_reply("restart in 10 minutes", bridge))


class HealthTests(PhoneServer):
    def setUp(self):
        super().setUp()
        self.said = []
        watch = app.pc_health.FinishWatch(self.said.append, self.temp.name)
        self.addCleanup(watch.stop)
        patcher = mock.patch.dict(app.FINISH, {"watch": watch, "loud": False})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_the_health_panel(self):
        answer = self.get_json("/health?do=status&busy=1")
        self.assertIn("cpu", answer["now"])
        self.assertTrue(answer["busy"]["memory"])
        self.assertEqual([s["kind"] for s in answer["switches"]], ["low_battery", "downloads", "disk"])
        self.assertTrue(all(s["on"] for s in answer["switches"]))
        self.assertNotIn("busy", self.get_json("/health?do=status"))
        answer = self.get_json("/health?do=switch&alert=downloads&on=0")
        self.assertEqual({s["kind"]: s["on"] for s in answer["switches"]}, {"low_battery": True, "downloads": False, "disk": True})
        self.assertEqual(app.health_switches()["downloads"], False)
        open(os.path.join(self.temp.name, "song.mp3.part"), "wb").close()
        with mock.patch.object(app.pc_health.FinishWatch, "_watch"):
            self.assertEqual(self.get_json("/health?do=finish")["say"], "Okay, I'll tell you when your download finishes.")
        self.assertEqual(self.get_json("/health?do=status")["watching"], "your download")
        self.assertEqual(self.get_json("/health?do=stop_watch")["say"], "Okay, I stopped watching your download.")
        self.assertEqual(self.get("/health?do=status", token=False)[0], 403)
        for needed in ('id="healthModal"', "fromMenu(openHealth)", "function showHealth"):
            self.assertIn(needed, app.PHONE_PAGE)

    def test_said_typed_or_sent(self):
        self.assertIsNone(app.health_reply("open battery settings"))
        self.assertIn("CPU", app.health_reply("how's the PC doing?"))
        self.assertIn("Using memory", app.health_reply("what’s using my memory"))
        self.assertIn("GB free of", app.health_reply("how much disk space is left"))
        self.assertRegex(app.health_reply("battery"), r"^(Battery|This PC has no battery)")
        open(os.path.join(self.temp.name, "song.mp3.part"), "wb").close()
        with mock.patch.object(app.pc_health.FinishWatch, "_watch"):
            self.assertIn("I'll tell you when your download finishes", app.health_reply("notify me when it's done"))
        self.assertFalse(app.FINISH["loud"])  # asked here, so the chats hear only when nobody's around
        self.assertEqual(app.health_reply("stop watching"), "Okay, I stopped watching your download.")

    def test_alerts_reach_the_phone_and_the_chats(self):
        with mock.patch.object(app, "telegram_alert") as telegram, mock.patch.object(app, "chat_alert") as chats, \
                mock.patch.dict(app.HEALTH_HOOK, {"bridge": self.bridge}), mock.patch.dict(app.TASK_STATUS):
            app.health_alert("finished", "✅ Done.", loud=True)
            notice = app.get_task_status()["notice"]
        telegram.assert_called_once_with("✅ Done.", only_when_away=False)
        chats.assert_called_once_with("✅ Done.", only_when_away=False)
        self.bridge.notice_sig.emit.assert_called_with("✅ Done.", 12000)
        self.assertEqual(notice["text"], "✅ Done.")


class FindTests(PhoneServer):
    def found(self, *names):
        results = []
        for name in names:
            path = os.path.join(self.temp.name, name)
            if name.endswith("/"):
                os.makedirs(path, exist_ok=True)
            else:
                with open(path, "wb") as f:
                    f.write(b"hello")
            results.append({"path": path.rstrip("/"), "name": os.path.basename(path.rstrip("/")), "folder": "Desktop",
                            "size": 0 if name.endswith("/") else 5, "modified": int(time.time()), "is_folder": name.endswith("/")})
        return results

    def test_the_find_panel(self):
        results = self.found("resume.pdf", "setup.exe", "Jobs/")
        with mock.patch.object(app.find_files, "search", return_value=results) as search:
            answer = self.get_json("/find?q=my+resume&kind=pdf")
        self.assertEqual(search.call_args.args[0]["words"], ["resume"])
        self.assertEqual(search.call_args.args[0]["kind"], "pdf")
        self.assertEqual(answer["say"], "Found 3.")
        pdf, exe, folder = answer["results"]
        self.assertNotIn("path", pdf)  # the phone gets ids, never paths
        self.assertEqual((pdf["name"], pdf["size"], pdf["folder_item"], folder["folder_item"]), ("resume.pdf", 5, False, True))
        status, data = self.get(f"/file?id={pdf['id']}")
        self.assertEqual((status, data), (200, b"hello"))
        with mock.patch.object(app.os, "startfile", create=True) as start, mock.patch.object(app.subprocess, "Popen") as popen:
            self.assertEqual(self.get_json(f"/find?do=open&id={pdf['id']}")["say"], "Opened resume.pdf on the PC.")
            self.assertEqual(self.get_json(f"/find?do=open&id={exe['id']}")["say"], "That one runs a program, so open it at the PC.")
            self.assertEqual(self.get_json(f"/find?do=folder&id={pdf['id']}")["say"], "Showing it in its folder on the PC.")
            self.get_json(f"/find?do=open&id={folder['id']}")
            self.assertEqual(self.get_json("/find?do=open&id=guessed")["say"], "That isn't there any more.")
        self.assertEqual(start.call_args_list, [mock.call(results[0]["path"]), mock.call(results[2]["path"])])
        popen.assert_called_once_with(["explorer.exe", "/select,", results[0]["path"]])
        self.assertEqual(self.get_json("/find?q=the")["say"], "Type a name, or words that are inside it.")
        with mock.patch.object(app.find_files, "search", return_value=[]):
            self.assertEqual(self.get_json("/find?q=unicorn")["say"], "No files named “unicorn” on the PC.")
        self.assertEqual(self.get("/find?q=resume", token=False)[0], 403)
        for needed in ('id="findModal"', "fromMenu(openFind)", "function findFiles"):
            self.assertIn(needed, app.PHONE_PAGE)

    def test_said_typed_or_sent(self):
        results = self.found("resume.pdf")
        with mock.patch.object(app.find_files, "search", return_value=results) as search:
            self.assertIsNone(app.find_reply("find a cheap flight to Paris"))
            self.assertIsNone(app.find_reply("where is the nearest gas station"))
            reply = app.find_reply("where’s my resume?")
            self.assertTrue(reply.startswith("Found it:\n1. resume.pdf, in Desktop (5 bytes, today"), reply)
            self.assertIsNotNone(app.find_reply("search my PC for taxes"))
            self.assertIsNotNone(app.find_reply("find the budget spreadsheet"))
        self.assertEqual([c.args[0]["words"] for c in search.call_args_list], [["resume"], ["taxes"], ["budget"]])
        with mock.patch.object(app.find_files, "search", return_value=[]):
            self.assertEqual(app.find_reply("find my unicorn photos"), "I couldn't find photos named “unicorn” on the PC.")


class BriefTests(PhoneServer):
    PLACE = {"name": "Springfield, Massachusetts", "lat": 42.1, "lon": -72.6}
    FORECAST = {"now": 52, "feels": 50, "code": 0, "wind": 5, "high": 63, "low": 44, "rain": 10, "day_code": 0, "sunset": "18:05",
                "wind_unit": "mph"}

    def setUp(self):
        super().setUp()
        for patcher in (mock.patch.object(app.daily_brief, "find_place", side_effect=lambda name: self.PLACE if "spring" in name.lower() else None),
                        mock.patch.object(app.WEATHER, "today", return_value=self.FORECAST),
                        mock.patch.object(app.REMINDERS, "upcoming", return_value=[])):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_the_brief_panel(self):
        answer = self.get_json("/brief?do=status")
        self.assertIn('Say "set my city to"', answer["brief"])
        self.assertEqual((answer["place"], answer["weather"], answer["morning"]), ("", None, True))
        answer = self.get_json("/brief?do=place&name=Springfield%2C+MA")
        self.assertEqual(answer["say"], "Got it: Springfield, Massachusetts.")
        self.assertEqual((answer["place"], answer["weather"]["icon"], answer["weather"]["sky"]), ("Springfield, Massachusetts", "☀️", "clear"))
        self.assertIn("☀️ 52° and clear in Springfield", answer["brief"])
        self.assertEqual(self.get_json("/brief?do=place&name=Atlantis")["say"], "I couldn't find a town called Atlantis.")
        self.assertFalse(self.get_json("/brief?do=morning&on=0")["morning"])
        self.assertEqual(self.get("/brief?do=status", token=False)[0], 403)
        for needed in ('id="briefModal"', "fromMenu(openBrief)", "function showBrief"):
            self.assertIn(needed, app.PHONE_PAGE)

    def test_said_typed_or_sent(self):
        self.assertIn("Which town?", app.brief_reply("what's the weather"))
        self.assertTrue(app.brief_reply("set my city to Springfield, MA").startswith("Got it: Springfield, Massachusetts."))
        self.assertTrue(app.brief_reply("is it going to rain today?").startswith("☀️ 52° and clear"))
        self.assertRegex(app.brief_reply("Good morning!"), r"^Good (morning|afternoon|evening)! It's \w+day")
        for other in ("run my morning routine", "remind me to check the weather", "open weather.com"):
            self.assertIsNone(app.brief_reply(other), other)

    def test_todays_reminders_and_routines(self):
        app.save_setting("brief_place", self.PLACE)
        end = app.daily_brief.today_end(time.time())
        upcoming = [{"due": end - 60, "text": "call the dentist", "kind": "reminder"},
                    {"due": end + 3600, "text": "tomorrow's thing", "kind": "reminder"},
                    {"due": end - 30, "text": "Evening", "kind": "routine", "routine": "r1"}]
        with mock.patch.object(app.REMINDERS, "upcoming", return_value=upcoming), \
                mock.patch.object(app.ROUTINES, "find", return_value={"name": "Evening"}), \
                mock.patch.object(app.pc_health, "battery", return_value={"percent": 22, "charging": False, "minutes_left": 50}):
            text = app.brief_text()
        self.assertIn("11:59 PM call the dentist.", text)
        self.assertNotIn("tomorrow's thing", text)
        self.assertIn("Your Evening routine runs at 11:59 PM.", text)
        self.assertIn("Battery 22%: plug in soon.", text)


class PhoneAlertTests(PhoneServer):
    def test_shown_by_the_buddy_unless_something_is_full_screen(self):
        notice = json.dumps({"app": "WhatsApp", "title": "Mom", "text": "Are you\ncoming?"}).encode()
        before = app.PHONE_CONTACT.get("time")
        with mock.patch.object(app, "pc_takes_notices", return_value=True):
            self.assertEqual(self.post("/phone_notice", notice), (200, b'{"ok": true}'))
        self.bridge.notice_sig.emit.assert_called_once_with("📱 WhatsApp · Mom: Are you coming?", 9000)
        self.assertEqual(app.PHONE_CONTACT.get("time"), before)  # a phone in a pocket isn't someone at the page
        with mock.patch.object(app, "pc_takes_notices", return_value=False):
            self.assertEqual(self.post("/phone_notice", notice)[0], 200)
        self.assertEqual(self.bridge.notice_sig.emit.call_count, 1)
        self.assertEqual(self.post("/phone_notice", b"not json"), (200, b'{"ok": false}'))
        self.assertEqual(self.post("/phone_notice", json.dumps({"app": "Clock"}).encode()), (200, b'{"ok": false}'))
        self.assertEqual(self.post("/phone_notice", b"x" * 20000)[0], 413)
        self.assertEqual(self.post("/phone_notice", notice, token=False)[0], 403)
        self.assertEqual(self.bridge.notice_sig.emit.call_count, 1)
        for needed in ('id="rowNotices"', "window.onNotices", "function toggleNotices"):
            self.assertIn(needed, app.PHONE_PAGE)

    def test_windows_says_when(self):
        def state(value):
            def query(pointer):
                pointer._obj.value = value
                return 0
            return query
        for value, shown in ((5, True), (6, True), (1, False), (3, False), (4, False), (7, False)):
            with mock.patch.object(app.ctypes.windll.shell32, "SHQueryUserNotificationState", side_effect=state(value)):
                self.assertEqual(app.pc_takes_notices(), shown, value)


if __name__ == "__main__":
    unittest.main()
