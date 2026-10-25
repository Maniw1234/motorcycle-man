import mm_test_env  # noqa: F401 (first: the tests never touch the real settings and keys)
import io
import json
import os
import tempfile
import unittest
import zipfile
from unittest import mock

import motorcycle_man as app

DOMAIN = "bike-example.ngrok-free.app"


class FakeNgrok:
    # Stands in for ngrok's program: writes to its log what the real one does.
    def __init__(self, lines):
        self.lines, self.pid, self.killed, self.args, self.env = lines, 4242, False, None, None

    def __call__(self, args, **kwargs):
        self.args, self.env = args, kwargs.get("env", {})
        with open(args[args.index("--log") + 1], "w", encoding="utf-8") as f:
            f.write("\n".join(self.lines))
        return self

    def poll(self):
        return 1 if any("ERR_NGROK" in line for line in self.lines) else None

    def kill(self):
        self.killed = True


class FixedLinkTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        patcher = mock.patch.dict(os.environ, {"APPDATA": self.temp.name})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_the_fixed_link_is_set_with_a_domain_and_an_authtoken(self):
        self.assertIsNone(app.fixed_address())
        app.save_setting("ngrok_domain", DOMAIN)
        self.assertIsNone(app.fixed_address())  # still needs the authtoken
        app.save_key("ngrok_authtoken", "2abc_token_from_the_dashboard")
        self.assertEqual(app.fixed_address(), (DOMAIN, "2abc_token_from_the_dashboard"))
        self.assertEqual(app.clean_domain("https://Bike-Example.ngrok-free.app/"), DOMAIN)
        for good in (DOMAIN, "bike.ngrok.app", "x.ngrok.dev"):
            self.assertTrue(app.NGROK_DOMAIN.fullmatch(good), good)
        for bad in ("evil.com", "ngrok-free.app.evil.com", ""):
            self.assertFalse(app.NGROK_DOMAIN.fullmatch(bad), bad)

    def test_only_a_download_signed_by_ngrok_is_kept(self):
        def download(request, part, progress, limit, what):
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, "w") as archive:
                archive.writestr("ngrok.exe", b"MZ program")
            with open(part, "wb") as f:
                f.write(buffer.getvalue())

        for signed in (False, True):
            with mock.patch.object(app, "download_file", side_effect=download), \
                    mock.patch.object(app, "signed_by", return_value=signed) as check:
                if signed:
                    self.assertEqual(app.get_ngrok(), app.ngrok_path())
                    with open(app.ngrok_path(), "rb") as f:
                        self.assertEqual(f.read(), b"MZ program")
                else:
                    with self.assertRaises(RuntimeError):
                        app.get_ngrok()
                    self.assertFalse(os.path.exists(app.ngrok_path()))
            self.assertEqual(check.call_args.args[1], "ngrok, Inc")
        self.assertEqual(os.listdir(os.path.dirname(app.ngrok_path())), ["ngrok.exe"])  # nothing half-done left

    def test_opening_and_failing(self):
        link = app.InternetLink()
        os.makedirs(os.path.dirname(app.ngrok_path()), exist_ok=True)
        started = FakeNgrok(['{"lvl":"info","msg":"started tunnel","url":"https://' + DOMAIN + '"}'])
        with mock.patch.object(app, "get_ngrok", return_value=app.ngrok_path()), mock.patch.object(app.subprocess, "Popen", started), \
                mock.patch.object(app.time, "sleep"):
            self.assertEqual(link._new_ngrok(DOMAIN, "secret-token"), f"https://{DOMAIN}")
        self.assertEqual(started.args[started.args.index("--url") + 1], f"https://{DOMAIN}")
        self.assertEqual(started.env["NGROK_AUTHTOKEN"], "secret-token")
        self.assertNotIn("secret-token", started.args)  # never on its command line
        with open(link._ngrok_saved_path(), encoding="utf-8") as f:
            self.assertEqual(json.load(f), {"pid": 4242, "address": f"https://{DOMAIN}"})
        refused = FakeNgrok(['{"lvl":"crit","msg":"command failed","err":"authentication failed ERR_NGROK_105"}'])
        with mock.patch.object(app, "get_ngrok", return_value=app.ngrok_path()), mock.patch.object(app.subprocess, "Popen", refused), \
                mock.patch.object(app.time, "sleep"):
            with self.assertRaises(RuntimeError) as failed:
                link._new_ngrok(DOMAIN, "bad")
        self.assertIn("authtoken", str(failed.exception))
        self.assertIn("isn't yours", app.ngrok_problem("ERR_NGROK_320"))

    def test_away_from_home_uses_the_fixed_link_when_it_is_set(self):
        link = app.InternetLink()
        shown = []
        link.changed.connect(lambda state, detail: shown.append((state, detail)))
        with mock.patch.object(link, "_new_tunnel", return_value="https://words.trycloudflare.com"), \
                mock.patch.object(link, "_running_tunnel", return_value=None), \
                mock.patch.object(link, "_new_ngrok", return_value=f"https://{DOMAIN}") as ngrok, \
                mock.patch.object(link, "_running_ngrok", return_value=None):
            link._open()
            self.assertEqual(link.address, "https://words.trycloudflare.com")  # no fixed link yet: Cloudflare
            app.save_setting("ngrok_domain", DOMAIN)
            app.save_key("ngrok_authtoken", "secret-token")
            link._open()
        ngrok.assert_called_once_with(DOMAIN, "secret-token")
        self.assertEqual((link.state, link.address), ("on", f"https://{DOMAIN}"))
        self.assertIn(("opening", "Opening your fixed address through ngrok..."), shown)


if __name__ == "__main__":
    unittest.main()
