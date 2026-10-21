import mm_test_env  # noqa: F401 (first: the tests never touch the real settings and keys)
import json
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

from PySide6.QtCore import QMimeData
from PySide6.QtNetwork import QLocalSocket
from PySide6.QtWidgets import QApplication

import motorcycle_man as app
from test_top_picks import PhoneServer

QT = QApplication.instance() or QApplication([])


class SendToPhoneTests(PhoneServer):
    def setUp(self):
        super().setUp()
        for patcher in (mock.patch.object(app, "PHONE_INBOX", app.PhoneInbox()), mock.patch.object(app, "SENT_TO_PHONE", [])):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.files = []
        for name, size in (("Trip plan.pdf", 5), ("song.mp3", 7)):
            path = os.path.join(self.temp.name, name)
            with open(path, "wb") as f:
                f.write(b"x" * size)
            self.files.append(path)

    def test_files_go_to_the_phone_and_the_send_panel(self):
        said = app.send_to_phone(self.files + [self.temp.name])
        self.assertTrue(said.startswith("2 files will reach your phone when the app is back online"), said)
        self.assertTrue(said.endswith("Folders were left out."))
        jobs = app.PHONE_INBOX.take(0)
        self.assertEqual([(j["kind"], j["name"], j["size"]) for j in jobs], [("file", "Trip plan.pdf", 5), ("file", "song.mp3", 7)])
        status, data = self.get(f"/file?id={jobs[0]['id']}")
        self.assertEqual((status, data), (200, b"xxxxx"))
        sent = self.get_json("/files")["sent"]
        self.assertEqual([s["name"] for s in sent], ["song.mp3", "Trip plan.pdf"])  # the newest first
        app.PHONE_INBOX.asked_at = time.monotonic()  # the phone is there
        self.assertEqual(app.send_to_phone(self.files[:1]), "Sending “Trip plan.pdf” to your phone.")
        self.assertEqual(app.send_to_phone([self.temp.name]), "Only files can go to the phone, not folders.")

    def test_opened_by_send_to_while_already_running(self):
        self.assertEqual(app.sending_asked(["Motorcycle Man.exe", "--send-to-phone", r"C:\a b.pdf", r"C:\c.txt"]),
                         [r"C:\a b.pdf", r"C:\c.txt"])
        self.assertIsNone(app.sending_asked(["Motorcycle Man.exe"]))
        window, bridge = mock.Mock(), mock.Mock()
        with mock.patch.object(app, "instance_name", return_value=f"mm-test-{os.getpid()}"):
            server = app.listen_for_another_start(window, bridge)
            self.addCleanup(server.close)
            message = b"send\n" + "\n".join(self.files).encode("utf-8")
            socket = QLocalSocket()
            socket.connectToServer(app.instance_name())
            self.assertTrue(socket.waitForConnected(1000))
            socket.write(message)
            socket.waitForBytesWritten(1000)
            deadline = time.monotonic() + 3
            while not bridge.say_sig.emit.called and time.monotonic() < deadline:
                QT.processEvents()
            socket.disconnectFromServer()
        bridge.say_sig.emit.assert_called_once()
        self.assertIn("2 files", bridge.say_sig.emit.call_args.args[0])
        window.showNormal.assert_not_called()  # it sends; it doesn't come forward
        self.assertEqual(len(app.PHONE_INBOX.take(0)), 2)

    def test_the_send_to_shortcut(self):
        send_to = os.path.join(self.temp.name, "Microsoft", "Windows", "SendTo")
        os.makedirs(send_to)
        with mock.patch.object(sys, "frozen", True, create=True), mock.patch.object(sys, "executable", r"C:\Apps\Motorcycle Man.exe"):
            app.put_in_send_to()
        link = os.path.join(send_to, app.SEND_TO_NAME)
        self.assertTrue(os.path.isfile(link))
        with open(link, "rb") as shortcut:
            self.assertIn("--send-to-phone".encode("utf-16-le"), shortcut.read())
        os.remove(link)
        app.put_in_send_to()  # run from source: nothing to point at
        self.assertFalse(os.path.exists(link))


def text_picture(lines):
    from PIL import Image, ImageDraw, ImageFont
    picture = Image.new("RGB", (2560, 1600), "white")
    draw, font = ImageDraw.Draw(picture), ImageFont.truetype("segoeui.ttf", 30)
    for n, line in enumerate(lines):
        draw.text((100, 100 + n * 60), line, fill="black", font=font)
    return picture


