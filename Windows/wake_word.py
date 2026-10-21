# "Hey Motorcycle Man": Windows' own offline speech recognizer (free, nothing to download) listens for just
# the wake words, in a small PowerShell helper, and says when it hears them. Nothing it hears leaves the PC;
# only the command said after the wake words is written down (by Gemini), like any spoken command.
import base64, logging, os, subprocess, threading

LOG = logging.getLogger("motorcycle_man")
PHRASES = ("hey motorcycle man", "okay motorcycle man")
# Everyday words the recognizer can match instead, so talk that only sounds a bit like the wake words
# ("I love my motorcycle, man") isn't forced onto them. In tests with 88 recordings of Windows' two voices
# at two speeds, all 20 with the wake words woke it, and 1 of the 68 without did ("hey motor city").
OTHER_WORDS = ("motorcycle", "the motorcycle", "a motorcycle", "my motorcycle", "motorcycles", "motor", "man", "the man",
               "hey", "okay", "hi", "hello", "high", "cycle", "my cycle", "hey there", "okay so", "the", "a", "is", "and",
               "that", "my", "it", "go", "let's go")
CONFIDENCE = 0.6
SCRIPT = r"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Speech
$engine = New-Object System.Speech.Recognition.SpeechRecognitionEngine([System.Globalization.CultureInfo]'en-US')
$phrases = New-Object System.Speech.Recognition.Choices
foreach ($phrase in $env:MM_WAKE_PHRASES.Split('|')) { $phrases.Add($phrase) }
$builder = New-Object System.Speech.Recognition.GrammarBuilder($phrases)
$builder.Culture = $engine.RecognizerInfo.Culture
$wake = New-Object System.Speech.Recognition.Grammar($builder)
$wake.Name = 'wake'
$engine.LoadGrammar($wake)
$others = New-Object System.Speech.Recognition.Choices
foreach ($phrase in $env:MM_WAKE_OTHER.Split('|')) { $others.Add($phrase) }
$builder = New-Object System.Speech.Recognition.GrammarBuilder($others)
$builder.Culture = $engine.RecognizerInfo.Culture
$engine.LoadGrammar((New-Object System.Speech.Recognition.Grammar($builder)))
$file = $env:MM_WAKE_FILE
function Tell($heard) {
  if ($heard -ne $null -and $heard.Grammar.Name -eq 'wake') {
    [Console]::Out.WriteLine('WAKE ' + $heard.Confidence.ToString([System.Globalization.CultureInfo]::InvariantCulture) + ' ' + $heard.Text)
    [Console]::Out.Flush()
  }
}
if ($file) {  # a recording, for tests: heard straight through
  $engine.SetInputToWaveFile($file)
  [Console]::Out.WriteLine('READY')
  for ($i = 0; $i -lt 30; $i++) { try { Tell $engine.Recognize() } catch { break } }
  exit
}
$engine.SetInputToDefaultAudioDevice()
Register-ObjectEvent -InputObject $engine -EventName SpeechRecognized -SourceIdentifier heard | Out-Null
Register-ObjectEvent -InputObject $engine -EventName RecognizeCompleted -SourceIdentifier ended | Out-Null
$engine.RecognizeAsync([System.Speech.Recognition.RecognizeMode]::Multiple)
[Console]::Out.WriteLine('READY')
[Console]::Out.Flush()
while ($true) {
  # It ends with Motorcycle Man, even if that closed without saying so.
  if ($env:MM_WAKE_PARENT -and -not (Get-Process -Id $env:MM_WAKE_PARENT -ErrorAction SilentlyContinue)) { break }
  $news = Wait-Event -Timeout 1
  if ($news -eq $null) { continue }
  Remove-Event -EventIdentifier $news.EventIdentifier
  if ($news.SourceIdentifier -eq 'heard') { Tell $news.SourceEventArgs.Result; continue }
  if ($news.SourceIdentifier -eq 'ended') {  # the microphone went away: try again in a moment
    Start-Sleep -Seconds 2
    try { $engine.SetInputToDefaultAudioDevice(); $engine.RecognizeAsync([System.Speech.Recognition.RecognizeMode]::Multiple) } catch {}
  }
}
$engine.RecognizeAsyncCancel()
"""


def helper_command() -> list:
    encoded = base64.b64encode(SCRIPT.encode("utf-16-le")).decode("ascii")
    return ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-EncodedCommand", encoded]


def start_helper(wave_file: str = "") -> subprocess.Popen:
    env = dict(os.environ, MM_WAKE_PHRASES="|".join(PHRASES), MM_WAKE_OTHER="|".join(OTHER_WORDS), MM_WAKE_FILE=wave_file,
               MM_WAKE_PARENT=str(os.getpid()))
    return subprocess.Popen(helper_command(), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            env=env, text=True, encoding="utf-8", errors="replace",
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def read_line(line: str):
    # (confidence, words) for a "WAKE 0.95 hey motorcycle man" line, else None.
    parts = line.strip().split(" ", 2)
    if len(parts) == 3 and parts[0] == "WAKE":
        try:
            return float(parts[1]), parts[2]
        except ValueError:
            return None
    return None


def hear_file(path: str, timeout: float = 60) -> list:
    # The wake words heard in a WAV file, as (confidence, words): for tests.
    helper = start_helper(path)
    try:
        out, _ = helper.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        helper.kill()
        out, _ = helper.communicate()
    return [found for found in map(read_line, out.splitlines()) if found]


class WakeWord:
    # Listens while it's on; heard() is called (on the listener's thread) each time the wake words are said.
    # changed(state, detail): "on", "off", or "failed" with why.
    RESTARTS = 3

    def __init__(self, heard=None, changed=None):
        self.heard, self.changed = heard, changed
        self.helper, self.thread = None, None
        self.stopped = threading.Event()
        self.state, self.detail = "off", ""

    def start(self):
        if self.thread is not None and self.thread.is_alive():
            if not self.stopped.is_set():
                return  # already listening
            # Turned off a moment ago and its listener is still ending: let it end, then start a new one.
            # (Returning here left the switch on with nobody listening.)
            self.thread.join(3)
        self.stopped.clear()
        self.thread = threading.Thread(target=self.run, name="wake word", daemon=True)
        self.thread.start()

    def stop(self):
        self.stopped.set()
        helper = self.helper
        if helper is not None and helper.poll() is None:
            helper.kill()
        self._tell("off", "")

    def _tell(self, state: str, detail: str):
        self.state, self.detail = state, detail
        if self.changed:
            self.changed(state, detail)

    def run(self):
        failures = 0
        while not self.stopped.is_set():
            try:
                self.helper = helper = start_helper()
            except OSError as exc:
                self._tell("failed", f"Windows' speech recognizer couldn't start: {exc}")
                return
            ready = False
            for line in helper.stdout:
                if self.stopped.is_set():
                    break
                if line.strip() == "READY":
                    ready, failures = True, 0
                    self._tell("on", "")
                    LOG.info("Listening for \"Hey Motorcycle Man\"")
                    continue
                found = read_line(line)
                if found and found[0] >= CONFIDENCE and self.heard:
                    LOG.info("Heard the wake words (%.2f)", found[0])
                    try:
                        self.heard()
                    except Exception:
                        LOG.exception("Answering the wake words failed")
            if self.stopped.is_set():
                return
            why = (helper.stderr.read() or "").strip().splitlines()
            helper.wait()
            failures += 1
            LOG.warning("The wake word listener stopped: %s", why[-1] if why else helper.returncode)
            if failures >= self.RESTARTS or not ready:
                reason = " ".join(why).lower()
                self._tell("failed", "No microphone found." if "audio" in reason or "microphone" in reason
                           else "Windows' English speech recognizer isn't available on this PC.")
                return
            self.stopped.wait(2 * failures)
