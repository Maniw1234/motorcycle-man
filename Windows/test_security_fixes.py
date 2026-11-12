import mm_test_env  # noqa: F401 (first: the tests never touch the real settings and keys)
import hashlib
import hmac
import http.client
import json
import os
import socket
import tempfile
import threading
import unittest
from unittest import mock

import motorcycle_man as app

# The fixes from the November 2026 security check.


class FilePathTests(unittest.TestCase):
    def check(self, action):
        return app.validate_action(action, 960, 540)

    def test_no_streams_or_device_paths(self):
        for path in (r"C:\Users\x\notes.bat::$DATA", r"C:\Users\x\a.txt:hidden", r"\\?\C:\Users\x\a.txt",
                     r"\\.\C:\Users\x\a.txt"):
            with self.subTest(path=path), self.assertRaisesRegex(ValueError, "plain file path"):
                self.check({"type": "write_file", "path": path, "content": "x"})
        self.check({"type": "write_file", "path": r"C:\Users\x\notes.txt", "content": "x"})

    def test_its_own_folder_is_off_limits(self):
        own = app.app_data_dir()
        for action in ({"type": "open_path", "path": os.path.join(own, "settings.json")},
                       {"type": "write_file", "path": os.path.join(own, "x.txt"), "content": "x"},
                       {"type": "delete_file", "path": os.path.dirname(own)},  # the folder that holds it
                       {"type": "copy_file", "source": os.path.join(own, "settings.json"),
                        "destination": r"C:\Users\x\copy.json"}):
            with self.subTest(action=action), self.assertRaisesRegex(ValueError, "own folder"):
                self.check(action)
        self.check({"type": "open_path", "path": os.path.dirname(own)})  # only looking at the folder above

    def test_what_runs_a_program(self):
        for name in ("a.bat", "a.BAT.", "a.bat ", "a.bat::$DATA", "a.pyz", "report.docm", "x.settingcontent-ms"):
            self.assertTrue(app.runs_a_program("C:\\x\\" + name), name)
        self.assertFalse(app.runs_a_program(r"C:\x\notes.txt"))

    def test_network_places_and_startup_ask_first(self):
        with mock.patch.object(app.os.path, "isdir") as isdir, mock.patch.object(app.os.path, "exists") as exists:
            reason = app.risky_action_reason
            self.assertRegex(reason({"type": "copy_file", "source": r"C:\Users\x\a.txt",
                                     "destination": r"\\evil.example\share\a.txt"}), "network place")
            self.assertRegex(reason({"type": "open_path", "path": "//evil.example/share"}), "network place")
            self.assertRegex(reason({"type": "create_folder", "path": r"\\evil.example\share\new"}), "network place")
            self.assertRegex(reason({"type": "write_file", "path": r"\\evil.example\share\a.txt", "content": "x"}),
                             "network place")
            self.assertRegex(reason({"type": "delete_file", "path": r"\\evil.example\share\a.txt"}), "Delete")
            isdir.assert_not_called()  # asked before Windows reaches for that computer
            exists.assert_not_called()
        startup = r"C:\Users\x\AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Startup\notes.txt"
        self.assertRegex(app.risky_action_reason({"type": "write_file", "path": startup, "content": "x"}), "sign in")

    def test_a_guest_s_file_actions_always_ask(self):
        action = {"type": "open_path", "path": r"C:\Users\x\Documents"}
        self.assertIsNone(app.risky_action_reason(action))
        app.GUEST_COMMAND.name = "Dana"
        self.addCleanup(setattr, app.GUEST_COMMAND, "name", None)
        self.assertRegex(app.risky_action_reason(action), r"Dana \(a guest\) wants to open path")
        self.assertRegex(app.risky_action_reason({"type": "copy_file", "source": r"C:\Users\x\a.txt",
                                                  "destination": r"C:\Users\x\b.txt"}), "a guest")
        self.assertIsNone(app.risky_action_reason({"type": "scroll", "direction": "down"}))

    def test_file_explorer_asks_for_what_it_would_run(self):
        with mock.patch.object(app, "foreground_window_kind", return_value="File Explorer"), \
                tempfile.TemporaryDirectory() as folder:
            reason = lambda text: app.risky_action_reason({"type": "type_text", "text": text})
            self.assertRegex(reason("powershell"), "File Explorer")
            self.assertRegex(reason(r"C:\Users\x\run.bat"), "File Explorer")
            self.assertIsNone(reason(folder))  # going to a folder
            self.assertIsNone(reason("holiday photos"))  # a search