class ScreenTextTests(PhoneServer):
    def test_reading_a_box_on_the_screen(self):
        screen = text_picture(["Invoice 4521, total 38.90", "Thank you for riding with us"])
        with mock.patch.object(app.ImageGrab, "grab", return_value=screen):
            answer = self.get_json("/screen_text?x0=0&y0=0.05&x1=0.5&y1=0.096")  # just the first line
            self.assertIn("4521", answer["text"])
            self.assertNotIn("riding", answer["text"])
            self.assertEqual(self.get_json("/screen_text?x0=0.6&y0=0.6&x1=0.9&y1=0.9"), {"text": "", "say": "No text there."})
        self.assertEqual(self.get("/screen_text", token=False)[0], 403)
        for needed in ('id="screenTextModal"', 'id="screenTextBtn"', "function screenPickEnd"):
            self.assertIn(needed, app.PHONE_PAGE)

    def test_read_the_screen_from_anywhere(self):
        screen = text_picture(["Motorcycle Man reads the screen"])
        with mock.patch.object(app.ImageGrab, "grab", return_value=screen), \
                mock.patch.object(app, "set_clipboard_text", return_value=True) as clipboard:
            reply = app.screen_text_reply("copy the text on the screen")
        self.assertTrue(reply.startswith("Copied 1 line from the screen (on the PC clipboard):\nMotorcycle Man reads the screen"), reply)
        clipboard.assert_called_once_with("Motorcycle Man reads the screen")
        self.assertIsNone(app.screen_text_reply("read my email"))
        with mock.patch.object(app.pc_ocr, "read_text", side_effect=app.pc_ocr.Unavailable("no language")):
            self.assertIn("can't read text here", app.screen_text_reply("read the screen"))


class ClipboardSyncTests(PhoneServer):
    def setUp(self):
        super().setUp()
        for patcher in (mock.patch.object(app, "PHONE_INBOX", app.PhoneInbox()), mock.patch.dict(app.CLIP_SYNC, {"last": ""})):
            patcher.start()
            self.addCleanup(patcher.stop)

    def copied(self, text, private=None):
        data = QMimeData()
        data.setText(text)
        if private:
            data.setData(f'application/x-qt-windows-mime;value="{private[0]}"', private[1])
        app.pc_clipboard_changed(mock.Mock(mimeData=lambda: data))

    def test_copied_on_the_pc_goes_to_the_phone(self):
        self.copied("first")
        self.copied("second")  # only the newest waits for the phone
        self.copied("second")
        self.copied("a password", ("ExcludeClipboardContentFromMonitorProcessing", b"\x01"))
        self.copied("another password", ("CanIncludeInClipboardHistory", b"\x00\x00\x00\x00"))
        self.copied("   ")
        self.assertEqual(app.PHONE_INBOX.take(0), [{"kind": "clip", "text": "second"}])
        self.copied("fine", ("CanIncludeInClipboardHistory", b"\x01\x00\x00\x00"))  # history allowed: so is the phone
        self.assertEqual(app.PHONE_INBOX.take(0), [{"kind": "clip", "text": "fine"}])

    def test_copied_on_the_phone_comes_to_the_pc_without_coming_back(self):
        with mock.patch.object(app, "set_clipboard_text", return_value=True) as clipboard:
            self.assertEqual(self.post("/clip_sync", json.dumps({"text": "from the phone"}).encode()), (200, b'{"ok": true}'))
            self.copied("from the phone")  # the PC's own "changed" for it
            self.assertEqual(self.post("/clip_sync", json.dumps({"text": "from the phone"}).encode())[1], b'{"ok": false}')
        clipboard.assert_called_once_with("from the phone")
        self.assertEqual(app.PHONE_INBOX.take(0), [])
        self.assertEqual(self.post("/clip_sync", b"not json")[1], b'{"ok": false}')
        self.assertEqual(self.post("/clip_sync", b"{}", token=False)[0], 403)

    def test_the_switch(self):
        self.assertEqual(self.get_json("/clip_sync"), {"on": True})
        self.assertEqual(self.get_json("/clip_sync?on=0"), {"on": False})
        self.copied("not sent")
        with mock.patch.object(app, "set_clipboard_text") as clipboard:
            self.post("/clip_sync", json.dumps({"text": "not taken"}).encode())
        clipboard.assert_not_called()
        self.assertEqual(app.PHONE_INBOX.take(0), [])
        self.assertIn('id="clipSync"', app.PHONE_PAGE)


