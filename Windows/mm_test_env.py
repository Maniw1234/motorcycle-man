# Imported first by every test file: the tests get a throwaway Motorcycle Man folder, so they never read or change
# the real settings and keys. In October 2026 a test read the real Gemini key, and its failure message showed it.
import atexit
import os
import shutil
import tempfile

if not os.environ.get("MM_TEST_APPDATA"):
    _folder = tempfile.mkdtemp(prefix="mm-tests-")
    os.environ["MM_TEST_APPDATA"] = os.environ["APPDATA"] = _folder
    atexit.register(shutil.rmtree, _folder, True)