class WindowKindTests(unittest.TestCase):
    def kind(self, program, class_name="SomeWindow", title=""):
        def named(hwnd, buffer, size):
            buffer.value = class_name

        def titled(hwnd, buffer, size):
            buffer.value = title

        with mock.patch.object(app, "_user32_windows") as user32, \
                mock.patch.object(app, "window_process_name", return_value=program):
            user32.GetForegroundWindow.return_value = 1
            user32.GetClassNameW.side_effect = named
            user32.GetWindowTextW.side_effect = titled
            return app.foreground_window_kind()

    def test_windows_are_known_by_their_program_too(self):
        self.assertEqual(self.kind("pwsh.exe"), "terminal")
        self.assertEqual(self.kind("explorer.exe", "#32770", "Ausführen"), "Run box")  # any language
        self.assertEqual(self.kind("taskmgr.exe"), "Run box")
        self.assertEqual(self.kind("searchhost.exe"), "Start search")
        self.assertEqual(self.kind("explorer.exe", "CabinetWClass", "Documents"), "File Explorer")
        self.assertIsNone(self.kind("notepad.exe", "Notepad", "Untitled"))


class PhoneDoorTests(unittest.TestCase):
    TOKEN = "test-link-secret"

    def setUp(self):
        patcher = mock.patch.object(app, "PHONE_SESSION_TOKEN", self.TOKEN)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.server = app.PhoneServer(("127.0.0.1", 0), app.make_phone_handler(mock.Mock(), 1000, 800))
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.port = self.server.server_address[1]

    def test_hello_proves_it_holds_the_secret_without_telling_it(self):
        nonce = "0123456789abcdef0123456789abcdef"
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        self.addCleanup(connection.close)
        connection.request("GET", "/hello?nonce=" + nonce)
        raw = connection.getresponse().read().decode()
        proof = hmac.new(self.TOKEN.encode(), f"motorcycle-man-hello|{nonce}|127.0.0.1".encode(), hashlib.sha256)
        self.assertEqual(json.loads(raw)["proof"], proof.hexdigest())
        self.assertNotIn(self.TOKEN, raw)
        self.assertNotEqual(app.hello_info(nonce, "192.168.1.9")["proof"], proof.hexdigest())  # tied to the address
        for bad in ("", "xyz", nonce.upper(), nonce + "0"):
            self.assertNotIn("proof", app.hello_info(bad, "127.0.0.1"))

    def test_a_get_with_a_body_is_refused_and_hung_up_on(self):
        # Left unread on a kept connection, the body would be read as the next request.
        smuggled = f"GET /power?do=lock&token={self.TOKEN} HTTP/1.1\r\nHost: x\r\n\r\n".encode()
        for header in (f"Content-Length: {len(smuggled)}", "Transfer-Encoding: chunked"):
            with self.subTest(header=header), socket.create_connection(("127.0.0.1", self.port), timeout=5) as sock:
                sock.sendall(f"GET /hello HTTP/1.1\r\nHost: x\r\n{header}\r\n\r\n".encode() + smuggled)
                data = b""
                while True:
                    chunk = sock.recv(4096)
                    if not chunk:
                        break
                    data += chunk
                self.assertTrue(data.startswith(b"HTTP/1.1 400 "), data[:40])
                self.assertEqual(data.count(b"HTTP/1.1 "), 1)  # the second "request" was never answered


if __name__ == "__main__":
    unittest.main()