SONG = {"title": "Ride On", "artist": "The Bikers", "album": "", "app": "Spotify", "playing": True, "position": 61.5,
        "length": 200.0, "can_seek": True, "art": b"\x89PNG cover"}


class MusicTests(PhoneServer):
    def test_whats_playing_and_its_cover(self):
        with mock.patch.object(app.pc_media, "media", return_value=dict(SONG)) as media:
            now = self.get_json("/media")["now"]
            self.assertEqual((now["title"], now["artist"], now["app"], now["position"]), ("Ride On", "The Bikers", "Spotify", 61.5))
            self.assertEqual(self.get(f"/media_art?k={now['art']}"), (200, b"\x89PNG cover"))
            self.get_json("/media?do=seek&at=120")
            self.get_json("/media?do=format")  # not a thing: just asks what's playing
        self.assertEqual([c.args for c in media.call_args_list], [("info", -1.0), ("seek", 120.0), ("info", -1.0)])
        self.assertEqual(self.get("/media_art?k=guessed")[0], 404)
        with mock.patch.object(app.pc_media, "media", return_value={"title": None}):
            self.assertEqual(self.get_json("/media"), {"now": {"title": None}})
            self.assertEqual(app.playing_reply("what's playing"), "Nothing's playing on the PC.")
        with mock.patch.object(app.pc_media, "media", return_value=dict(SONG)):
            self.assertEqual(app.playing_reply("What song is this?"), "🎵 Ride On by The Bikers, on Spotify.")
        self.assertIsNone(app.playing_reply("play some music"))
        self.assertEqual(self.get("/media", token=False)[0], 403)
        self.assertIn('id="musicModal"', app.PHONE_PAGE)

    def test_volume_app_by_app(self):
        apps = [{"path": r"C:\Apps\Spotify.exe", "volume": 80, "muted": False},
                {"path": r"C:\Windows\System32\audiodg.exe", "volume": 100, "muted": False}, {"path": "", "volume": 100, "muted": False}]
        with mock.patch.object(app.pc_mixer, "apps", return_value=apps), mock.patch.object(app, "master_volume", return_value=(40, False)), \
                mock.patch.object(app.pc_mixer, "set_app", return_value=True) as set_app, \
                mock.patch.object(app, "set_master_volume") as set_master:
            answer = self.get_json("/mixer")
            self.assertEqual((answer["master"], [a["name"] for a in answer["apps"]]), (40, ["Spotify"]))  # Windows' helpers left out
            key = answer["apps"][0]["key"]
            self.get_json(f"/mixer?do=app&k={key}&volume=25")
            self.get_json(f"/mixer?do=app&k={key}&muted=1")
            self.get_json("/mixer?do=master&volume=70")
            self.assertEqual(self.get_json("/mixer?do=app&k=guessed&volume=5")["say"], "That app has no sound now.")
        self.assertEqual(set_app.call_args_list, [mock.call(r"C:\Apps\Spotify.exe", 25, None), mock.call(r"C:\Apps\Spotify.exe", None, True)])
        set_master.assert_called_once_with(70)

    def test_this_pc(self):
        self.assertIn("title", app.pc_media.media())
        for found in app.pc_mixer.apps():
            self.assertTrue(0 <= found["volume"] <= 100)
        self.assertEqual(app.pc_media.app_label("Microsoft.ZuneMusic_8wekyb3d8bbwe!Microsoft.ZuneMusic"), "Media Player")
        self.assertEqual(app.pc_media.app_label("Spotify.exe"), "Spotify")


