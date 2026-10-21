import mm_test_env  # noqa: F401 (first: the tests never touch the real settings and keys)
import ctypes
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

from PySide6.QtCore import Qt
from PySide6.QtNetwork import QLocalSocket
from PySide6.QtWidgets import QApplication

import keep_running
import motorcycle_man as app

QT = QApplication.instance() or QApplication([])
WM_CLOSE = 0x0010


class TempAppData(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        patcher = mock.patch.dict(os.environ, {"APPDATA": self.temp.name})
        patcher.start()
        self.addCleanup(patcher.stop)


class StaysByTheClockTests(TempAppData):
    def window(self):
        window = app.ControlCenterWindow("127.0.0.1", app.Bridge(), lambda text: None, token="test-token")
        window.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)  # never on the real screen
        window.tray = mock.Mock()
        self.addCleanup(window.deleteLater)
        window.show()
        QT.processEvents()
        return window

    def test_the_x_puts_it_by_the_clock(self):
        window = self.window()
        ctypes.windll.user32.SendMessageW(ctypes.c_void_p(int(window.winId())), WM_CLOSE, 0, 0)  # as the × does
        QT.processEvents()
        self.assertFalse(window.isVisible())
        window.tray.showMessage.assert_called_once()
        self.assertIn("Right-click my icon to quit", window.tray.showMessage.call_args.args[1])
        self.assertTrue(app.load_settings()["told_about_tray"])
        window.show()
        ctypes.windll.user32.SendMessageW(ctypes.c_void_p(int(window.winId())), WM_CLOSE, 0, 0)
        QT.processEvents()
        self.assertFalse(window.isVisible())
        self.assertEqual(window.tray.showMessage.call_count, 1)  # told once

    def test_quit_still_closes_it(self):
        window = self.window()
        self.assertTrue(window.close())  # Quit (and Windows shutting down) close it for real
        self.assertFalse(window.isVisible())
        window.tray.showMessage.assert_not_called()

    def test_after_a_task_it_goes_back_behind(self):
        window = self.window()
        with mock.patch.object(ctypes.windll.user32, "SetWindowPos") as place:
            window.step_aside()
            self.assertFalse(window.isVisible())
            window.come_back()
            self.assertTrue(window.isVisible())
        self.assertEqual(place.call_args.args[1].value, 1)  # HWND_BOTTOM: behind the app the task worked in
        self.assertEqual(place.call_args.args[6], 0x0013)  # and the keyboard stays there

    def test_in_front_it_comes_back_in_front(self):
        window = self.window()
        with mock.patch.object(app._user32_windows, "GetForegroundWindow", return_value=int(window.winId())), \
                mock.patch.object(ctypes.windll.user32, "SetWindowPos") as place:
            window.step_aside()
            window.come_back()
        self.assertTrue(window.isVisible())
        place.assert_not_called()

    def test_put_away_it_stays_away(self):
        window = self.window()
        window.hide()  # by the clock
        window.step_aside()
        window.come_back()
        self.assertFalse(window.isVisible())
        window.show()
        window.showMinimized()
        QT.processEvents()
        window.step_aside()
        self.assertTrue(window.isVisible())  # minimized windows are left on the taskbar
        window.come_back()
        self.assertTrue(window.isMinimized())

    def test_started_with_windows_again(self):
        window, bridge = mock.Mock(), mock.Mock()
        with mock.patch.object(app, "instance_name", return_value=f"mm-test-tray-{os.getpid()}"):
            server = app.listen_for_another_start(window, bridge)
            self.addCleanup(server.close)
            for message in (b"tray", b"show"):
                socket = QLocalSocket()
                socket.connectToServer(app.instance_name())
                self.assertTrue(socket.waitForConnected(1000))
                socket.write(message)
                socket.waitForBytesWritten(1000)
                socket.disconnectFromServer()
                deadline = time.monotonic() + 1.5
                while time.monotonic() < deadline and not window.showNormal.called:
                    QT.processEvents()
                if message == b"tray":
                    window.showNormal.assert_not_called()  # it stays where it is
        window.showNormal.assert_called_once_with()  # opened by hand: it comes forward


