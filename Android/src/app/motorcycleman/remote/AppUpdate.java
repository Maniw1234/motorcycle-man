package app.motorcycleman.remote;

import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

// Gets a newer Motorcycle Man app, from the PC or from GitHub.
// The PC: Motorcycle Man.exe carries the app it was built with and hands it only to phones that have the link's
// secret: /app_version says which version it is, /app.apk is the app.
// GitHub: every push builds the app there (.github/workflows/release.yml) and the newest release carries it with
// phone-app.json, which says its version, size and SHA-256; the app is checked against that once downloaded.
// Either way Android installs it only when it is signed with the same key as this app.
// Plain Java with no Android parts, so it can be tried on a PC too.
final class AppUpdate {
    // The newest release's files; a fork changes this to its own repository.
    static final String GITHUB_LATEST = "https://github.com/Maniw1234/motorcycle-man/releases/latest/download/";
    static final String GITHUB_FILES = "https://github.com/";  // where the app itself may come from
    static final long MOST_BYTES = 64L << 20;  // the app is well under 1 MB, so a far bigger download is stopped

    static final class Offer {
        final int code;  // Android's version number for the app: a higher one is newer
        final String name;  // the version people see, like "1.4"
        final long size;  // in bytes, or -1 when the PC didn't say
        final String url;  // where the app is downloaded from
        final String sha256;  // what the app must hash to, or null (the PC doesn't say)
        final boolean fromPc;

        Offer(int code, String name, long size, String url, String sha256, boolean fromPc) {
            this.code = code;
            this.name = name;
            this.size = size;
            this.url = url;
            this.sha256 = sha256;
            this.fromPc = fromPc;
        }
    }

    interface Progress {
        void downloaded(long done, long total);
    }

    private AppUpdate() {
    }

    // The app the PC at base has ("http://192.168.1.20:8080" on the Wi-Fi, or
    // "https://<words>.trycloudflare.com" through the internet link), or null when it didn't answer.
    // A Motorcycle Man with no app to hand out says code 0.
    static Offer check(String base, String token) {
        String reply = PcFinder.get(base + "/app_version?token=" + PcFinder.encode(token), 3000, 8000);
        if (reply == null) {
            return null;
        }
        String name = PcFinder.field(reply, "versionName");
        long size = size(reply);
        return new Offer(PcFinder.number(reply, "versionCode"), name == null ? "" : name, size,
                base + "/app.apk?token=" + PcFinder.encode(token), null, true);
    }

    // The app in GitHub's newest release, or null when there is none (no answer, a release without the app, or
    // an answer that doesn't hold together).
    static Offer checkGitHub() {
        return checkGitHub(GITHUB_LATEST, GITHUB_FILES);
    }

    static Offer checkGitHub(String latest, String filesFrom) {
        String reply = PcFinder.get(latest + "phone-app.json", 5000, 10000);
        if (reply == null) {
            return null;
        }
        String name = PcFinder.field(reply, "versionName");
        String url = PcFinder.field(reply, "url");
        String sha256 = PcFinder.field(reply, "sha256");
        int code = PcFinder.number(reply, "versionCode");
        long size = size(reply);
        if (code <= 0 || size <= 0 || size > MOST_BYTES || url == null || !url.startsWith(filesFrom)
                || sha256 == null || !sha256.matches("[0-9a-fA-F]{64}")) {
            return null;
        }
        return new Offer(code, name == null ? "" : name, size, url, sha256.toLowerCase(), false);
    }

    // Of the PC's and GitHub's offers (either may be missing), the newest one that is newer than the app that's
    // installed, or null when there is none. The PC's wins a tie: it comes over the Wi-Fi or the PC's own link.
    static Offer newest(Offer pc, Offer gitHub, int installed) {
        Offer best = null;
        for (Offer offer : new Offer[] {pc, gitHub}) {
            if (offer != null && offer.code > installed && (best == null || offer.code > best.code)) {
                best = offer;
            }
        }
        return best;
    }

    private static long size(String json) {
        Matcher size = Pattern.compile("\"size\"\\s*:\\s*(\\d{1,12})").matcher(json);
        return size.find() ? Long.parseLong(size.group(1)) : -1;
    }

    // Downloads the offered app into file. Returns null when all of it arrived (and, from GitHub, matched its
    // SHA-256), else what went wrong.
    static String download(Offer offer, File file, Progress progress) {
        String tryAgain = offer.fromPc ? "Couldn't get the new app from your PC. Make sure Motorcycle Man is open there, "
                + "then try again." : "Couldn't download the new app. Check the phone's internet, then try again.";
        HttpURLConnection connection = null;
        try {
            MessageDigest digest = MessageDigest.getInstance("SHA-256");
            connection = (HttpURLConnection) new URL(offer.url).openConnection();
            connection.setConnectTimeout(5000);
            connection.setReadTimeout(20000);
            if (connection.getResponseCode() != 200) {
                return offer.fromPc ? "Your PC didn't hand over the new app. Try again in a moment." : tryAgain;
            }
            long size = offer.size;
            long total = connection.getContentLengthLong() >= 0 ? connection.getContentLengthLong() : size;
            long done = 0;
            try (InputStream in = connection.getInputStream(); OutputStream out = new FileOutputStream(file)) {
                byte[] buffer = new byte[64 * 1024];
                for (int n = in.read(buffer); n != -1; n = in.read(buffer)) {
                    done += n;
                    if (done > MOST_BYTES || (size >= 0 && done > size)) {
                        return "The download was bigger than the new app should be, so it was stopped.";
                    }
                    out.write(buffer, 0, n);
                    digest.update(buffer, 0, n);
                    progress.downloaded(done, total);
                }
            }
            if ((total >= 0 && done != total) || (size >= 0 && done != size)) {
                return offer.fromPc ? "The download from your PC was cut short. Try again." : "The download was cut short. Try again.";
            }
            if (offer.sha256 != null && !offer.sha256.equals(hex(digest.digest()))) {
                return "The new app arrived damaged, so it wasn't used. Try again.";
            }
            return null;
        } catch (IOException | RuntimeException | NoSuchAlgorithmException e) {
            return tryAgain;
        } finally {
            if (connection != null) {
                connection.disconnect();
            }
        }
    }

    static String hex(byte[] bytes) {
        StringBuilder text = new StringBuilder();
        for (byte b : bytes) {
            text.append(String.format("%02x", b));
        }
        return text.toString();
    }
}