class QuickSettingsTests(PhoneServer):
    def setUp(self):
        super().setUp()
        self.radios = {"wifi": True, "bluetooth": True}

        def radios(kind="", on=-1):
            if kind:
                self.radios[kind.lower()] = bool(on)
            return dict(self.radios)
        for patcher in (mock.patch.object(app.pc_settings, "radios", side_effect=radios),
                        mock.patch.object(app.pc_settings, "set_dark_mode"), mock.patch.object(app.pc_settings, "dark_mode", return_value=True),
                        mock.patch.object(app.pc_settings, "set_warm_screen", return_value=True),
                        mock.patch.object(app.pc_settings, "set_do_not_disturb", return_value=True),
                        mock.patch.dict(app.pc_settings.STATE, {"warm": False, "dnd": None})):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_the_settings_panel(self):
        answer = self.get_json("/settings")
        self.assertEqual(answer["state"], {"dark": True, "warm": False, "dnd": None, "wifi": True, "bluetooth": True})
        self.assertEqual(self.get_json("/settings?do=dark&on=0")["say"], "Light mode on.")
        app.pc_settings.set_dark_mode.assert_called_once_with(False)
        self.assertEqual(self.get_json("/settings?do=warm&on=1")["say"], "The screen is warm for the night.")
        self.assertEqual(self.get_json("/settings?do=dnd&on=1")["say"], "Do not disturb is on.")
        self.assertEqual(self.get_json("/settings?do=bluetooth&on=0")["say"], "Bluetooth is off.")
        self.assertEqual(self.get("/settings", token=False)[0], 403)
        self.assertIn('id="settingsModal"', app.PHONE_PAGE)

    def test_wifi_off_from_the_same_network_asks_twice(self):
        with mock.patch.object(app.ipaddress, "ip_address", return_value=mock.Mock(is_private=True, is_loopback=False)):
            first = self.get_json("/settings?do=wifi&on=0")
            self.assertEqual(first["ask"], "wifi")
            self.assertTrue(self.radios["wifi"])  # not yet
            self.assertEqual(self.get_json("/settings?do=wifi&on=0&anyway=1")["say"], "Wi-Fi is off.")
        self.assertFalse(self.radios["wifi"])
        self.assertEqual(app.quick_setting("bluetooth", False, from_bluetooth=True)["say"],
                         "This phone is connected over Bluetooth, so Bluetooth stays on.")
        self.assertTrue(self.radios["bluetooth"])

    def test_said_typed_or_sent(self):
        self.assertEqual(app.settings_reply("turn on dark mode"), "Dark mode on.")
        self.assertEqual(app.settings_reply("light mode"), "Light mode on.")
        self.assertEqual(app.settings_reply("dark mode off"), "Light mode on.")
        self.assertEqual(app.settings_reply("Night light on"), "The screen is warm for the night.")
        self.assertEqual(app.settings_reply("turn off the wi-fi"), "Wi-Fi is off.")
        self.radios["wifi"] = True
        with mock.patch.object(app.TELEGRAM_COMMAND, "active", True, create=True):
            self.assertIn("won't switch Wi-Fi off from a chat", app.settings_reply("wifi off"))
        self.assertTrue(self.radios["wifi"])
        self.assertIsNone(app.settings_reply("bluetooth"))
        self.assertIsNone(app.settings_reply("open bluetooth settings"))


class WindowsSettingsTests(unittest.TestCase):
    def test_the_warm_screen(self):
        ramps = []
        with mock.patch.object(app.pc_settings.ctypes.windll.gdi32, "SetDeviceGammaRamp",
                               side_effect=lambda dc, ramp: ramps.append(list(ramp._obj)) or 1), \
                mock.patch.dict(app.pc_settings.STATE, {"warm": False}):
            self.assertTrue(app.pc_settings.set_warm_screen(True))
            self.assertTrue(app.pc_settings.STATE["warm"])
            self.assertTrue(app.pc_settings.set_warm_screen(False))
        warm, plain = ramps
        self.assertEqual((warm[255], plain[511], plain[767]), (65535, 65535, 65535))
        self.assertLess(warm[767], warm[511])
        self.assertLess(warm[511], warm[255])

    def test_dark_mode(self):
        written = []
        with mock.patch.object(app.pc_settings.winreg, "OpenKey", mock.MagicMock()), \
                mock.patch.object(app.pc_settings.winreg, "SetValueEx",
                                  side_effect=lambda key, name, reserved, kind, value: written.append((name, value))), \
                mock.patch.object(app.pc_settings.ctypes.windll.user32, "SendMessageTimeoutW") as told:
            app.pc_settings.set_dark_mode(True)
        self.assertEqual(written, [("AppsUseLightTheme", 0), ("SystemUsesLightTheme", 0)])
        self.assertEqual(told.call_args.args[3], "ImmersiveColorSet")  # open apps hear of it

    def test_do_not_disturb_through_the_notification_panel(self):
        toggle = mock.Mock(CurrentToggleState=0)
        toggle.Toggle.side_effect = lambda: setattr(toggle, "CurrentToggleState", 1)
        button = mock.Mock()
        button.GetCurrentPattern.return_value.QueryInterface.return_value = toggle
        panel = mock.Mock()
        panel.FindFirst.return_value = button
        automation = mock.Mock()
        first = iter([None])
        automation.GetRootElement.return_value.FindFirst.side_effect = lambda *a: next(first, panel)  # it takes a moment to open
        keys = mock.Mock()
        with mock.patch.object(app.pc_settings, "_uia", return_value=(mock.Mock(), automation)), \
                mock.patch.object(app.pc_settings.time, "sleep"), mock.patch.dict(app.pc_settings.STATE, {"dnd": None}):
            self.assertTrue(app.pc_settings.set_do_not_disturb(True, keys))
            toggle.Toggle.assert_called_once_with()
            self.assertTrue(app.pc_settings.set_do_not_disturb(True, keys))  # already on: left alone
            self.assertEqual(toggle.Toggle.call_count, 1)
        self.assertEqual(keys.call_args_list, [mock.call(["win", "n"]), mock.call(["esc"])] * 2)  # opened, and closed again
        automation.GetRootElement.return_value.FindFirst.side_effect = None
        automation.GetRootElement.return_value.FindFirst.return_value = None
        with mock.patch.object(app.pc_settings, "_uia", return_value=(mock.Mock(), automation)), \
                mock.patch.object(app.pc_settings.time, "sleep"), self.assertRaises(app.pc_settings.Unavailable):
            app.pc_settings.set_do_not_disturb(True, keys, wait=0)
        self.assertEqual(keys.call_args_list[-1], mock.call(["esc"]))  # closed even then


