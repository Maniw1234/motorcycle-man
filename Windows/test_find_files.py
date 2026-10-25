import mm_test_env  # noqa: F401 (first: the tests never touch the real settings and keys)
import os
import tempfile
import time
import unittest
from unittest import mock

import find_files

NOW = time.mktime((2026, 10, 20, 14, 0, 0, 0, 0, -1))  # a Tuesday


class UnderstandTests(unittest.TestCase):
    def test_words_kinds_and_times(self):
        q = find_files.understand("find my tax return PDF from last week", NOW)
        self.assertEqual((q["words"], q["kind"], q["newest"]), (["tax", "return"], "pdf", True))
        self.assertEqual(q["since"], time.mktime((2026, 10, 12, 0, 0, 0, 0, 0, -1)))  # last Monday but one
        q = find_files.understand("photos from yesterday", NOW)
        self.assertEqual((q["words"], q["kind"]), ([], "photo"))
        self.assertEqual(q["since"], time.mktime((2026, 10, 19, 0, 0, 0, 0, 0, -1)))
        self.assertEqual(find_files.understand("budget 2025 spreadsheets", NOW)["words"], ["budget", "2025"])
        self.assertEqual(find_files.understand("my latest screenshot", NOW)["words"], ["screenshot"])
        self.assertTrue(find_files.understand("my latest screenshot", NOW)["newest"])
        self.assertEqual(find_files.describe(find_files.understand("the powerpoint about mars")), "presentations named “mars”")


class SearchTests(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.TemporaryDirectory()
        self.addCleanup(self.home.cleanup)
        self.put("Documents/Jobs/Resume 2026.docx", age_days=3)
        self.put("Documents/Jobs/old resume.pdf", age_days=400)
        self.put("Desktop/resume notes.txt", age_days=1)
        self.put("Documents/code/build/resume.pdf", age_days=0)  # a build folder: noise
        self.put("Documents/code/resume_test.py", age_days=0)
        self.put("Pictures/Screenshots/Screenshot 2026-10-19.png", age_days=1)
        self.put("Pictures/beach.jpg", age_days=30)
        os.makedirs(os.path.join(self.home.name, "Documents", "Taxes 2025"))
        for patcher in (mock.patch.object(find_files, "_from_index", side_effect=OSError("no index")),  # looked through by hand
                        mock.patch.dict(os.environ, {"OneDrive": ""})):
            patcher.start()
            self.addCleanup(patcher.stop)

    def put(self, relative, age_days):
        path = os.path.join(self.home.name, *relative.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(b"x" * 10)
        stamp = time.time() - age_days * 86400
        os.utime(path, (stamp, stamp))

    def names(self, text, **kw):
        return [r["name"] for r in find_files.search(find_files.understand(text), home=self.home.name, **kw)]

    def test_by_name_best_first(self):
        self.assertEqual(self.names("resume"), ["resume notes.txt", "Resume 2026.docx", "old resume.pdf", "resume_test.py"])
        self.assertEqual(self.names("resume pdf"), ["old resume.pdf"])
        self.assertEqual(self.names("my latest screenshot"), ["Screenshot 2026-10-19.png"])
        self.assertEqual(self.names("photos"), ["Screenshot 2026-10-19.png", "beach.jpg"])
        self.assertEqual(self.names("taxes folder"), ["Taxes 2025"])
        self.assertEqual(self.names("the"), [])

    def test_what_a_result_says(self):
        found = find_files.search(find_files.understand("resume docx"), home=self.home.name)[0]
        self.assertEqual((found["name"], found["folder"], found["size"], found["is_folder"]),
                         ("Resume 2026.docx", os.path.join("Documents", "Jobs"), 10, False))

    def test_the_index_first(self):
        path = os.path.join(self.home.name, "Desktop", "resume notes.txt")
        with mock.patch.object(find_files, "_from_index", return_value=[(path, True)]) as index:
            self.assertEqual(self.names("resume"), ["resume notes.txt"])
        self.assertEqual(index.call_args.args[0]["words"], ["resume"])

    def test_sql_words_are_plain(self):
        self.assertEqual(find_files._sql_text("o'brien%_\"x"), "obrien_x")


if __name__ == "__main__":
    unittest.main()
