package app.motorcycleman.remote;

// One press of the helmet, in plain Java (no Android classes), so a test can run it with a plain JDK.
// The phone's speech recognizer ends a piece at every pause. While the helmet is held, MainActivity starts the next
// piece and the words are added on, so a pause never ends the press; this keeps the words and decides what comes
// after each end. MainActivity does the listening.
final class SpeechTurn {
    // How the page wants this press heard.
    static final int OFF = 0;
    static final int HELD = 1;         // the finger is down: every end starts the next piece
    static final int UNTIL_QUIET = 2;  // a tap, the tile or the widget: the first end with words finishes
    static final int ENDING = 3;       // let go: the recognizer's last words, then the end

    // How a piece ended.
    static final int ENDED = 0;        // results, the end of a long session, or nothing heard (no match, speech timeout)
    static final int TROUBLE = 1;      // client, busy, server, server disconnected, or the microphone never opened
    static final int NO_NETWORK = 2;   // network, network timeout
    static final int FAILED = 3;       // audio, permissions, too many requests and anything new: tell the user
    static final int NO_LANGUAGE = 4;  // language not supported or not available

    // What happens next.
    static final int AGAIN = 1;          // the next piece, on the same recognizer, at once
    static final int AGAIN_FRESH = 2;    // the next piece on a fresh recognizer, after a rest
    static final int AGAIN_OFFLINE = 3;  // fresh, asking for the phone's offline recognition
    static final int AGAIN_ENGLISH = 4;  // fresh, in en-US
    static final int FINISH = 5;

    static final int MOST_TRIES = 3;               // fresh tries in a row before the microphone opens
    static final long WAIT_FOR_WORDS_MS = 8000;    // a tap with nothing said ends after this long
    static final long QUIET_MS = 1500;             // a tap with words ends after this long with no new ones

    // A speech service that refuses a way of listening (one long session, or the app's own recording) is asked that
    // way again until it has refused it in DOUBT_PRESSES different presses; then not for DOUBT_MS (or until the
    // service is updated). Stored in a setting as "count|untilMs|versionCode|service".
    static final int DOUBT_PRESSES = 3;
    static final long DOUBT_MS = 7L * 24 * 60 * 60 * 1000;

    private final StringBuilder done = new StringBuilder();  // the pieces finished so far
    private String live = "";       // what the recognizer is hearing in the piece now
    private boolean segmentCame;    // this piece (one long session) gave its words in segments already
    private int tries;              // troubles in a row; the microphone opening, or words, start it again at 0
    private boolean offlineTried;
    private boolean englishTried;

    // A new press. inEnglish: it already listens in en-US, so a language error has nothing to fall back to.
    void reset(boolean inEnglish) {
        done.setLength(0);
        live = "";
        segmentCame = false;
        tries = 0;
        offlineTried = false;
        englishTried = inEnglish;
    }

    // The microphone opened.
    void ready() {
        tries = 0;
    }

    // What the recognizer hears so far in this piece (partial results replace each other).
    void hearing(String partial) {
        live = clean(partial);
        if (!live.isEmpty()) {
            tries = 0;
        }
    }

    // A long session's finished segment (Android 13 and newer): its words are added on, and the session goes on.
    void segment(String words) {
        add(clean(words).isEmpty() ? live : words);
        segmentCame = true;
    }

    // A piece is over. Its results count, unless segments already gave them (then any words heard since the last
    // segment count). A piece that ends with nothing after showing words keeps the words it showed.
    void ended(String results) {
        String words = segmentCame ? "" : clean(results);
        add(words.isEmpty() ? live : words);
        segmentCame = false;
    }

    private void add(String words) {
        String piece = clean(words);
        if (!piece.isEmpty()) {
            tries = 0;
        }
        String before = done.toString();
        done.setLength(0);
        done.append(join(before, piece));
        live = "";
    }

    // Everything heard in this press: the finished pieces and the one being heard.
    String text() {
        return join(done.toString(), live);
    }

    boolean isEmpty() {
        return text().isEmpty();
    }

    int tries() {
        return tries;
    }