class StartWithWindowsTests(unittest.TestCase):
    def setUp(self):
        self.registry = mock.MagicMock()
        for patcher in (mock.patch("winreg.OpenKey", return_value=self.registry),
                        mock.patch("winreg.SetValueEx"), mock.patch("winreg.DeleteValue")):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_the_built_app_starts_by_the_clock(self):
        import winreg
        env = {k: v for k, v in os.environ.items() if k != "MOTORCYCLE_MAN_PORT"}
        with mock.patch.object(sys, "frozen", True, create=True), \
                mock.patch.object(sys, "executable", r"C:\Users\Sam\Desktop\Motorcycle Man.exe"), \
                mock.patch.dict(os.environ, env, clear=True):
            self.assertTrue(app.set_start_with_windows(True))
            winreg.SetValueEx.assert_called_once_with(self.registry.__enter__.return_value, "Motorcycle Man", 0, winreg.REG_SZ,
                                                      '"C:\\Users\\Sam\\Desktop\\Motorcycle Man.exe" --tray')
            self.assertTrue(app.set_start_with_windows(False))
            winreg.DeleteValue.assert_called_once_with(self.registry.__enter__.return_value, "Motorcycle Man")
        self.assertEqual(winreg.OpenKey.call_args.args[1], r"Software\Microsoft\Windows\CurrentVersion\Run")

    def test_never_a_test_copy_or_the_source(self):
        import winreg
        with mock.patch.object(sys, "frozen", True, create=True), mock.patch.dict(os.environ, {"MOTORCYCLE_MAN_PORT": "18080"}):
            self.assertFalse(app.set_start_with_windows(True))
        self.assertFalse(app.set_start_with_windows(True))  # run from the source
        winreg.OpenKey.assert_not_called()


class OneCopyTests(unittest.TestCase):
    def test_a_second_start_finds_the_first(self):
        with mock.patch.object(app, "instance_name", return_value=f"mm-test-claim-{os.getpid()}"), \
                mock.patch.dict(app.THIS_COPY):
            self.assertTrue(app.claim_this_copy())  # nothing else running
            first = app.THIS_COPY["claim"]
            self.addCleanup(app._kernel32_windows.CloseHandle, first)
            self.assertFalse(app.claim_this_copy())  # as a second Motorcycle Man would see it
            self.addCleanup(app._kernel32_windows.CloseHandle, app.THIS_COPY["claim"])

    def test_the_port_is_its_own(self):
        handler = app.make_phone_handler(mock.Mock(), 1000, 800)
        server = app.PhoneServer(("127.0.0.1", 0), handler)
        self.addCleanup(server.server_close)
        port = server.server_address[1]
        with self.assertRaises(OSError):  # a second copy, even one that asks to share, is turned away
            sharing = type("Sharing", (app.http.server.ThreadingHTTPServer,), {"allow_reuse_address": True})
            sharing(("127.0.0.1", port), handler).server_close()
        self.assertEqual(server.request_queue_size, 64)

    def test_a_taken_port_is_said_and_tried_again(self):
        bridge = mock.Mock()
        attempts = []

        def opened(*args):
            attempts.append(args)
            if len(attempts) < 3:
                raise OSError(10048, "in use")
            raise KeyboardInterrupt  # stands for the door opening, and ends the test
        with mock.patch.object(app, "PhoneServer", side_effect=opened), self.assertRaises(KeyboardInterrupt):
            app.serve_phone(bridge, 1000, 800, wait=0)
        self.assertEqual(len(attempts), 3)
        bridge.say_sig.emit.assert_called_once()  # said once, not every try
        self.assertIn("Another program is using port", bridge.say_sig.emit.call_args.args[0])