class BrowseTests(PhoneServer):
    def setUp(self):
        super().setUp()
        self.top = os.path.join(self.temp.name, "Stuff")
        os.makedirs(os.path.join(self.top, "beta folder"))
        os.makedirs(os.path.join(self.top, "Alpha folder"))
        for name in ("zebra.txt", "apple.pdf", "secret.txt", "desktop.ini"):
            with open(os.path.join(self.top, name), "wb") as f:
                f.write(b"12345")
        app.ctypes.windll.kernel32.SetFileAttributesW(os.path.join(self.top, "secret.txt"), 0x2)  # hidden

    def test_a_folder(self):
        answer = app.browse(app.phone_file_id(self.top))
        self.assertEqual([(i["name"], i["folder"]) for i in answer["items"]],
                         [("Alpha folder", True), ("beta folder", True), ("apple.pdf", False), ("zebra.txt", False)])
        self.assertEqual(answer["name"], "Stuff")
        self.assertEqual(app.listed_pc_path(answer["parent"]), self.temp.name)
        pdf = answer["items"][2]
        self.assertEqual(self.get(f"/file?id={pdf['id']}"), (200, b"12345"))
        inside = app.browse(answer["items"][0]["id"])
        self.assertEqual((inside["items"], app.listed_pc_path(inside["parent"])), ([], self.top))

    def test_the_top_and_the_drives(self):
        top = self.get_json("/browse")
        self.assertEqual((top["name"], top["parent"]), ("This PC", None))
        drive = next(p for p in top["items"] if p["name"].startswith("Drive "))
        root = self.get_json(f"/browse?id={drive['id']}")
        self.assertEqual(root["parent"], "")  # up from a drive is This PC
        self.assertEqual(self.get_json("/browse?id=guessed")["say"], "That folder isn't there any more.")
        self.assertEqual(self.get("/browse", token=False)[0], 403)
        self.assertIn('id="filesModal"', app.PHONE_PAGE)

    def test_files_from_the_phone_into_a_folder(self):
        folder_id = app.phone_file_id(os.path.join(self.top, "beta folder"))
        status, data = self.post(f"/upload?name=photo.jpg&into={folder_id}", b"picture")
        self.assertEqual(status, 200, data)
        with open(os.path.join(self.top, "beta folder", "photo.jpg"), "rb") as f:
            self.assertEqual(f.read(), b"picture")
        self.post(f"/upload?name=photo.jpg&into={folder_id}", b"again")  # never over what's there
        self.assertTrue(os.path.exists(os.path.join(self.top, "beta folder", "photo (1).jpg")))
        self.assertIn("beta folder", self.bridge.notice_sig.emit.call_args.args[0])


