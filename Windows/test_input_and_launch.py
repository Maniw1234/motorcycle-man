import mm_test_env  # noqa: F401 (first: the tests never touch the real settings and keys)
import ctypes
import os
import struct
import tempfile
import unittest
from unittest import mock

import motorcycle_man as app


class TypingTests(unittest.TestCase):
    def test_input_record_is_the_full_win32_size(self):
        expected = 40 if struct.calcsize("P") == 8 else 28
        self.assertEqual(ctypes.sizeof(app.INPUT), expected)

    def test_windows_accepts_the_input_record(self):
        # A relative mouse move of (0, 0) has no visible effect. Before the fix,
        # Windows rejected every record with error 87 and nothing was typed.
        noop = app.INPUT(type=0, u=app.INPUT_UNION(mi=app.MOUSEINPUT(0, 0, 0, 0x0001, 0, 0)))
        sent = app._user32_send.SendInput(1, ctypes.byref(noop), ctypes.sizeof(app.INPUT))
        self.assertEqual(sent, 1, f"SendInput failed with error {ctypes.get_last_error()}")

    def test_each_character_is_sent_as_one_unicode_down_and_up(self):
        with mock.patch.object(app.Win32Input, "send_inputs") as send, mock.patch.object(app.time, "sleep"):
            app.Win32Input.type_unicode_text("hé")
        batches = [call.args[0] for call in send.call_args_list]
        self.assertEqual(len(batches), 2)
        for batch, ch in zip(batches, "hé"):
            self.assertEqual([(e.type, e.u.ki.wScan, e.u.ki.dwFlags) for e in batch], [
                (app.INPUT_KEYBOARD, ord(ch), app.KEYEVENTF_UNICODE),
                (app.INPUT_KEYBOARD, ord(ch), app.KEYEVENTF_UNICODE | app.KEYEVENTF_KEYUP),
            ])

    def test_a_pictograph_is_sent_as_a_utf16_surrogate_pair(self):
        events = app.Win32Input.unicode_key_events("\U0001F600")
        self.assertEqual([(e.u.ki.wScan, e.u.ki.dwFlags) for e in events], [
            (0xD83D, app.KEYEVENTF_UNICODE),
            (0xD83D, app.KEYEVENTF_UNICODE | app.KEYEVENTF_KEYUP),
            (0xDE00, app.KEYEVENTF_UNICODE),
            (0xDE00, app.KEYEVENTF_UNICODE | app.KEYEVENTF_KEYUP),
        ])

    def test_line_breaks_press_enter_once(self):
        with mock.patch.object(app.Win32Input, "send_inputs") as send, \
                mock.patch.object(app.Win32Input, "press_key") as press, \
                mock.patch.object(app.time, "sleep"):
            app.Win32Input.type_unicode_text("a\r\nb")
        self.assertEqual(send.call_count, 2)
        press.assert_called_once_with("enter")

    def test_rejected_key_events_raise_instead_of_reporting_success(self):
        with mock.patch.object(app._user32_send, "SendInput", return_value=0):
            with self.assertRaises(OSError):
                app.Win32Input.send_inputs(app.Win32Input.unicode_key_events("a"))