class KeepRunningTests(TempAppData):
    def setUp(self):
        super().setUp()
        os.makedirs(os.path.join(self.temp.name, "Motorcycle Man"))
        self.start = mock.Mock()

    def ended(self, code, now, shutting_down=False):
        return keep_running.after_exit(code, now=now, shutting_down=lambda: shutting_down, start=self.start)

    def log(self):
        with open(os.path.join(self.temp.name, "Motorcycle Man", "motorcycle_man.log"), encoding="utf-8") as f:
            return f.read()

    def test_what_counts_as_a_crash(self):
        self.assertTrue(keep_running.crashed(0xC0000005))  # access violation
        self.assertTrue(keep_running.crashed(-1073741819))  # the same, as a signed number
        self.assertTrue(keep_running.crashed(0xC00000FD))  # stack overflow
        for code in (0, 1, 0x40010004):  # Quit, Task Manager, Windows ending it
            self.assertFalse(keep_running.crashed(code))

    def test_back_after_a_crash(self):
        with mock.patch.dict(os.environ, {"_PYI_APPLICATION_HOME_DIR": r"C:\Temp\_MEI1", "_MEIPASS2": r"C:\Temp\_MEI1"}):
            self.assertTrue(self.ended(0xC0000005, now=1000))
        command, env = self.start.call_args.args
        self.assertEqual(command, [sys.executable, "--tray", "--restarted"])
        self.assertEqual(env["PYINSTALLER_RESET_ENVIRONMENT"], "1")  # unpacked afresh, not in the crashed copy's folder
        self.assertFalse([name for name in env if name.upper().startswith(("_PYI_", "_MEIPASS"))])
        self.assertIn("Motorcycle Man crashed (0xC0000005)", self.log())
        self.assertIn("Started Motorcycle Man again", self.log())

    def test_not_after_quit_or_shutdown(self):
        self.assertFalse(self.ended(0, now=1000))
        self.assertFalse(self.ended(1, now=1000))
        self.assertFalse(self.ended(0xC0000005, now=1000, shutting_down=True))
        self.start.assert_not_called()

    def test_at_most_three_in_ten_minutes(self):
        self.assertEqual([self.ended(0xC0000005, now=t) for t in (1000, 1100, 1200, 1300)], [True, True, True, False])
        self.assertIn("it crashed 3 times in 10 minutes", self.log())
        self.assertTrue(self.ended(0xC0000005, now=1601))  # the first one is more than 10 minutes ago

    def test_the_watcher_waits_for_the_app_to_end(self):
        child = subprocess.Popen([sys.executable, "-c", "import os, time; time.sleep(0.4); os._exit(-1073741819)"])
        self.addCleanup(child.wait)
        with mock.patch.object(keep_running, "after_exit") as ended:
            self.assertEqual(keep_running.main(["Motorcycle Man.exe", "--keep-running", str(child.pid)]), 0)
        ended.assert_called_once_with(0xC0000005)
        self.assertEqual(keep_running.main(["Motorcycle Man.exe", "--keep-running", "nonsense"]), 2)

    def test_the_watcher_starts_without_the_rest_of_the_app(self):
        here = os.path.dirname(os.path.abspath(app.__file__))
        finished = subprocess.run([sys.executable, "-X", "importtime", os.path.join(here, "motorcycle_man.py"), "--keep-running", "0"],
                                  cwd=here, capture_output=True, text=True, timeout=60)
        self.assertEqual(finished.returncode, 1)  # no process 0 to watch
        self.assertIn("keep_running", finished.stderr)
        self.assertNotIn("PySide6", finished.stderr)  # Qt and the rest never load
        self.assertNotIn("PIL", finished.stderr)


