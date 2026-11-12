import mm_test_env  # noqa: F401 (first: the tests never touch the real settings and keys)
import os
import re
import shutil
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
ANDROID = os.path.join(HERE, "..", "Android")
SOURCES = os.path.join(ANDROID, "src", "app", "motorcycleman", "remote")
NS = "{http://schemas.android.com/apk/res/android}"

# Holding the helmet on the phone (app 1.1): SpeechTurn.java keeps a press's words and decides what comes after each
# pause, in plain Java, so this runs it with a plain JDK. No phone, microphone or Android needed.
CHECK = r"""
package app.motorcycleman.remote;

public class SpeechTurnCheck {
    static void same(Object want, Object got, String what) {
        if (!want.equals(got)) {
            throw new AssertionError(what + ": wanted " + want + ", got " + got);
        }
    }

    public static void main(String[] args) {
        SpeechTurn turn = new SpeechTurn();

        // The words of a held press add up across pauses; partials replace each other within a piece.
        turn.reset(true);
        turn.hearing("open note");
        same("open note", turn.text(), "partial");
        turn.ended("open notepad");
        turn.hearing("and type");
        same("open notepad and type", turn.text(), "next piece adds on");
        turn.ended("");
        same("open notepad and type", turn.text(), "an empty end keeps the words shown");
        turn.hearing("  hello   world ");
        same("open notepad and type hello world", turn.text(), "spaces");
        turn.reset(true);
        same(true, turn.isEmpty(), "reset");

        // Repeated words are never dropped.
        same("press tab tab tab", SpeechTurn.join("press tab", "tab tab"), "repeats");
        same("a b", SpeechTurn.join(" a ", " b "), "one space");
        same("a", SpeechTurn.join("a", ""), "nothing added");

        // One long session: segments add on, and onResults then only marks the end (no words twice).
        turn.reset(true);
        turn.segment("open notepad");
        turn.hearing("and type");
        turn.segment("and type hello");
        same("open notepad and type hello", turn.text(), "segments");
        turn.ended("open notepad and type hello");
        same("open notepad and type hello", turn.text(), "results after segments");
        turn.hearing("bye");
        turn.ended("");
        same("open notepad and type hello bye", turn.text(), "words after the last segment");
        // A service that ignored the long session: its results are the piece's words.
        turn.ended("and save it");
        same("open notepad and type hello bye and save it", turn.text(), "results without segments");

        // Held: a pause starts the next piece at once; trouble gets fresh tries with rests, three in a row at most.
        turn.reset(true);
        same(SpeechTurn.AGAIN, turn.next(SpeechTurn.HELD, SpeechTurn.ENDED, 5000, true), "held pause");
        same(SpeechTurn.AGAIN_FRESH, turn.next(SpeechTurn.HELD, SpeechTurn.ENDED, 5300, false), "ended before the mic opened");
        same(1, turn.tries(), "counted");
        same(150L, SpeechTurn.backoffMs(turn.tries()), "first rest");
        same(SpeechTurn.AGAIN_FRESH, turn.next(SpeechTurn.HELD, SpeechTurn.TROUBLE, 5600, false), "second try");
        same(SpeechTurn.AGAIN_FRESH, turn.next(SpeechTurn.HELD, SpeechTurn.TROUBLE, 6400, false), "third try");
        same(900L, SpeechTurn.backoffMs(turn.tries()), "third rest");
        same(SpeechTurn.FINISH, turn.next(SpeechTurn.HELD, SpeechTurn.TROUBLE, 7400, false), "gives up");
        turn.reset(true);
        turn.next(SpeechTurn.HELD, SpeechTurn.TROUBLE, 100, false);
        turn.next(SpeechTurn.HELD, SpeechTurn.TROUBLE, 400, false);
        turn.ready();
        same(0, turn.tries(), "the mic opening starts the count again");
        turn.next(SpeechTurn.HELD, SpeechTurn.TROUBLE, 900, true);
        turn.hearing("hi");
        same(0, turn.tries(), "words start the count again");

        // Held: one offline try after a network error, one en-US try after a language error, then the end.
        turn.reset(false);
        same(SpeechTurn.AGAIN_OFFLINE, turn.next(SpeechTurn.HELD, SpeechTurn.NO_NETWORK, 2000, true), "offline");
        same(SpeechTurn.FINISH, turn.next(SpeechTurn.HELD, SpeechTurn.NO_NETWORK, 3000, true), "offline once");
        same(SpeechTurn.AGAIN_ENGLISH, turn.next(SpeechTurn.HELD, SpeechTurn.NO_LANGUAGE, 3500, false), "en-US");
        same(SpeechTurn.FINISH, turn.next(SpeechTurn.HELD, SpeechTurn.NO_LANGUAGE, 4000, false), "en-US once");
        turn.reset(true);
        same(SpeechTurn.FINISH, turn.next(SpeechTurn.HELD, SpeechTurn.NO_LANGUAGE, 500, false), "already en-US");
        same(SpeechTurn.FINISH, turn.next(SpeechTurn.HELD, SpeechTurn.FAILED, 500, true), "failed");

        // A tap: the first end with words finishes; with nothing said it waits up to 8 seconds; one fresh try.
        turn.reset(true);
        same(SpeechTurn.AGAIN, turn.next(SpeechTurn.UNTIL_QUIET, SpeechTurn.ENDED, 4000, true), "tap, nothing yet");
        same(SpeechTurn.FINISH, turn.next(SpeechTurn.UNTIL_QUIET, SpeechTurn.ENDED, 8000, true), "tap, 8 s of nothing");
        turn.reset(true);
        same(SpeechTurn.AGAIN_FRESH, turn.next(SpeechTurn.UNTIL_QUIET, SpeechTurn.TROUBLE, 300, false), "tap trouble");
        same(SpeechTurn.FINISH, turn.next(SpeechTurn.UNTIL_QUIET, SpeechTurn.TROUBLE, 900, false), "tap trouble twice");
        turn.reset(true);
        turn.ended("open spotify");
        same(SpeechTurn.FINISH, turn.next(SpeechTurn.UNTIL_QUIET, SpeechTurn.ENDED, 2500, true), "tap with words");
        turn.reset(true);
        turn.hearing("open");
        turn.ended("");
        same(SpeechTurn.FINISH, turn.next(SpeechTurn.UNTIL_QUIET, SpeechTurn.TROUBLE, 2500, true), "tap trouble after words");

        // Let go: whatever happens, the end.
        turn.reset(true);
        same(SpeechTurn.FINISH, turn.next(SpeechTurn.ENDING, SpeechTurn.ENDED, 9000, true), "let go");
        same(SpeechTurn.FINISH, turn.next(SpeechTurn.ENDING, SpeechTurn.TROUBLE, 9000, true), "let go, trouble");

        // Rests and error sorting.
        same(0L, SpeechTurn.backoffMs(0), "rest 0");
        same(150L, SpeechTurn.backoffMs(1), "rest 1");
        same(600L, SpeechTurn.backoffMs(2), "rest 2");
        same(900L, SpeechTurn.backoffMs(3), "rest 3");
        same(900L, SpeechTurn.backoffMs(7), "rest 7");
        int[] kinds = {SpeechTurn.FAILED, SpeechTurn.NO_NETWORK, SpeechTurn.NO_NETWORK, SpeechTurn.FAILED, SpeechTurn.TROUBLE,
                SpeechTurn.TROUBLE, SpeechTurn.ENDED, SpeechTurn.ENDED, SpeechTurn.TROUBLE, SpeechTurn.FAILED, SpeechTurn.FAILED,
                SpeechTurn.TROUBLE, SpeechTurn.NO_LANGUAGE, SpeechTurn.NO_LANGUAGE, SpeechTurn.FAILED, SpeechTurn.FAILED};
        for (int error = 0; error < kinds.length; error++) {
            same(kinds[error], SpeechTurn.kindOf(error), "error " + error);
        }

        // A tap's quiet timer: 1.5 s, only for a tap, and it ends the press only once there are words.
        same(1500L, SpeechTurn.quietTimerMs(SpeechTurn.UNTIL_QUIET), "a tap's quiet");
        same(0L, SpeechTurn.quietTimerMs(SpeechTurn.HELD), "a hold has none");
        same(0L, SpeechTurn.quietTimerMs(SpeechTurn.ENDING), "nor a let go");
        same(0L, SpeechTurn.quietTimerMs(SpeechTurn.OFF), "nor no press");
        turn.reset(true);
        same(false, turn.quietEnds(SpeechTurn.UNTIL_QUIET), "a tap with no words waits for them");
        turn.hearing("open spotify");
        same(true, turn.quietEnds(SpeechTurn.UNTIL_QUIET), "a tap with words ends");
        same(false, turn.quietEnds(SpeechTurn.HELD), "a hold goes on until let go");

        // A speech service refusing a way of listening (one long session, or the app's own recording).
        same(true, SpeechTurn.isRefusal(12, true), "language not supported that way");
        same(true, SpeechTurn.isRefusal(13, false), "language unavailable that way");
        same(true, SpeechTurn.isRefusal(5, false), "client error before the microphone opened");
        same(false, SpeechTurn.isRefusal(5, true), "client error after it opened");
        same(false, SpeechTurn.isRefusal(8, false), "one BUSY before ready does not set the doubt");
        for (int error : new int[] {11, 2, 1, 9, 7, 6, -1}) {
            same(false, SpeechTurn.isRefusal(error, false), "not a refusal: " + error);
        }

        // Refused in three presses: not asked that way for a week, or until the service is updated.
        String service = "com.example.speech/.RecognitionService";
        long now = 1000000L;
        same(3, SpeechTurn.DOUBT_PRESSES, "three presses");
        same(7L * 24 * 60 * 60 * 1000, SpeechTurn.DOUBT_MS, "a week");
        same(false, SpeechTurn.refuses("", service, 7, now), "nothing stored");
        String stored = SpeechTurn.afterRefusal("", service, 7, now);
        same("1|" + (now + SpeechTurn.DOUBT_MS) + "|7|" + service, stored, "stored");
        same(false, SpeechTurn.refuses(stored, service, 7, now), "one press");
        stored = SpeechTurn.afterRefusal(stored, service, 7, now + 10);
        same(false, SpeechTurn.refuses(stored, service, 7, now + 10), "two presses");
        stored = SpeechTurn.afterRefusal(stored, service, 7, now + 20);
        same(true, SpeechTurn.refuses(stored, service, 7, now + 20), "three presses: not asked that way");
        same(true, SpeechTurn.refuses(stored, service, 7, now + 20 + SpeechTurn.DOUBT_MS - 1), "for a week");
        same(false, SpeechTurn.refuses(stored, service, 7, now + 20 + SpeechTurn.DOUBT_MS), "then asked again");
        same(false, SpeechTurn.refuses(stored, service, 8, now + 30), "an updated service is asked again");
        same(false, SpeechTurn.refuses(stored, "com.example.other/.RecognitionService", 7, now + 30), "another service");
        same("1|" + (now + 40 + SpeechTurn.DOUBT_MS) + "|8|" + service, SpeechTurn.afterRefusal(stored, service, 8, now + 40),
             "counted afresh after an update");
        same("1|" + (now + 50 + 2 * SpeechTurn.DOUBT_MS) + "|7|" + service,
             SpeechTurn.afterRefusal(stored, service, 7, now + 50 + SpeechTurn.DOUBT_MS), "counted afresh after a week");
        same(false, SpeechTurn.refuses(null, service, 7, now), "nothing stored (null)");
        same(false, SpeechTurn.refuses("not a count", service, 7, now), "unreadable");
        same(false, SpeechTurn.refuses("x|y|7|" + service, service, 7, now), "unreadable numbers");
        System.out.println("speech turn ok");
    }
}
"""


