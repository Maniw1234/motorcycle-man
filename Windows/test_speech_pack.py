import mm_test_env  # noqa: F401 (first: the tests never touch the real settings and keys)
import hashlib
import io
import json
import os
import subprocess
import tempfile
import threading
import unittest
import wave
import zipfile
from unittest import mock

import speech_pack

FILES = ("whisper-cli.exe", "whisper.dll", "ggml.dll", "ggml-base.dll", "ggml-cpu.dll")
CLI_URL = "https://github.com/ggml-org/whisper.cpp/releases/download/v9.9.9/whisper-bin-x64.zip"


def zipped(extra=()) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        for name in FILES:
            archive.writestr(f"Release/{name}", f"pretend {name}")
        for name, data in extra:
            archive.writestr(name, data)
    return out.getvalue()


def a_second_of_wav(level=0) -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(int(level).to_bytes(2, "little", signed=True) * 16000)
    return out.getvalue()


class FakeReply:
    # An answer from the internet, in memory.
    def __init__(self, data: bytes, size=None):
        self.data, self.headers = io.BytesIO(data), {"Content-Length": str(len(data) if size is None else size)}

    def read(self, n=-1):
        return self.data.read(n)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


class FakeProcess:
    # Stands in for whisper-cli started by Popen: it answers what its FakeRun says, or (with hold) works until it
    # is told to finish or is killed. Nothing is started for real.
    def __init__(self, owner):
        self.owner, self.returncode, self.killed = owner, None, False

    def communicate(self, timeout=None):
        owner = self.owner
        if not self.killed and owner.hold is not None:
            owner.started.set()
            owner.hold.wait(5)
        if self.killed:
            self.returncode = 1
            return b"", b""
        if owner.error is not None:
            raise owner.error
        self.returncode = 0
        return owner.heard.encode("utf-8"), b""

    def kill(self):
        self.killed = True
        self.owner.kills.append(self)
        if self.owner.hold is not None:
            self.owner.hold.set()


class FakeRun:
    # Stands in for subprocess.run (Windows' voice "says" the check sentence into its file) and for subprocess.Popen
    # (popen: whisper-cli answers).
    def __init__(self, heard=" Hey Motorcycle Man, open Notepad.\n", error=None):
        self.heard, self.error, self.calls, self.kills = heard, error, [], []
        self.hold, self.started = None, threading.Event()

    def __call__(self, command, **kwargs):
        self.calls.append((command, kwargs))
        if command[0] == "powershell":
            with open(kwargs["env"]["MM_WAVE"], "wb") as f:
                f.write(a_second_of_wav(1000))
            return subprocess.CompletedProcess(command, 0, b"", b"")
        raise AssertionError("whisper-cli is started with Popen, so that it can be stopped")

    def popen(self, command, **kwargs):
        self.calls.append((command, kwargs))
        self.wav_path = command[command.index("-f") + 1]
        self.wav_existed = os.path.isfile(self.wav_path)
        return FakeProcess(self)


class SpeechPackTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = os.path.join(temp.name, "speech")
        self.zip_bytes, self.model_bytes = zipped([("../evil.dll", "nope"), ("Release/readme.txt", "hi")]), b"model" * 1000
        self.pack = dict(speech_pack.PACK, cli_url=CLI_URL, cli_sha256=hashlib.sha256(self.zip_bytes).hexdigest(),
                         model_commit="0" * 40, model_sha256=hashlib.sha256(self.model_bytes).hexdigest())
        self.served = {CLI_URL: self.zip_bytes, speech_pack.model_url(self.pack): self.model_bytes}
        self.run = FakeRun()

    def opener(self, request, timeout=None):
        self.assertTrue(request.full_url.startswith("https://"))
        return FakeReply(self.served[request.full_url])

    def make(self, **changes):
        return speech_pack.SpeechPack(lambda: self.root, dict(self.pack, **changes), self.opener, self.run, self.run.popen)

    def files(self) -> list:
        return [os.path.join(top, name) for top, _, names in os.walk(os.path.dirname(self.root)) for name in names]

    def test_not_offered_until_pinned(self):
        self.assertFalse(speech_pack.available(dict(speech_pack.PACK, cli_sha256="", model_sha256="")))
        self.assertTrue(speech_pack.available(self.pack))
        self.assertFalse(speech_pack.available(dict(self.pack, cli_url="https://example.com/whisper.zip")))
        with self.assertRaises(speech_pack.PackError):
            self.make(model_sha256="").install()
        self.assertEqual(self.files(), [])

    def test_set_up_tried_and_removed(self):
        pack, seen = self.make(), []
        info = pack.install(lambda done, total: seen.append(done), lambda: seen.append("checking"))
        self.assertTrue(pack.installed() and pack.ready())
        folder = pack.folder()
        self.assertEqual(sorted(os.listdir(folder)), sorted(FILES + ("ggml-base.en-q5_1.bin", "pack.json")))
        with open(os.path.join(folder, "pack.json"), encoding="utf-8") as f:
            self.assertEqual(json.load(f), info)
        self.assertEqual(info["tag"], self.pack["tag"])
        self.assertIn("checking", seen)
        self.assertFalse([path for path in self.files() if ".part" in path or "evil" in path or "readme" in path])
        pack.remove()
        self.assertFalse(pack.installed())
        self.assertFalse(os.path.exists(self.root))

    def test_a_download_that_doesnt_match_is_never_used(self):
        self.served[speech_pack.model_url(self.pack)] = b"something else" * 1000
        with self.assertRaises(speech_pack.PackError) as said:
            self.make().install()
        self.assertIn("checksum", str(said.exception))
        self.assertEqual(self.files(), [])

    def test_a_download_far_bigger_than_it_should_be_is_stopped(self):
        with self.assertRaises(speech_pack.PackError) as said:
            self.make(cli_limit=100).install()
        self.assertIn("far bigger", str(said.exception))
        self.assertEqual(self.files(), [])

    def test_only_the_listed_files_and_each_by_name_alone(self):
        short = io.BytesIO()
        with zipfile.ZipFile(short, "w") as archive:
            archive.writestr("../whisper-cli.exe", "only this")  # saved by its name alone, inside the pack
            archive.writestr("whisper-cli.exe", "a second copy is skipped")
        self.served[CLI_URL] = short.getvalue()
        with self.assertRaises(speech_pack.PackError) as said:
            self.make(cli_sha256=hashlib.sha256(short.getvalue()).hexdigest()).install()
        self.assertIn("missing", str(said.exception))
        self.assertEqual(self.files(), [])
        self.served[CLI_URL] = b"PK not really a zip"
        with self.assertRaises(speech_pack.PackError):
            self.make(cli_sha256=hashlib.sha256(self.served[CLI_URL]).hexdigest()).install()
        self.assertEqual(self.files(), [])

    def test_writing_down_a_recording(self):
        pack = self.make()
        pack.install()
        self.run.heard = " Open notepad.\n"
        self.assertEqual(pack.transcribe(a_second_of_wav(2000), "Motorcycle Man, Spotify", 0.9), "Open notepad.")
        command, kwargs = self.run.calls[-1]
        for flag in ("-m", "-f", "-nt", "-np", "--prompt"):
            self.assertIn(flag, command)
        self.assertEqual(command[command.index("-l") + 1], "en")
        self.assertEqual(command[command.index("--prompt") + 1], "Motorcycle Man, Spotify")
        self.assertEqual(kwargs["creationflags"], speech_pack.CREATE_NO_WINDOW)
        self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)
        self.assertTrue(self.run.wav_existed)
        self.assertFalse(os.path.exists(self.run.wav_path))  # the temporary recording is gone
        with self.assertRaises(speech_pack.PackError):
            pack.transcribe(b"OggS a voice message")

    def test_made_up_words_and_notes_are_dropped(self):
        self.assertEqual(speech_pack.clean_words("[BLANK_AUDIO]"), "")
        self.assertEqual(speech_pack.clean_words(" (music) "), "")
        self.assertEqual(speech_pack.clean_words("Thank you.", 0.4), "")
        self.assertEqual(speech_pack.clean_words("Thank you.", 2.0), "Thank you.")
        self.assertEqual(speech_pack.clean_words("Open notepad.", 0.4), "Open notepad.")
        self.assertEqual(speech_pack.clean_words("[typing] Open notepad."), "Open notepad.")

    def test_a_pack_that_fails_rests_and_the_recording_is_still_deleted(self):
        pack = self.make()
        pack.install()
        self.run.error = subprocess.TimeoutExpired("whisper-cli.exe", 11.5)
        with self.assertRaises(speech_pack.PackError) as said:
            pack.transcribe(a_second_of_wav(2000))
        self.assertEqual(str(said.exception), "sharp ears took too long")
        self.assertEqual(len(self.run.kills), 1)  # whisper-cli doesn't go on by itself
        self.assertFalse(os.path.exists(self.run.wav_path))
        self.assertTrue(pack.sick())
        self.assertFalse(pack.ready())

    def writing_down(self, pack, job=None):
        # transcribe on a thread of its own, with whisper-cli working until told: (thread, what it ended with).
        self.run.hold, self.run.started = threading.Event(), threading.Event()
        ended = {}

        def work():
            try:
                ended["words"] = pack.transcribe(a_second_of_wav(2000), "", None, job=job)
            except speech_pack.PackError as exc:
                ended["error"] = exc

        thread = threading.Thread(target=work, daemon=True)
        thread.start()
        self.assertTrue(self.run.started.wait(5))
        return thread, ended

    def test_one_recording_at_a_time(self):
        # A second recording while sharp ears work on one goes to the next way at once, rather than waiting.
        pack = self.make()
        pack.install()
        thread, ended = self.writing_down(pack)
        with self.assertRaises(speech_pack.PackBusy) as said:
            pack.transcribe(a_second_of_wav(2000))
        self.assertEqual(str(said.exception), "sharp ears are busy with another recording")
        self.assertIsInstance(said.exception, speech_pack.PackError)  # hear() moves on to the next way
        self.assertFalse(pack.sick())
        self.run.hold.set()
        thread.join(5)
        self.assertEqual(ended, {"words": "Hey Motorcycle Man, open Notepad."})
        self.assertEqual(self.run.kills, [])
        self.assertTrue(pack.ready())

    def test_a_stopped_task_stops_whisper(self):
        pack = self.make()
        pack.install()
        job = speech_pack.Job()
        thread, ended = self.writing_down(pack, job)
        pack.cancel(speech_pack.Job())  # another recording's: nothing happens
        self.assertEqual(self.run.kills, [])
        pack.cancel(job)
        thread.join(5)
        self.assertEqual(len(self.run.kills), 1)
        self.assertIsInstance(ended.get("error"), speech_pack.PackCancelled)
        self.assertEqual(str(ended["error"]), "sharp ears were stopped")
        self.assertFalse(pack.sick())  # stopped, not broken
        self.assertFalse(os.path.exists(self.run.wav_path))
        second, ended = self.writing_down(pack)  # free for the next recording
        self.run.hold.set()
        second.join(5)
        self.assertEqual(ended, {"words": "Hey Motorcycle Man, open Notepad."})
        # A job cancelled before whisper-cli starts never starts it.
        early = speech_pack.Job()
        pack.cancel(early)
        self.run.hold = None
        calls = len(self.run.calls)
        with self.assertRaises(speech_pack.PackCancelled):
            pack.transcribe(a_second_of_wav(2000), job=early)
        self.assertEqual(len(self.run.calls), calls)


if __name__ == "__main__":
    unittest.main()
