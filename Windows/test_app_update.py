import mm_test_env  # noqa: F401 (first: the tests never touch the real settings and keys)
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from PySide6.QtWidgets import QApplication, QMenu

import app_update
import motorcycle_man as app

QT = QApplication.instance() or QApplication([])
NEW_EXE = b"MZ the new Motorcycle Man"
RELEASE_JSON = {"tag_name": "v1.2", "draft": False, "prerelease": False, "body": "A faster screen view",
                "assets": [{"name": "MotorcycleMan.exe", "size": len(NEW_EXE),
                            "browser_download_url": "https://github.com/Maniw1234/motorcycle-man/releases/download/v1.2/MotorcycleMan.exe"},
                           {"name": "SHA256SUMS.txt", "size": 100,
                            "browser_download_url": "https://github.com/Maniw1234/motorcycle-man/releases/download/v1.2/SHA256SUMS.txt"},
                           {"name": "MotorcycleMan.apk", "size": 5,
                            "browser_download_url": "https://github.com/Maniw1234/motorcycle-man/releases/download/v1.2/MotorcycleMan.apk"}]}


class Reply(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def web(files):
    # Stands in for urlopen: answers from {url ending: bytes}.
    def opener(request, timeout=None):
        url = request.full_url
        for ending, body in files.items():
            if url.endswith(ending):
                return Reply(body)
        raise OSError(f"not found: {url}")
    return opener


def sums(body=NEW_EXE):
    return f"{hashlib.sha256(body).hexdigest()}  MotorcycleMan.exe\n{'0' * 64}  MotorcycleMan.apk\n".encode()


class ReleaseTests(unittest.TestCase):
    def test_the_newest_release(self):
        release = app_update.latest_release(opener=web({"/releases/latest": json.dumps(RELEASE_JSON).encode()}))
        self.assertEqual((release["version"], release["tag"], release["notes"]), ("1.2", "v1.2", "A faster screen view"))
        self.assertTrue(app_update.newer(release, "1.1"))
        self.assertTrue(app_update.newer(release, "1.0.9"))
        self.assertFalse(app_update.newer(release, "1.2"))
        self.assertFalse(app_update.newer(release, "1.10"))  # numbers, not letters: 1.10 is newer than 1.2
        self.assertTrue(app_update.newer(release, ""))  # a copy made on a PC
        self.assertFalse(app_update.newer({}, ""))
        self.assertNotIn(" ", app_update.EXE_ASSET)  # GitHub turns spaces in release file names into dots

    def test_releases_that_dont_count(self):
        for change in ({"draft": True}, {"prerelease": True}, {"tag_name": "release-42"}, {"tag_name": "latest"}, {"assets": RELEASE_JSON["assets"][1:]}):
            with self.subTest(change=change):
                data = dict(RELEASE_JSON, **change)
                self.assertEqual(app_update.latest_release(opener=web({"/releases/latest": json.dumps(data).encode()})), {})
        self.assertEqual(app_update.latest_release(opener=web({})), {})  # GitHub out of reach

    def test_own_version(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "version.json")
            self.assertEqual(app_update.own_version(path), "")  # none: made on a PC
            for written, read in (({"version": "1.1", "commit": "abc"}, "1.1"), ({"version": "nonsense"}, ""), ({"build": 9}, "")):
                with open(path, "w") as f:
                    json.dump(written, f)
                self.assertEqual(app_update.own_version(path), read)


class DownloadTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.folder, True)
        self.release = app_update.latest_release(opener=web({"/releases/latest": json.dumps(RELEASE_JSON).encode()}))

    def test_a_checked_download(self):
        path = app_update.download_update(self.release, self.folder, web({"MotorcycleMan.exe": NEW_EXE, "SHA256SUMS.txt": sums()}))
        with open(path, "rb") as f:
            self.assertEqual(f.read(), NEW_EXE)
        self.assertEqual(os.path.basename(path), "Motorcycle Man 1.2.exe")

    def test_a_download_that_doesnt_match_is_thrown_away(self):
        with self.assertRaises(app_update.UpdateError) as said:
            app_update.download_update(self.release, self.folder, web({"MotorcycleMan.exe": b"MZ something else",
                                                                       "SHA256SUMS.txt": sums()}))
        self.assertIn("didn't match", str(said.exception))
        self.assertEqual(os.listdir(self.folder), [])  # nothing left behind

    def test_only_from_github(self):
        elsewhere = dict(self.release, exe_url="https://example.com/Motorcycle Man.exe")
        with self.assertRaises(app_update.UpdateError):
            app_update.download_update(elsewhere, self.folder, web({}))

    def test_too_big_is_stopped(self):
        with mock.patch.object(app_update, "DOWNLOAD_LIMIT", 10):
            with self.assertRaises(app_update.UpdateError) as said:
                app_update.download_update(self.release, self.folder, web({"MotorcycleMan.exe": NEW_EXE, "SHA256SUMS.txt": sums()}))
        self.assertIn("bigger", str(said.exception))


class InstallTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.folder, True)
        self.current = os.path.join(self.folder, "Motorcycle Man.exe")
        self.new = os.path.join(self.folder, "updates", "Motorcycle Man 1.2.exe")
        os.makedirs(os.path.dirname(self.new))
        with open(self.current, "wb") as f:
            f.write(b"MZ old")
        with open(self.new, "wb") as f:
            f.write(NEW_EXE)

    def test_the_new_one_takes_the_old_ones_place(self):
        launch = mock.Mock()
        code = app_update.finish_update(["x", "--finish-update", self.current, "123"], source=self.new,
                                        wait=lambda pid, seconds: True, launch=launch)
        self.assertEqual(code, 0)
        with open(self.current, "rb") as f:
            self.assertEqual(f.read(), NEW_EXE)
        with open(self.current + ".old", "rb") as f:
            self.assertEqual(f.read(), b"MZ old")  # kept until the next start
        launch.assert_called_once_with(self.current)
        app_update.clean_up(self.current, os.path.dirname(self.new))
        self.assertFalse(os.path.exists(self.current + ".old"))
        self.assertEqual(os.listdir(os.path.dirname(self.new)), [])

    def test_nothing_is_touched_while_the_old_one_runs(self):
        launch = mock.Mock()
        code = app_update.finish_update(["x", "--finish-update", self.current, "123"], source=self.new,
                                        wait=lambda pid, seconds: False, launch=launch)
        self.assertEqual(code, 3)
        with open(self.current, "rb") as f:
            self.assertEqual(f.read(), b"MZ old")
        launch.assert_not_called()
        self.assertEqual(app_update.finish_update(["x", "--finish-update"]), 2)

    def test_the_new_one_starts_as_its_own_app(self):
        with mock.patch("subprocess.Popen") as popen, \
                mock.patch.dict(os.environ, {"_PYI_APPLICATION_HOME_DIR": r"C:\Temp\_MEI1"}):
            app_update.start_install(self.new, self.current, 4321)
            app_update.start_updated(self.current)
        (helper, helper_kw), (updated, updated_kw) = [(c.args[0], c.kwargs) for c in popen.call_args_list]
        self.assertEqual(helper, [self.new, "--finish-update", self.current, "4321"])
        self.assertEqual(updated, [self.current, "--tray", "--updated"])  # back in the tray, like the one it replaced
        for env in (helper_kw["env"], updated_kw["env"]):
            self.assertEqual(env["PYINSTALLER_RESET_ENVIRONMENT"], "1")
            self.assertNotIn("_PYI_APPLICATION_HOME_DIR", env)

    def test_the_exe_hands_over_before_loading_anything(self):
        here = os.path.dirname(os.path.abspath(app.__file__))
        finished = subprocess.run([sys.executable, "-X", "importtime", os.path.join(here, "motorcycle_man.py"), "--finish-update"],
                                  cwd=here, capture_output=True, text=True, timeout=60)
        self.assertEqual(finished.returncode, 2)  # no arguments to act on
        self.assertIn("app_update", finished.stderr)
        self.assertNotIn("PySide6", finished.stderr)


class AppUpdateFlowTests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.dict(app.UPDATE, {"release": {}, "busy": False})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.release = app_update.latest_release(opener=web({"/releases/latest": json.dumps(RELEASE_JSON).encode()}))

    def test_an_update_is_said_once_and_offered_in_the_menu(self):
        bridge = mock.Mock()
        with mock.patch.object(app_update, "latest_release", return_value=self.release), mock.patch.object(app, "this_version", return_value="1.1"):
            app.check_for_update(bridge)
            app.check_for_update(bridge)
        bridge.notice_sig.emit.assert_called_once()
        self.assertIn("Motorcycle Man 1.2 is ready", bridge.notice_sig.emit.call_args.args[0])
        menu = QMenu()
        app.fill_tray_menu(menu, mock.Mock(), bridge, 1000, 800)
        self.assertEqual(menu.actions()[0].text(), "Install update (1.2)")

    def test_no_update_when_this_is_the_newest(self):
        bridge = mock.Mock()
        with mock.patch.object(app_update, "latest_release", return_value=self.release), mock.patch.object(app, "this_version", return_value="1.2"):
            app.check_for_update(bridge)
        bridge.notice_sig.emit.assert_not_called()
        self.assertEqual(app.UPDATE["release"], {})

    def test_installing_closes_this_copy_for_the_new_one(self):
        bridge = mock.Mock()
        app.UPDATE["release"] = self.release
        with mock.patch.object(app_update, "download_update", return_value="C:/new.exe"), \
                mock.patch.object(app_update, "start_install") as start:
            app.install_update(bridge)
        start.assert_called_once()
        bridge.update_quit_sig.emit.assert_called_once_with()
        with mock.patch.object(app_update, "download_update", side_effect=app_update.UpdateError("didn't match")):
            app.install_update(bridge)
        self.assertIn("didn't work", bridge.say_sig.emit.call_args.args[0])

    def test_never_from_the_source_or_a_test_copy(self):
        self.assertFalse(app.updates_itself())  # run from the source
        with mock.patch.object(sys, "frozen", True, create=True), mock.patch.dict(os.environ, {"MOTORCYCLE_MAN_PORT": "18080"}):
            self.assertFalse(app.updates_itself())


if __name__ == "__main__":
    unittest.main()