def jdk_tool(name: str):
    # The JDK build_apk.ps1 downloads, else JAVA_HOME's, else one on PATH; None when there is none.
    exe = name + (".exe" if os.name == "nt" else "")
    folders = [os.path.join(ANDROID, "tools", "jdk17", "bin")]
    if os.environ.get("JAVA_HOME"):
        folders.append(os.path.join(os.environ["JAVA_HOME"], "bin"))
    for folder in folders:
        if os.path.isfile(os.path.join(folder, exe)):
            return os.path.join(folder, exe)
    return shutil.which(name)


def read(name: str) -> str:
    with open(os.path.join(SOURCES, name), encoding="utf-8") as handle:
        return handle.read()


class SpeechTurnTests(unittest.TestCase):
    def test_a_held_press_keeps_every_word_and_tries_again_only_a_few_times(self):
        javac, java = jdk_tool("javac"), jdk_tool("java")
        if not javac or not java:
            self.skipTest("No JDK (build_apk.ps1 downloads one into Android\\tools)")
        with tempfile.TemporaryDirectory() as temp_dir:
            check = os.path.join(temp_dir, "SpeechTurnCheck.java")
            with open(check, "w", encoding="utf-8") as handle:
                handle.write(CHECK)
            classes = os.path.join(temp_dir, "classes")
            built = subprocess.run([javac, "--release", "8", "-d", classes, os.path.join(SOURCES, "SpeechTurn.java"), check],
                                   capture_output=True, text=True, timeout=120)
            self.assertEqual(built.returncode, 0, built.stderr)
            ran = subprocess.run([java, "-cp", classes, "app.motorcycleman.remote.SpeechTurnCheck"],
                                 capture_output=True, text=True, timeout=60)
            self.assertEqual(ran.returncode, 0, ran.stderr)
            self.assertIn("speech turn ok", ran.stdout)

    def test_the_press_logic_needs_no_android(self):
        self.assertNotIn("import android", read("SpeechTurn.java"))