    // What to do after a piece ended. mode: HELD, UNTIL_QUIET or ENDING; how: ENDED, TROUBLE...; sinceStartMs: since the
    // press began; wasReady: the microphone opened in this piece. A piece that ended before the microphone opened
    // is trouble, whatever the recognizer called it: only those count toward MOST_TRIES.
    int next(int mode, int how, long sinceStartMs, boolean wasReady) {
        if (how == ENDED && !wasReady) {
            how = TROUBLE;
        }
        if (how != ENDED) {
            tries++;
        }
        if (mode != HELD && mode != UNTIL_QUIET) {
            return FINISH;  // let go (or already over): the words so far go
        }
        if (mode == UNTIL_QUIET && !isEmpty()) {
            return FINISH;  // a tap ends at its first end with words
        }
        switch (how) {
            case ENDED:
                return mode == HELD || sinceStartMs < WAIT_FOR_WORDS_MS ? AGAIN : FINISH;
            case TROUBLE:
                return tries <= (mode == HELD ? MOST_TRIES : 1) ? AGAIN_FRESH : FINISH;
            case NO_NETWORK:
                if (offlineTried) {
                    return FINISH;
                }
                offlineTried = true;
                return AGAIN_OFFLINE;
            case NO_LANGUAGE:
                if (englishTried) {
                    return FINISH;
                }
                englishTried = true;
                return AGAIN_ENGLISH;
            default:
                return FINISH;
        }
    }

    // A tap's quiet timer (MainActivity starts it again at every new word and at the end of speech): how long it
    // runs in this mode, or 0 for none. Only a tap has one; a held press ends when the finger lifts.
    static long quietTimerMs(int mode) {
        return mode == UNTIL_QUIET ? QUIET_MS : 0;
    }

    // Whether the quiet timer, when it runs out, ends the press: a tap that heard words.
    boolean quietEnds(int mode) {
        return mode == UNTIL_QUIET && !isEmpty();
    }

    // Whether an error is the speech service refusing the way it was asked to listen (a long session, or the app's
    // own recording): a language it won't do that way (12, 13), or ERROR_CLIENT (5) before the microphone opened.
    // Busy (8), disconnected (11), the network (1, 2), permissions (9) and a microphone that never opened (-1) say
    // nothing about the way of listening.
    static boolean isRefusal(int error, boolean wasReady) {
        return error == 12 || error == 13 || (error == 5 && !wasReady);
    }

    // The setting after one more press in which service refused (and, asked the usual way, opened the microphone).
    static String afterRefusal(String stored, String service, long version, long nowMs) {
        long count = Math.min(doubts(stored, service, version, nowMs) + 1, 99);
        return count + "|" + (nowMs + DOUBT_MS) + "|" + version + "|" + service;
    }

    // Whether the setting says not to ask service that way now.
    static boolean refuses(String stored, String service, long version, long nowMs) {
        return doubts(stored, service, version, nowMs) >= DOUBT_PRESSES;
    }

    // The presses the setting counts for this service at this version, until it runs out; 0 for anything else.
    private static long doubts(String stored, String service, long version, long nowMs) {
        String[] parts = stored == null ? new String[0] : stored.split("\\|", 4);
        if (parts.length != 4 || !parts[3].equals(service)) {
            return 0;
        }
        try {
            long count = Long.parseLong(parts[0]), until = Long.parseLong(parts[1]), was = Long.parseLong(parts[2]);
            return was == version && nowMs < until && count > 0 ? count : 0;
        } catch (NumberFormatException e) {
            return 0;
        }
    }

    // The rest before a fresh try: 0, 150, 600, then 900 ms.
    static long backoffMs(int triesInARow) {
        return triesInARow <= 0 ? 0 : Math.min(900, 150L << (2 * (triesInARow - 1)));
    }

    // SpeechRecognizer's error numbers, sorted. Numbers, not SpeechRecognizer's names, so this needs no Android.
    static int kindOf(int error) {
        switch (error) {
            case 6:   // ERROR_SPEECH_TIMEOUT: nothing said
            case 7:   // ERROR_NO_MATCH: nothing understood
                return ENDED;
            case 4:   // ERROR_SERVER
            case 5:   // ERROR_CLIENT
            case 8:   // ERROR_RECOGNIZER_BUSY
            case 11:  // ERROR_SERVER_DISCONNECTED (Android 12)
                return TROUBLE;
            case 1:   // ERROR_NETWORK_TIMEOUT
            case 2:   // ERROR_NETWORK
                return NO_NETWORK;
            case 12:  // ERROR_LANGUAGE_NOT_SUPPORTED (Android 12)
            case 13:  // ERROR_LANGUAGE_UNAVAILABLE (Android 12)
                return NO_LANGUAGE;
            default:  // ERROR_AUDIO 3, ERROR_INSUFFICIENT_PERMISSIONS 9, ERROR_TOO_MANY_REQUESTS 10, and newer ones
                return FAILED;
        }
    }

    static String clean(String words) {
        return words == null ? "" : words.trim().replaceAll("\\s+", " ");
    }

    // Two runs of words with exactly one space between them; no word is ever dropped, repeats included.
    static String join(String a, String b) {
        return clean(clean(a) + " " + clean(b));
    }
}