class CrashLogTests(TempAppData):
    def setUp(self):
        super().setUp()
        for name in ("excepthook",):
            self.addCleanup(setattr, sys, name, getattr(sys, name))
        self.addCleanup(setattr, threading, "excepthook", threading.excepthook)
        patcher = mock.patch.object(app.faulthandler, "enable")
        self.enable = patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(lambda: app.CRASHES["file"] and app.CRASHES["file"].close())

    def test_errors_nobody_caught_are_written_down(self):
        app.watch_for_crashes()
        self.assertTrue(self.enable.call_args.args[0].name.endswith("crash.log"))
        with self.assertLogs(app.LOG, "ERROR") as said:
            sys.excepthook(ValueError, ValueError("broken slot"), None)
            job = threading.Thread(target=lambda: 1 / 0, name="guard_loop")
            job.start()
            job.join()
        self.assertIn("Uncaught error", said.output[0])
        self.assertIn("A background job stopped (guard_loop)", said.output[1])

    def test_the_next_start_knows_whether_it_closed_properly(self):
        with self.assertNoLogs(app.LOG, "WARNING"):
            app.watch_for_crashes()
        app.CRASHES["file"].close()
        with self.assertLogs(app.LOG, "WARNING") as said:
            app.watch_for_crashes()  # started again without closing: a crash, Task Manager or the power
        self.assertIn("didn't close properly", said.output[0])
        app.CRASHES["file"].close()
        app.closed_properly()
        with self.assertNoLogs(app.LOG, "WARNING"):
            app.watch_for_crashes()

    def test_only_the_built_app_starts_the_watcher(self):
        with mock.patch.object(app.keep_running, "start_detached") as start:
            self.assertIsNone(app.keep_running_after_a_crash())
            start.assert_not_called()
            with mock.patch.object(sys, "frozen", True, create=True):
                app.keep_running_after_a_crash()
        command, env = start.call_args.args
        self.assertEqual(command, [sys.executable, "--keep-running", str(os.getpid())])
        self.assertEqual(env["PYINSTALLER_RESET_ENVIRONMENT"], "1")


class QuietTests(TempAppData):
    def test_news_from_the_buddy_or_from_windows(self):
        overlay, tray = mock.Mock(), mock.Mock()
        tray.supportsMessages.return_value = True
        app.show_notice("📱 WhatsApp · Mom: Coming?", 9000, overlay, tray)
        overlay.say.assert_called_once_with("📱 WhatsApp · Mom: Coming?", 9000)
        app.save_setting("quiet", True)
        app.show_notice("📱 WhatsApp · Mom: Coming?", 9000, overlay, tray)
        tray.showMessage.assert_called_once_with("Motorcycle Man", "📱 WhatsApp · Mom: Coming?",
                                                 app.QSystemTrayIcon.MessageIcon.Information, 9000)
        self.assertEqual(overlay.say.call_count, 1)
        tray.supportsMessages.return_value = False  # Windows can't show it: the buddy does after all
        app.show_notice("Pixel is connected!", 5000, overlay, tray)
        self.assertEqual(overlay.say.call_count, 2)

    def test_news_goes_the_quiet_way_and_warnings_stay_loud(self):
        bridge = mock.Mock()
        app.greet_phone(bridge, "Pixel 10 Pro")
        bridge.notice_sig.emit.assert_called_once_with("Pixel 10 Pro is connected!", 5000)
        with mock.patch.dict(app.POWER_HOOK, {"bridge": bridge}), mock.patch.dict(app.TASK_STATUS):
            app.power_warning("Shutting down in 1 minute.")
        bridge.say_sig.emit.assert_called_once_with("⏻ Shutting down in 1 minute.", 30000)  # always the buddy

    def test_quiet_from_the_tray(self):
        from PySide6.QtWidgets import QMenu
        menu = QMenu()
        app.fill_tray_menu(menu, mock.Mock(), mock.Mock(), 1000, 800)
        quiet = next(a for a in menu.actions() if a.text() == "Quiet")
        self.assertFalse(quiet.isChecked())
        quiet.trigger()
        self.assertTrue(app.load_settings()["quiet"])