class PhoneBatteryTests(PhoneServer):
    def setUp(self):
        super().setUp()
        for patcher in (mock.patch.dict(app.PHONE_BATTERY, {"percent": None, "charging": False, "time": 0.0, "warned": 101}),
                        mock.patch.object(app, "PHONE_INBOX", app.PhoneInbox()), mock.patch.dict(app.TASK_STATUS)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_heard_with_the_inbox_and_the_status(self):
        self.assertIn("haven't heard from your phone", app.phone_battery_reply("phone battery"))
        self.get_json("/phone_inbox?wait=0&battery=64&charging=1")
        self.assertEqual(app.phone_battery_reply("how's my phone's battery"), "Your phone's battery is at 64%, charging.")
        self.assertEqual(app.tray_tip(), "Motorcycle Man · phone 64% ⚡")
        self.get_json("/status?battery=58&charging=0")
        self.assertEqual(app.phone_battery_reply("how much battery does my phone have"), "Your phone's battery is at 58%.")
        self.get_json("/status?battery=lots")  # nonsense changes nothing
        self.assertEqual(app.PHONE_BATTERY["percent"], 58)
        self.assertIsNone(app.phone_battery_reply("battery"))  # the PC's own, for Health

    def test_low_once_per_level_until_it_charges(self):
        for percent in (25, 20, 19, 12, 10, 8):
            app.phone_battery_heard({"battery": [str(percent)], "charging": ["0"]}, self.bridge)
        said = [c.args[0] for c in self.bridge.notice_sig.emit.call_args_list]
        self.assertEqual(said, ["📱 Your phone's battery is at 20%: time to charge it.",
                                "📱 Your phone's battery is at 10%: time to charge it."])
        app.phone_battery_heard({"battery": ["9"], "charging": ["1"]}, self.bridge)
        app.phone_battery_heard({"battery": ["9"], "charging": ["0"]}, self.bridge)
        self.assertEqual(self.bridge.notice_sig.emit.call_count, 3)  # unplugged again while low: said again
        self.assertIn("9%", app.get_task_status()["notice"]["text"])


class GuardTests(PhoneServer):
    def setUp(self):
        super().setUp()
        self.tick = 1000
        for patcher in (mock.patch.dict(app.GUARD, {"armed": False, "tick": 0, "from": 0.0, "lock": False, "tripped": ""}),
                        mock.patch.object(app, "last_input_tick", side_effect=lambda: self.tick),
                        mock.patch.object(app, "PHONE_INBOX", app.PhoneInbox()), mock.patch.object(app, "telegram_bot_running", return_value=None),
                        mock.patch.dict(app.CHAT_SERVICES, {}, clear=True), mock.patch.dict(app.TASK_STATUS),
                        mock.patch.object(app.ctypes.windll.user32, "LockWorkStation"),
                        mock.patch.dict(app.APP_INPUT, {"time": -1e9})):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_someone_at_the_pc_is_caught_once(self):
        self.assertIn("Guarding the PC", self.get_json("/guard?on=1&lock=1")["say"])
        start = time.monotonic()
        self.assertFalse(app.guard_check(now=start + 10))  # nothing touched
        self.tick = 2000
        self.assertFalse(app.guard_check(now=start + 1))  # still in the grace after arming
        self.assertTrue(app.guard_check(now=start + 10))
        alert = app.PHONE_INBOX.take(0)[0]
        self.assertEqual(alert["kind"], "alert")
        self.assertTrue(alert["text"].startswith("🚨 Someone is using your PC"), alert)
        self.assertIn("I locked it.", alert["text"])
        app.ctypes.windll.user32.LockWorkStation.assert_called_once_with()
        self.assertFalse(app.GUARD["armed"])  # once, until armed again
        self.tick = 3000
        self.assertFalse(app.guard_check(now=start + 20))
        self.assertTrue(self.get_json("/guard")["tripped"])

    def test_motorcycle_mans_own_input_isnt_someone(self):
        app.arm_guard(True, lock=False)
        app.GUARD["from"] = 0.0
        with mock.patch.object(app.ctypes.windll.user32, "SetCursorPos"):
            app.Win32Input.user32.SetCursorPos(5, 5)  # the phone's remote control moves the mouse
        self.tick = 2000
        self.assertFalse(app.guard_check())
        self.assertEqual(app.GUARD["tick"], 2000)  # noted, so the next touch counts
        self.assertTrue(app.GUARD["armed"])
        app.APP_INPUT["time"] = -1e9
        self.tick = 2500
        self.assertTrue(app.guard_check())
        app.ctypes.windll.user32.LockWorkStation.assert_not_called()

    def test_said_typed_or_sent(self):
        self.assertIn("Guarding the PC", app.guard_reply("guard my PC while I'm away"))
        self.assertTrue(app.GUARD["armed"])
        self.assertEqual(app.guard_reply("stop guarding my pc"), "The guard is off.")
        self.assertIn("Guarding", app.guard_reply("I'm leaving"))
        self.assertIsNone(app.guard_reply("guard the castle"))


class ScreenTimeAppTests(PhoneServer):
    def test_the_screen_time_panel(self):
        book = app.screen_time.ScreenTime(lambda: os.path.join(self.temp.name, "screen_time.json"))
        book.add("Chrome", 3600, r"C:\Apps\chrome.exe")
        with mock.patch.object(app, "SCREEN_TIME", book):
            answer = self.get_json("/screen_time")
            self.assertEqual((answer["total"], answer["apps"][0]["name"], answer["apps"][0]["seconds"]), (3600, "Chrome", 3600))
            self.assertEqual(app.WINDOW_ICONS[answer["apps"][0]["icon"]], r"C:\Apps\chrome.exe")
            self.assertEqual(len(answer["week"]), 7)
            self.assertEqual(app.screen_time_reply("how long have I been on the computer today"), "1 h on the PC today: Chrome 1 h.")
            self.assertIsNone(app.screen_time_reply("time to go"))
            with mock.patch.object(app, "user_idle_seconds", return_value=5), \
                    mock.patch.object(app, "pc_takes_notices_or_full_screen", return_value=True), \
                    mock.patch.object(app.pc_windows, "window_program", return_value=(1, r"C:\Apps\Code.exe")), \
                    mock.patch.object(app._user32_windows, "GetForegroundWindow", return_value=77):
                app.screen_time_tick()
            self.assertEqual(dict(book.day())["VS Code"], app.SCREEN_TIME_EVERY)
            with mock.patch.object(app, "user_idle_seconds", return_value=600):
                app.screen_time_tick()  # nobody there: nothing added
            self.assertEqual(dict(book.day())["VS Code"], app.SCREEN_TIME_EVERY)
        self.assertIn('id="timeModal"', app.PHONE_PAGE)


class FocusAndTricksTests(PhoneServer):
    def setUp(self):
        super().setUp()
        for patcher in (mock.patch.dict(app.FOCUS, {"until": 0.0, "minutes": 0, "block": True, "halfway": False, "said_at": 0.0,
                                                     "done": 0, "day": ""}),
                        mock.patch.object(app, "PHONE_INBOX", app.PhoneInbox()),
                        mock.patch.object(app.pc_windows, "minimize")):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_a_focus_session(self):
        start = time.time()
        self.assertIn("Focus time! 20 minutes", app.start_focus(20, bridge=self.bridge, now=start))
        with mock.patch.object(app._user32_windows, "GetForegroundWindow", return_value=77), \
                mock.patch.object(app, "is_phone_window", return_value=False), \
                mock.patch.object(app, "window_process_name", return_value="chrome.exe"), \
                mock.patch.object(app, "window_title", return_value="Funny cats - YouTube - Google Chrome"):
            self.assertEqual(app.focus_tick(self.bridge, now=start + 60), "kept away")
            app.pc_windows.minimize.assert_called_once_with(77)
            self.assertEqual(app.focus_tick(self.bridge, now=start + 62), "")  # not again at once
            self.assertEqual(app.focus_tick(self.bridge, now=start + 601), "halfway")
        with mock.patch.object(app._user32_windows, "GetForegroundWindow", return_value=77), \
                mock.patch.object(app, "is_phone_window", return_value=False), \
                mock.patch.object(app, "window_process_name", return_value="code.exe"), \
                mock.patch.object(app, "window_title", return_value="motorcycle_man.py - VS Code"):
            self.assertEqual(app.focus_tick(self.bridge, now=start + 700), "")  # work is fine
        self.assertEqual(app.focus_tick(self.bridge, now=start + 1201), "done")
        self.assertIn("Focus done! 20 minutes", self.bridge.say_sig.emit.call_args.args[0])
        self.bridge.act_sig.emit.assert_called_with("done", {})
        self.assertEqual(app.PHONE_INBOX.take(0)[0]["kind"], "alert")
        self.assertEqual(app.FOCUS["done"], 1)
        self.assertEqual(app.stop_focus(), "No focus time is running.")

    def test_the_focus_panel_and_words(self):
        answer = self.get_json("/focus?do=start&minutes=45&block=0")
        self.assertEqual(answer["say"], "Focus started 💪")
        self.assertEqual((answer["minutes"], answer["block"]), (45, False))
        self.assertGreater(answer["left"], 2690)
        self.assertEqual(self.get_json("/focus?do=stop")["say"], "Focus stopped. Have a breather!")
        self.assertIn("Focus time! 30 minutes", app.focus_reply("focus for 30 minutes", self.bridge))
        self.assertEqual(app.focus_reply("stop focus", self.bridge), "Focus stopped. Have a breather!")
        self.assertIsNone(app.focus_reply("focus the camera", self.bridge))
        self.assertIn('id="focusModal"', app.PHONE_PAGE)

    def test_tricks(self):
        self.assertEqual(app.trick_reply("do a wheelie", self.bridge), "Wheelie time!")
        self.bridge.act_sig.emit.assert_called_with("wheelie", {})
        self.get_json("/focus?do=race")
        self.bridge.act_sig.emit.assert_called_with("race", {})
        self.assertIsNone(app.trick_reply("race to the store", self.bridge))
        with mock.patch.object(app.random, "choice", return_value="rev"):
            self.assertEqual(app.trick_reply("do a trick", self.bridge), "VROOM VROOM!")
        say_then_act = [c[0] for c in self.bridge.mock_calls if c[0] in ("say_sig.emit", "act_sig.emit")][-2:]
        self.assertEqual(say_then_act, ["say_sig.emit", "act_sig.emit"])  # out first, then the trick

    def test_the_face_follows_the_clock(self):
        self.assertEqual([app.clock_mood(h) for h in (1, 9, 12, 13, 14, 23)], ["sleeping", "idle", "cool", "cool", "idle", "idle"])
        self.assertIn("cool", app.helmet_face.MOODS)


class ListsAndGamePadTests(PhoneServer):
    def test_the_lists_panel_and_words(self):
        book = app.shared_lists.SharedLists(lambda: os.path.join(self.temp.name, "lists.json"))
        with mock.patch.object(app, "SHARED_LISTS", book):
            self.assertEqual(self.get_json("/lists")["lists"], {"Shopping": [], "To do": []})
            self.get_json("/lists?do=add&list=Shopping&text=milk")
            self.get_json("/lists?do=toggle&list=Shopping&text=milk")
            self.assertTrue(self.get_json("/lists")["lists"]["Shopping"][0]["done"])
            self.assertIn("Garage", self.get_json("/lists?do=new&list=garage")["lists"])
            app.GUEST_COMMAND.name = "guest-1"
            self.addCleanup(setattr, app.GUEST_COMMAND, "name", None)
            self.assertEqual(app.lists_reply("add tires to the garage list"), "Added tires to the garage list.")
            self.assertEqual(book.all()["Garage"][0]["by"], "a guest")
            self.assertEqual(app.lists_brief(), "On your lists: garage 1.")
            self.get_json("/lists?do=clear_done&list=Shopping")
            self.assertEqual(book.all()["Shopping"], [])
        self.assertIn('id="listsModal"', app.PHONE_PAGE)

    def test_the_game_pad_holds_keys_and_lets_go(self):
        with mock.patch.object(app.Win32Input, "key_down") as down, mock.patch.object(app.Win32Input, "key_up") as up, \
                mock.patch.object(app, "GAME_HOLD", 0.3), mock.patch.dict(app.GAMEPAD, {"held": {}, "watching": False}):
            self.assertEqual(self.get_json("/gamepad?do=down&key=w"), {"ok": True})
            self.get_json("/gamepad?do=down&key=w")  # still held: pressed only once
            self.assertEqual(self.get_json("/gamepad?do=down&key=f4"), {"ok": False})  # not a game key
            down.assert_called_once_with("w")
            self.get_json("/gamepad?do=up&key=w")
            up.assert_called_once_with("w")
            self.get_json("/gamepad?do=down&key=space")  # and then the phone goes quiet
            deadline = time.monotonic() + 3
            while up.call_count < 2 and time.monotonic() < deadline:
                time.sleep(0.05)
            self.assertEqual(up.call_args_list[-1], mock.call("space"))
            self.get_json("/gamepad?do=down&key=left")
            self.get_json("/gamepad?do=release")
            self.assertEqual(up.call_args_list[-1], mock.call("left"))
        self.assertIn('id="gameView"', app.PHONE_PAGE)


if __name__ == "__main__":
    unittest.main()
