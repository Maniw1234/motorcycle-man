# Sharp ears: free speech-to-text that runs on this PC, for what is said into its microphone (the phone's own speech
# recognizer keeps listening on the phone). Motorcycle Man doesn't carry it: only when the user asks ("Get sharper
# ears" in the window) are whisper.cpp (MIT licensed) and its English "base" model, about 60 MB together, downloaded
# from the addresses in PACK and checked against the SHA-256 values in PACK, into %APPDATA%\Motorcycle Man\speech.
# A recording is then written to a temporary WAV file, whisper-cli writes down the words, and the file is deleted:
# nothing leaves the PC. A newer whisper.cpp or model is a change to PACK, never a download of "the latest".
import hashlib
import io
import json
import os
import posixpath
import re
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.request
import wave
import zipfile

CREATE_NO_WINDOW = 0x08000000
USER_AGENT = "MotorcycleMan-speech"
# The pinned pack. Both SHA-256 values and the model's Hugging Face commit are written in when a version of Motorcycle
# Man starts offering the pack (with the exact file names in the release zip, and the flags checked against that
# whisper-cli's --help); until all three are pinned, available() is False and the pack isn't offered at all,
# rather than downloaded unchecked.
PACK = {
    "version": 1,
    "tag": "whisper-1.8.2-base.en-q5_1",  # its folder under speech\
    "cli_url": "https://github.com/ggml-org/whisper.cpp/releases/download/v1.8.2/whisper-bin-x64.zip",
    "cli_sha256": "",
    "cli_limit": 40 << 20,  # bytes: the zip is a few MB, so a far bigger download is stopped
    "files": ("whisper-cli.exe", "whisper.dll", "ggml.dll", "ggml-base.dll", "ggml-cpu.dll"),
    "model": "ggml-base.en-q5_1.bin",
    "model_repo": "https://huggingface.co/ggerganov/whisper.cpp",
    "model_commit": "",
    "model_sha256": "",
    "model_limit": 80 << 20,  # the model is about 57 MB
}
CHECK_WORDS = "Hey Motorcycle Man, open Notepad."  # said by Windows' voice into a file, to try the new ears
SICK_SECONDS = 600  # after a failure the next way writes things down for 10 minutes
BUSY_WAIT_SECONDS = 30  # how long trying new ears waits for a recording being written down
# Bracketed notes ("[BLANK_AUDIO]", "(music)", "*laughs*"), and what Whisper is known to make up from a moment of
# sound, which is dropped when less than 0.6 s of talking was heard.
NOTES = re.compile(r"\[[^\]]*\]|\([^)]*\)|\*[^*]*\*|♪[^♪]*♪?")
INVENTED = {"thank you", "thank you so much", "thanks", "thanks for watching", "thank you for watching",
            "thank you so much for watching", "you", "bye", "bye bye"}
SPEAK_TO_FILE = r"""
Add-Type -AssemblyName System.Speech
$voice = New-Object System.Speech.Synthesis.SpeechSynthesizer
$format = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(16000, [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, [System.Speech.AudioFormat.AudioChannel]::Mono)
$voice.SetOutputToWaveFile($env:MM_WAVE, $format)
$voice.Speak($env:MM_WORDS)
$voice.Dispose()
"""


class PackError(RuntimeError):
    pass


class PackBusy(PackError):
    # Sharp ears are writing down another recording; this one goes to the next way rather than waiting.
    pass


class PackCancelled(PackError):
    # The task that wanted the words was stopped, and whisper-cli with it.
    pass


class Job:
    # One recording being written down, so cancel() stops that one and never a newer one.
    def __init__(self):
        self.cancelled = False


def available(pack=None) -> bool:
    # Whether this version of Motorcycle Man has a pack it can check: both downloads and the model's commit pinned.
    pack = PACK if pack is None else pack
    sha = re.compile(r"[0-9a-f]{64}")
    return bool(sha.fullmatch(pack.get("cli_sha256", "")) and sha.fullmatch(pack.get("model_sha256", ""))
                and re.fullmatch(r"[0-9a-f]{40}", pack.get("model_commit", ""))
                and str(pack.get("cli_url", "")).startswith("https://github.com/"))


def model_url(pack) -> str:
    # The model at one commit of its repository, so the same address always means the same file.
    return f"{pack['model_repo']}/resolve/{pack['model_commit']}/{pack['model']}"