class HoldToTalkTests(unittest.TestCase):
    def test_the_page_can_hold_the_phone_microphone_open_until_let_go(self):
        java = read("MainActivity.java")
        for needed in ("public void listen(final String language, final String talk, final String options)",
                       "public void listenUntilQuiet(final String talk)", "public void doneListening(final String talk)",
                       "public void cancelListening(final String talk)", "public boolean fingerDown()",
                       "onAndroidSpeechState", "onAndroidSpeechLevel", "onAndroidFinger",
                       "onSegmentResults", "onEndOfSegmentedSession", "AUDIOFOCUS_GAIN_TRANSIENT_EXCLUSIVE",
                       "setOnTouchListener", '"speech_segmented"'):
            self.assertIn(needed, java)
        # Pages from app 1.0 still listen the old way.
        for kept in ("public boolean isAvailable()", "public void startListening(final String language)",
                     "public void stopListening()"):
            self.assertIn(kept, java)

    def test_android_13_extras_are_only_asked_for_on_android_13(self):
        # Older phones have no such extras: each one is set only inside an "Android 13 or newer" check.
        lines = read("MainActivity.java").splitlines()
        found = 0
        for n, line in enumerate(lines):
            if re.search(r"put\w*Extra\(RecognizerIntent\.(EXTRA_SEGMENTED_SESSION|EXTRA_BIASING_STRINGS|EXTRA_AUDIO_SOURCE)", line):
                found += 1
                guard = next((above for above in reversed(lines[max(0, n - 8):n]) if "if (" in above), "")
                self.assertIn("Build.VERSION.SDK_INT >= 33", guard, line.strip())
        self.assertGreaterEqual(found, 2)

    def test_a_press_stops_when_the_app_leaves_the_front(self):
        java = read("MainActivity.java")
        on_pause = re.search(r"protected void onPause\(\) \{(.*?)\n    \}", java, re.S).group(1)
        self.assertIn("cancelTalk(true)", on_pause)
        self.assertIn("talkMode != SpeechTurn.OFF", java)  # the phone doesn't talk over a press

    def test_a_tap_ends_after_its_quiet_and_refusals_are_counted(self):
        java = read("MainActivity.java")
        quiet = re.search(r"private final Runnable talkQuiet = new Runnable\(\) \{(.*?)\n    \};", java, re.S).group(1)
        self.assertIn("talkQuietFor == talkRecognizer && talkOpen && turn.quietEnds(talkMode)", quiet)
        self.assertIn("endTalk(talkKey)", quiet)  # which asks the recognizer for its last words (stopListening)
        again = re.search(r"private void quietAgain\(\) \{(.*?)\n    \}", java, re.S).group(1)
        self.assertIn("SpeechTurn.quietTimerMs(talkMode)", again)
        stop = re.search(r"private void stopRecognizer\(\) \{(.*?)\n    \}", java, re.S).group(1)
        self.assertIn("handler.removeCallbacks(talkQuiet)", stop)
        self.assertGreaterEqual(java.count("quietAgain();"), 3)  # a tap, a new word, the end of speech
        self.assertIn("SpeechTurn.isRefusal(error, wasReady)", java)
        self.assertIn("SpeechTurn.refuses(prefs().getString(setting, \"\"), service, serviceVersion(service)", java)
        self.assertIn("SpeechTurn.afterRefusal(", java)

    def test_the_apps_own_recording_takes_out_the_phones_own_sound(self):
        # App 1.1, behind the page's hidden switch: 16 kHz mono with the echo canceller, through a pipe, and the
        # usual way in the same press when a speech service won't take it.
        mic = read("OwnMic.java")
        for needed in ("MediaRecorder.AudioSource.VOICE_RECOGNITION", "RATE = 16000", "AudioFormat.CHANNEL_IN_MONO",
                       "AcousticEchoCanceler.create(session)", "cleanNoise && NoiseSuppressor.isAvailable()",
                       "ParcelFileDescriptor.createPipe()", "checkSelfPermission(Manifest.permission.RECORD_AUDIO)"):
            self.assertIn(needed, mic)
        java = read("MainActivity.java")
        for needed in ('optionOn(options, "ownMic")', "!refused(KEY_OWN_MIC)", '"speech_own_mic"', "OWN_MIC_READY_MS = 1500",
                       "RecognizerIntent.EXTRA_SEGMENTED_SESSION, RecognizerIntent.EXTRA_AUDIO_SOURCE",
                       "talkMic.finish()", "talkDoubt = KEY_OWN_MIC"):
            self.assertIn(needed, java)
        stop = re.search(r"private void stopRecognizer\(\) \{(.*?)\n    \}", java, re.S).group(1)
        self.assertIn("closeMic()", stop)  # every way a press ends lets go of the recording

    def test_the_logs_never_hold_what_was_said(self):
        for name in os.listdir(SOURCES):
            if name.endswith(".java"):
                self.assertIsNone(re.search(r"Log\.\w\(TAG,[^;]*(text|words|piece)", read(name)), name)

    def test_phones_update_to_the_hold_to_talk_app(self):
        manifest = ET.parse(os.path.join(ANDROID, "AndroidManifest.xml")).getroot()
        self.assertGreaterEqual(int(manifest.get(NS + "versionCode")), 20)


if __name__ == "__main__":
    unittest.main()