class PhoneMenuTests(unittest.TestCase):
    def test_most_used_first(self):
        page = app.ANDROID_WEB_CLIENT
        self.assertIn('<div class="remote" id="menuFavs" style="display:none"></div>', page)
        self.assertIn("function openMenu() { menuMoreOpen = false; arrangeMenu();", page)
        self.assertIn("(use[tileKey(t)] || 0) >= 2)", page)  # tapped at least twice
        self.assertIn(".slice(0, 6)", page)


class HardeningTests(TempAppData):
    def test_where_a_request_came_from(self):
        self.assertEqual(app.request_route("192.168.1.20", {}), "")
        self.assertEqual(app.request_route("127.0.0.1", {}), "")
        self.assertEqual(app.request_route("127.0.0.1", {"Cf-Connecting-Ip": "203.0.113.9"}), "internet")  # Cloudflare's link
        self.assertEqual(app.request_route("127.0.0.1", {"X-Forwarded-For": "203.0.113.9"}), "internet")  # ngrok's
        self.assertEqual(app.request_route("8.8.8.8", {}), "internet")  # straight from outside the home network
        self.assertEqual(app.request_route(app.bluetooth_bridge.SOURCE, {}), "bluetooth")

    def test_a_question_from_the_internet_says_so(self):
        dialog = app.PairDialog("r1", "Pixel", "4821", "123456", "internet")
        self.addCleanup(dialog.deleteLater)
        self.assertIn("came over the internet", dialog.warning.text())
        home = app.PairDialog("r2", "Pixel", "4821", "123456", "")
        self.addCleanup(home.deleteLater)
        self.assertFalse(hasattr(home, "warning"))

    def test_strange_numbers_from_the_phone(self):
        for value in ("inf", "-inf", "nan", "1e999", "abc", ""):
            app.phone_battery_heard({"battery": [value]})  # no error
        with mock.patch.object(app, "set_master_volume") as volume, mock.patch.object(app, "master_volume", return_value=None), \
                mock.patch.object(app.pc_mixer, "apps", return_value=[]):
            app.mixer_request({"do": ["master"], "volume": ["1e999"]})  # said, not crashed
            volume.assert_not_called()
            app.mixer_request({"do": ["master"], "volume": ["250"]})
            volume.assert_called_once_with(100)
        with mock.patch.object(app, "start_focus") as focus:
            app.focus_request({"do": ["start"], "minutes": ["inf"]}, mock.Mock())
        self.assertEqual(focus.call_args.args[0], 25)

    def test_no_invisible_direction_marks_in_file_names(self):
        self.assertEqual(app.safe_file_name("photo\u202egpj.exe"), "photogpj.exe")  # shows what it really is
        self.assertEqual(app.safe_file_name("a\u200bb\u2066c.txt"), "abc.txt")

    def test_every_answer_keeps_the_link_secret_to_itself(self):
        server = app.PhoneServer(("127.0.0.1", 0), app.make_phone_handler(mock.Mock(), 1000, 800))
        self.addCleanup(server.server_close)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.shutdown)
        import urllib.request
        with urllib.request.urlopen(f"http://127.0.0.1:{server.server_address[1]}/hello", timeout=5) as reply:
            headers = reply.headers
        self.assertEqual(headers["Referrer-Policy"], "no-referrer")
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(headers["X-Frame-Options"], "SAMEORIGIN")

    def test_no_hidden_characters_in_the_source(self):
        import glob
        here = os.path.dirname(os.path.abspath(app.__file__))
        for path in glob.glob(os.path.join(here, "*.py")) + glob.glob(os.path.join(here, "..", "Android", "src", "**", "*.java"), recursive=True):
            with open(path, encoding="utf-8-sig") as f:
                text = f.read()
            hidden = [c for c in text if 0x202A <= ord(c) <= 0x202E or 0x2066 <= ord(c) <= 0x2069 or ord(c) in (0x200E, 0x200F)]
            self.assertEqual(hidden, [], os.path.basename(path))


if __name__ == "__main__":
    unittest.main()