class AppMatchingTests(unittest.TestCase):
    # Real shortcut names from this PC that partial matching used to pick by mistake.
    SHORTCUTS = {
        "android-studio-rabbit1-windows.exe": r"C:\Desk\android-studio-rabbit1-windows.exe.lnk",
        "jcmd": r"C:\Desk\BikeProject\Android\tools\jdk17\bin\jcmd.exe",
        "microg settings": r"C:\Desk\microG Settings.lnk",
        "youtube revanced": r"C:\Desk\YouTube ReVanced.lnk",
        "gemini": r"C:\Start\Gemini.lnk",
        "fl studio 21": r"C:\Start\FL Studio 21.lnk",
        "fl studio 21 (uninstall)": r"C:\Start\Uninstall FL Studio 21.lnk",
        "android studio": r"C:\Start\Android Studio.lnk",
        "android studio profiler": r"C:\Start\Android Studio Profiler.lnk",
    }

    def launch(self, target, start_apps=None):
        with mock.patch.object(app, "discover_installed_shortcuts", return_value=self.SHORTCUTS), \
                mock.patch.object(app, "discover_start_apps", return_value=start_apps or {}), \
                mock.patch.object(app.os, "startfile", create=True) as startfile, \
                mock.patch.object(app.webbrowser, "open") as browser:
            app.launch_any_app_or_target(target)
        return startfile, browser

    def test_installed_store_apps_open_instead_of_their_websites(self):
        start_apps = {"claude": "Claude_pzs8sxrjxfjjc!Claude", "whatsapp": "5319275A.WhatsAppDesktop_cv1g1gvanyjgm!App"}
        for target, app_id in (("claude", start_apps["claude"]), ("the claude app", start_apps["claude"]),
                               ("WhatsApp", start_apps["whatsapp"])):
            with self.subTest(target=target), mock.patch.object(app.subprocess, "Popen") as popen:
                startfile, browser = self.launch(target, start_apps)
                popen.assert_called_once_with(["explorer.exe", "shell:AppsFolder\\" + app_id])
                browser.assert_not_called()
                startfile.assert_not_called()

    def test_without_an_installed_app_the_website_still_opens(self):
        startfile, browser = self.launch("claude")
        browser.assert_called_once_with("https://claude.ai")

    def test_commands_about_the_screen_go_to_gemini_instead_of_launching_something(self):
        start_apps = {"gmail": "Chrome._crx_fmgjjmmmlfcabfkddbjimcfncm"}
        with mock.patch.object(app, "discover_installed_shortcuts", return_value=self.SHORTCUTS), \
                mock.patch.object(app, "discover_start_apps", return_value=start_apps), \
                mock.patch.object(app.os, "startfile", create=True) as startfile, \
                mock.patch.object(app.webbrowser, "open") as browser, \
                mock.patch.object(app.subprocess, "Popen") as popen, \
                mock.patch.object(app.Win32Input, "press_key") as press:
            for command in ("open the top email", "open the first result", "open settings in chrome", "open it"):
                with self.subTest(command=command):
                    self.assertIsNone(app.try_instant_local_command(command))
            startfile.assert_not_called()
            browser.assert_not_called()
            press.assert_not_called()
            self.assertEqual(app.try_instant_local_command("open gmail"), "Opened gmail.")
            popen.assert_called_once_with(["explorer.exe", "shell:AppsFolder\\Chrome._crx_fmgjjmmmlfcabfkddbjimcfncm"])

    def test_my_downloads_opens_the_downloads_folder(self):
        with tempfile.TemporaryDirectory() as downloads, \
                mock.patch.object(app, "get_known_folder_paths", return_value={"Downloads": downloads}), \
                mock.patch.object(app.subprocess, "Popen") as popen:
            self.assertEqual(app.try_instant_local_command("go to my downloads folder"), "Opened Downloads folder.")
        popen.assert_called_once_with(["explorer.exe", downloads])

    def test_builtins_are_not_hijacked_by_partial_shortcut_names(self):
        cases = {
            "x": ("browser", "https://x.com"),
            "youtube": ("browser", "https://www.youtube.com"),
            "cmd": ("startfile", "cmd.exe"),
            "settings": ("startfile", "ms-settings:"),
        }
        for target, (kind, dest) in cases.items():
            with self.subTest(target=target):
                startfile, browser = self.launch(target)
                called, other = (browser, startfile) if kind == "browser" else (startfile, browser)
                called.assert_called_once_with(dest)
                other.assert_not_called()

    def test_exact_shortcut_still_beats_a_builtin_website(self):
        startfile, browser = self.launch("gemini")
        startfile.assert_called_once_with(self.SHORTCUTS["gemini"])
        browser.assert_not_called()

    def test_partial_match_needs_whole_words_and_prefers_the_closest_name(self):
        cases = {
            "fl studio": "fl studio 21",
            "studio android": "android studio",
            "uninstall fl studio": "fl studio 21 (uninstall)",
            "cmd": None,
            "x": None,
            "tube": None,
        }
        for target, expected in cases.items():
            with self.subTest(target=target):
                self.assertEqual(app.best_shortcut_match(target, self.SHORTCUTS), expected)

    def test_desktop_is_scanned_only_at_its_top_level(self):
        with tempfile.TemporaryDirectory() as desktop, tempfile.TemporaryDirectory() as start_menu:
            os.makedirs(os.path.join(desktop, "BikeProject", "bin"))
            os.makedirs(os.path.join(start_menu, "Accessories"))
            for path in (
                os.path.join(desktop, "Spotify.lnk"),
                os.path.join(desktop, "BikeProject", "bin", "jcmd.exe"),
                os.path.join(start_menu, "Accessories", "Paint.lnk"),
            ):
                open(path, "w").close()
            found = app.discover_installed_shortcuts([desktop], [start_menu])
        self.assertEqual(sorted(found), ["paint", "spotify"])


if __name__ == "__main__":
    unittest.main()
