package app.motorcycleman.remote;

import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

// Gets a newer Motorcycle Man app from the PC. Motorcycle Man.exe carries the app it was built with and hands it only to
// phones that have the link's secret: /app_version says which version it is, /app.apk is the app.
// Plain Java with no Android parts, so it can be tried on a PC too.
final class AppUpdate {
    static final class Offer {
        final int code;  // Android's version number for the app: a higher one is newer
        final String name;  // the version people see, like "1.4"
        final long size;  // in bytes, or -1 when the PC didn't say

        Offer(int code, String name, long size) {
            this.code = code;
            this.name = name;
            this.size = size;
        }
    }

    interface Progress {
        void downloaded(long done, long total);
    }

    private AppUpdate() {
    }

    // The app the PC at base has ("http://192.168.1.20:8080" on the Wi-Fi, or
    // "https://<words>.trycloudflare.com" through the internet link), or null when it didn't answer.
    // An Motorcycle Man with no app to hand out, or one older than updates from the PC, says code 0.
    static Offer check(String base, String token) {
        String reply = PcFinder.get(base + "/app_version?token=" + PcFinder.encode(token), 3000, 8000);
        if (reply == null) {
            return null;
        }
        String name = PcFinder.field(reply, "versionName");
        Matcher size = Pattern.compile("\"size\"\\s*:\\s*(\\d{1,12})").matcher(reply);
        return new Offer(PcFinder.number(reply, "versionCode"), name == null ? "" : name,
                size.find() ? Long.parseLong(size.group(1)) : -1);
    }

    // Downloads the app into file. Returns null when all of it arrived, else what went wrong.
    static String download(String base, String token, long size, File file, Progress progress) {
        HttpURLConnection connection = null;
        try {
            connection = (HttpURLConnection) new URL(base + "/app.apk?token=" + PcFinder.encode(token)).openConnection();
            connection.setConnectTimeout(5000);
            connection.setReadTimeout(20000);
            if (connection.getResponseCode() != 200) {
                return "Your PC didn't hand over the new app. Try again in a moment.";
            }
            long total = connection.getContentLengthLong() >= 0 ? connection.getContentLengthLong() : size;
            long done = 0;
            try (InputStream in = connection.getInputStream(); OutputStream out = new FileOutputStream(file)) {
                byte[] buffer = new byte[64 * 1024];
                for (int n = in.read(buffer); n != -1; n = in.read(buffer)) {
                    out.write(buffer, 0, n);
                    done += n;
                    progress.downloaded(done, total);
                }
            }
            if ((total >= 0 && done != total) || (size >= 0 && done != size)) {
                return "The download from your PC was cut short. Try again.";
            }
            return null;
        } catch (IOException | RuntimeException e) {
            return "Couldn't get the new app from your PC. Make sure Motorcycle Man is open there, then try again.";
        } finally {
            if (connection != null) {
                connection.disconnect();
            }
        }
    }
}