def wav_seconds(wav_bytes: bytes):
    # How long a WAV recording is, or None for anything that isn't one.
    try:
        with wave.open(io.BytesIO(wav_bytes)) as w:
            return w.getnframes() / float(w.getframerate() or 1)
    except (wave.Error, EOFError, ValueError):
        return None


def clean_words(text: str, speech_seconds=None) -> str:
    # whisper-cli's answer as plain words: notes in brackets dropped, and "" when nothing real is left.
    words = " ".join(NOTES.sub(" ", str(text)).split()).strip(" -")
    if not re.search(r"[^\W\d_]", words):
        return ""
    if speech_seconds is not None and speech_seconds < 0.6 and " ".join(re.findall(r"[a-z']+", words.lower())) in INVENTED:
        return ""
    return words[:500]


def threads() -> int:
    # Half of the PC's cores, 2 to 4: enough to answer quickly, while the screen view and Windows stay smooth.
    return min(4, max(2, (os.cpu_count() or 2) // 2))


def speak_to_wav(words: str, path: str, run=subprocess.run) -> bool:
    # Windows' own voice saying words into a 16 kHz mono WAV file; False when Windows has no voice to say them.
    try:
        result = run(["powershell", "-NoProfile", "-NonInteractive", "-Command", SPEAK_TO_FILE],
                     env=dict(os.environ, MM_WAVE=path, MM_WORDS=words), capture_output=True, timeout=30,
                     creationflags=CREATE_NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0 and os.path.isfile(path) and os.path.getsize(path) > 1000


def silent_wav(path: str, seconds: float = 1.0):
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(bytes(int(16000 * seconds) * 2))


class SpeechPack:
    def __init__(self, root, pack=None, opener=None, run=None, popen=None):
        self.root = root  # a function giving the speech folder (%APPDATA%\Motorcycle Man\speech)
        self.pack = PACK if pack is None else pack
        self.opener = opener or urllib.request.urlopen
        self.run = run or subprocess.run  # Windows' voice, for the check after setting up
        self.popen = popen or subprocess.Popen  # whisper-cli, kept so that cancel() can stop it
        self.lock = threading.Lock()  # held while the pack is set up or removed
        self.busy = threading.Lock()  # held while whisper-cli runs (a recording never waits for it)
        self.guard = threading.Lock()  # for process and job
        self.process, self.job = None, None
        self.sick_until = 0.0
        self.problem = ""

    def folder(self) -> str:
        return os.path.join(self.root(), self.pack["tag"])

    def info(self) -> dict:
        # pack.json, written last when the pack was set up; {} when it is missing or from another pack.
        try:
            with open(os.path.join(self.folder(), "pack.json"), encoding="utf-8") as f:
                info = json.load(f)
        except (OSError, ValueError):
            return {}
        if not isinstance(info, dict) or any(info.get(name) != self.pack[name]
                                             for name in ("tag", "cli_sha256", "model_sha256")):
            return {}
        return info

    def installed(self) -> bool:
        folder = self.folder()
        return available(self.pack) and bool(self.info()) and all(
            os.path.isfile(os.path.join(folder, name)) for name in tuple(self.pack["files"]) + (self.pack["model"],))

    def sick(self) -> bool:
        return time.monotonic() < self.sick_until and self.installed()

    def ready(self) -> bool:
        # Whether a recording can be written down here now (checked on every use: antivirus may take a file away).
        return time.monotonic() >= self.sick_until and self.installed()

    def transcribe(self, wav_bytes: bytes, prompt: str = "", speech_seconds=None, job=None) -> str:
        # The words in a 16 kHz WAV recording ("" when nobody spoke). prompt: names the speaker may say, which
        # Whisper then expects. job: a Job, for cancel(). One recording at a time: while another is written down,
        # PackBusy at once (the next way takes it). A failure (PackError) rests the pack for SICK_SECONDS; being
        # busy or cancelled doesn't.
        seconds = wav_seconds(wav_bytes)
        if seconds is None:
            raise PackError("sharp ears only take WAV recordings")
        if not self.busy.acquire(blocking=False):
            raise PackBusy("sharp ears are busy with another recording")
        handle, path = None, ""
        try:
            handle, path = tempfile.mkstemp(suffix=".wav")
            with os.fdopen(handle, "wb") as f:
                f.write(wav_bytes)
            words = self._words(self.folder(), path, prompt, seconds, job or Job())
        except (PackBusy, PackCancelled):
            raise
        except PackError as exc:
            self.sick_until, self.problem = time.monotonic() + SICK_SECONDS, str(exc)
            raise
        except OSError as exc:
            raise PackError(f"the recording couldn't be saved ({exc})") from None
        finally:
            self.busy.release()
            if path:
                try:
                    os.remove(path)
                except OSError:
                    pass
        return clean_words(words, speech_seconds)

    def cancel(self, job=None):
        # Stops writing down job's recording (or whatever is being written down): whisper-cli is killed, and
        # transcribe raises PackCancelled. Nothing happens when job is already done.
        with self.guard:
            if job is not None:
                job.cancelled = True
            process = self.process if job is None or self.job is job else None
        if process is not None:
            try:
                process.kill()
            except OSError:
                pass

    def _words(self, folder: str, wav_path: str, prompt: str, seconds: float, job=None) -> str:
        job = job or Job()
        command = [os.path.join(folder, "whisper-cli.exe"), "-m", os.path.join(folder, self.pack["model"]),
                   "-f", wav_path, "-l", "en", "-nt", "-np", "-t", str(threads())]
        if prompt:
            command += ["--prompt", " ".join(str(prompt).split())[:400]]
        with self.guard:
            if job.cancelled:
                raise PackCancelled("sharp ears were stopped")
            try:
                process = self.popen(command, cwd=folder, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, creationflags=CREATE_NO_WINDOW)
            except OSError as exc:
                raise PackError(f"sharp ears couldn't start ({exc})") from None
            self.process, self.job = process, job
        try:
            try:
                out, _ = process.communicate(timeout=10 + 1.5 * seconds)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate()
                if job.cancelled:
                    raise PackCancelled("sharp ears were stopped") from None
                raise PackError("sharp ears took too long") from None
        finally:
            with self.guard:
                self.process, self.job = None, None
        if job.cancelled:
            raise PackCancelled("sharp ears were stopped")
        if process.returncode != 0:
            raise PackError(f"sharp ears stopped with error {process.returncode}")
        return " ".join((out or b"").decode("utf-8", "replace").split())

    def install(self, progress=lambda done, total: None, checking=lambda: None) -> dict:
        # Downloads, checks and tries the pack (Get sharper ears, or Repair); returns pack.json's contents. Any
        # failure raises PackError and leaves the pack that was there, if any, as it was.
        if not available(self.pack):
            raise PackError("this version of Motorcycle Man has no speech pack to offer")
        if not self.lock.acquire(blocking=False):
            raise PackError("it's already being set up")
        try:
            root = self.root()
            os.makedirs(root, exist_ok=True)
            folder, work = self.folder(), self.folder() + ".part"
            shutil.rmtree(work, ignore_errors=True)
            os.makedirs(work)
            try:
                got = {"cli": 0, "model": 0}
                sizes = {"cli": 3 << 20, "model": 58 << 20}  # until the server says, roughly what they are

                def report(which):
                    def tell(done, total):
                        got[which], sizes[which] = done, total or sizes[which]
                        progress(got["cli"] + got["model"], sizes["cli"] + sizes["model"])
                    return tell

                zip_path = os.path.join(work, "whisper.zip")
                self._fetch(self.pack["cli_url"], zip_path, self.pack["cli_sha256"], self.pack["cli_limit"], report("cli"))
                self._unpack(zip_path, work)
                os.remove(zip_path)
                self._fetch(model_url(self.pack), os.path.join(work, self.pack["model"]), self.pack["model_sha256"],
                            self.pack["model_limit"], report("model"))
                checking()
                # Never beside a recording being written down: one whisper-cli at a time, the one cancel() stops.
                if not self.busy.acquire(timeout=BUSY_WAIT_SECONDS):
                    raise PackError("sharp ears were busy writing down what you said, so try again in a moment")
                try:
                    speed = self._check(work)
                finally:
                    self.busy.release()
                old = folder + ".old"
                shutil.rmtree(old, ignore_errors=True)
                if os.path.isdir(folder):
                    os.replace(folder, old)  # a pack being repaired stays until the new one is in place
                try:
                    os.replace(work, folder)
                except OSError:
                    if os.path.isdir(old):
                        os.replace(old, folder)
                    raise
                shutil.rmtree(old, ignore_errors=True)
                info = {"version": self.pack["version"], "tag": self.pack["tag"], "cli_sha256": self.pack["cli_sha256"],
                        "model_sha256": self.pack["model_sha256"], "speed": speed, "checked": int(time.time())}
                part = os.path.join(folder, "pack.json.part")
                with open(part, "w", encoding="utf-8") as f:
                    json.dump(info, f, indent=1)
                os.replace(part, os.path.join(folder, "pack.json"))  # last: until it is there, nothing uses the pack
                for name in os.listdir(root):  # an earlier pack, from before this version pinned a newer one
                    if name != self.pack["tag"]:
                        shutil.rmtree(os.path.join(root, name), ignore_errors=True)
                self.sick_until, self.problem = 0.0, ""
                return info
            except OSError as exc:
                raise PackError(f"its files couldn't be saved ({exc})") from None
            finally:
                shutil.rmtree(work, ignore_errors=True)
        finally:
            self.lock.release()

    def remove(self):
        # The window's Remove: the whole speech folder goes.
        with self.lock:
            shutil.rmtree(self.root(), ignore_errors=True)
            self.sick_until, self.problem = 0.0, ""
        if self.installed():
            raise PackError("Windows is still using its files; try again in a moment")

    def _fetch(self, url: str, path: str, sha256: str, limit: int, report):
        digest, done = hashlib.sha256(), 0
        try:
            with self.opener(urllib.request.Request(url, headers={"User-Agent": USER_AGENT}), timeout=60) as reply, \
                    open(path, "wb") as out:
                headers = getattr(reply, "headers", None)
                size = int(headers.get("Content-Length") or 0) if headers is not None else 0
                if size > limit:
                    raise PackError("the download is far bigger than it should be")
                while True:
                    chunk = reply.read(1 << 20)
                    if not chunk:
                        break
                    done += len(chunk)
                    if done > limit:
                        raise PackError("the download is far bigger than it should be")
                    digest.update(chunk)
                    out.write(chunk)
                    report(done, size)
        except (OSError, ValueError) as exc:
            raise PackError(f"the download didn't finish ({exc})") from None
        if digest.hexdigest() != sha256:
            raise PackError("the download didn't match its checksum, so it wasn't used")

    def _unpack(self, zip_path: str, folder: str):
        # Only the files the pack lists, each once, saved by name alone (whatever folder the zip keeps them in).
        wanted, found = set(self.pack["files"]), set()
        try:
            with zipfile.ZipFile(zip_path) as archive:
                for entry in archive.infolist():
                    name = posixpath.basename(entry.filename.replace("\\", "/"))
                    if entry.is_dir() or name not in wanted or name in found:
                        continue
                    if entry.file_size > self.pack["cli_limit"]:
                        raise PackError(f"{name} is far bigger than it should be")
                    with archive.open(entry) as source, open(os.path.join(folder, name), "wb") as out:
                        shutil.copyfileobj(source, out)
                    found.add(name)
        except (zipfile.BadZipFile, OSError) as exc:
            raise PackError(f"the download couldn't be opened ({exc})") from None
        missing = wanted - found
        if missing:
            raise PackError("the download is missing " + ", ".join(sorted(missing)))

    def _check(self, folder: str) -> float:
        # Tries the new ears on Windows' voice saying CHECK_WORDS (or on a second of silence when Windows has no
        # voice); returns how long they took per second of sound.
        handle, path = tempfile.mkstemp(suffix=".wav")
        os.close(handle)
        try:
            spoken = speak_to_wav(CHECK_WORDS, path, self.run)
            if not spoken:
                silent_wav(path)
            with open(path, "rb") as f:
                seconds = wav_seconds(f.read()) or 1.0
            began = time.monotonic()
            words = self._words(folder, path, "Motorcycle Man", seconds)
            took = time.monotonic() - began
        finally:
            try:
                os.remove(path)
            except OSError:
                pass
        if spoken and not re.search(r"notepad|motorcycle", words, re.IGNORECASE):
            raise PackError("the new ears didn't hear the test sentence")
        return round(took / max(seconds, 0.5), 2)
