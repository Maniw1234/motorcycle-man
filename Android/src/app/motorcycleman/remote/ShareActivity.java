package app.motorcycleman.remote;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.DialogInterface;
import android.content.Intent;
import android.database.Cursor;
import android.net.Uri;
import android.os.Bundle;
import android.provider.OpenableColumns;
import android.util.Patterns;
import android.widget.Toast;

import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.util.ArrayList;
import java.util.regex.Matcher;

// "Share" to Motorcycle Man from any app: a link opens on the PC (or goes on its clipboard), text goes on the
// PC's clipboard or to Motorcycle Man as a command, and photos and files go to the PC's Downloads\Motorcycle Man.
// It uses the PC link the app last connected with.
public class ShareActivity extends Activity {
    private String base;  // the PC's address, like http://192.168.1.20:8080
    private String token;  // the link's secret
    private AlertDialog progress;

    @Override
    @SuppressWarnings("deprecation")  // the newer ways to read what was shared need Android 13
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        Uri saved = Uri.parse(getSharedPreferences("motorcycleman", MODE_PRIVATE).getString("phone_url", ""));
        token = saved.getQueryParameter("token");
        if (saved.getHost() == null || token == null) {
            done("Open Motorcycle Man and connect to your PC first.");
            return;
        }
        base = saved.getScheme() + "://" + saved.getEncodedAuthority();
        Intent intent = getIntent();
        ArrayList<Uri> files = new ArrayList<>();
        if (Intent.ACTION_SEND_MULTIPLE.equals(intent.getAction())) {
            ArrayList<Uri> many = intent.getParcelableArrayListExtra(Intent.EXTRA_STREAM);
            if (many != null) {
                files.addAll(many);
            }
        } else {
            Uri one = intent.getParcelableExtra(Intent.EXTRA_STREAM);
            if (one != null) {
                files.add(one);
            }
        }
        String text = intent.getStringExtra(Intent.EXTRA_TEXT);
        if (!files.isEmpty()) {
            sendFiles(files);
        } else if (text != null && !text.trim().isEmpty()) {
            chooseForText(text.trim());
        } else {
            done("There was nothing to send.");
        }
    }

    private void chooseForText(final String text) {
        Matcher found = Patterns.WEB_URL.matcher(text);
        final String link = found.find() && text.contains("://") ? found.group() : null;
        final String[] choices = link != null
                ? new String[] {"Open on the PC", "Put on the PC's clipboard", "Ask Motorcycle Man"}
                : new String[] {"Put on the PC's clipboard", "Ask Motorcycle Man"};
        new AlertDialog.Builder(this, android.R.style.Theme_DeviceDefault_Dialog_Alert)
                .setTitle("Send to your PC")
                .setItems(choices, new DialogInterface.OnClickListener() {
                    @Override
                    public void onClick(DialogInterface dialog, int which) {
                        String choice = choices[which];
                        if (choice.startsWith("Open")) {
                            send("Opening it on the PC...", "GET", "/open_link", "url=" + Uri.encode(link), null);
                        } else if (choice.startsWith("Put")) {
                            send("Putting it on the PC's clipboard...", "POST", "/clipboard", "", text);
                        } else {
                            startActivity(new Intent(ShareActivity.this, MainActivity.class)
                                    .putExtra(MainActivity.EXTRA_ASK, text).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));
                            finish();
                        }
                    }
                })
                .setOnCancelListener(new DialogInterface.OnCancelListener() {
                    @Override
                    public void onCancel(DialogInterface dialog) {
                        finish();
                    }
                })
                .show();
    }

    private void send(String what, final String method, final String path, final String query, final String text) {
        showProgress(what);
        new Thread(new Runnable() {
            @Override
            public void run() {
                String say;
                try {
                    byte[] body = text == null ? null : text.getBytes("UTF-8");
                    say = request(method, path, query, body == null ? null : new java.io.ByteArrayInputStream(body),
                            body == null ? 0 : body.length, "text/plain; charset=utf-8");
                } catch (Exception error) {
                    say = "Couldn't reach your PC. Is Motorcycle Man open?";
                }
                finishOnUi(say);
            }
        }).start();
    }

    private void sendFiles(final ArrayList<Uri> files) {
        showProgress(files.size() == 1 ? "Sending it to your PC..." : "Sending " + files.size() + " files to your PC...");
        new Thread(new Runnable() {
            @Override
            public void run() {
                String say = "";
                int sent = 0;
                for (int i = 0; i < files.size(); i++) {
                    Uri file = files.get(i);
                    File copy = null;
                    try {
                        String name = nameOf(file, i);
                        long size = sizeOf(file);
                        if (size < 0) {  // the app sharing it doesn't say how big it is: measure a copy
                            copy = File.createTempFile("share", ".part", getCacheDir());
                            try (InputStream in = getContentResolver().openInputStream(file); OutputStream out = new FileOutputStream(copy)) {
                                pipe(in, out);
                            }
                            size = copy.length();
                        }
                        try (InputStream in = copy != null ? new FileInputStream(copy) : getContentResolver().openInputStream(file)) {
                            say = request("POST", "/upload", "name=" + Uri.encode(name) + "&n=" + (i + 1) + "&of=" + files.size(),
                                    in, size, "application/octet-stream");
                        }
                        sent++;
                    } catch (Exception error) {
                        say = "Couldn't send everything. Is Motorcycle Man open on your PC?";
                        break;
                    } finally {
                        if (copy != null) {
                            copy.delete();
                        }
                    }
                }
                finishOnUi(sent == files.size() && sent > 1 ? "Sent " + sent + " files to your PC's Downloads\\Motorcycle Man." : say);
            }
        }).start();
    }

    // One call to Motorcycle Man on the PC; its "say" comes back.
    private String request(String method, String path, String query, InputStream body, long length, String kind) throws Exception {
        URL url = new URL(base + path + "?" + (query.isEmpty() ? "" : query + "&") + "token=" + Uri.encode(token));
        HttpURLConnection connection = (HttpURLConnection) url.openConnection();
        connection.setConnectTimeout(6000);
        connection.setReadTimeout(120000);
        connection.setRequestMethod(method);
        if (body != null) {
            connection.setDoOutput(true);
            connection.setFixedLengthStreamingMode(length);
            connection.setRequestProperty("Content-Type", kind);
            try (OutputStream out = connection.getOutputStream()) {
                pipe(body, out);
            }
        }
        int code = connection.getResponseCode();
        InputStream answer = code < 400 ? connection.getInputStream() : connection.getErrorStream();
        ByteArrayOutputStream words = new ByteArrayOutputStream();
        if (answer != null) {
            try (InputStream in = answer) {
                pipe(in, words);
            }
        }
        connection.disconnect();
        String say = new JSONObject(words.size() > 0 ? words.toString("UTF-8") : "{}").optString("say", "");
        return !say.isEmpty() ? say : code < 400 ? "Done." : "The PC didn't take it.";
    }

    private String nameOf(Uri file, int index) {
        try (Cursor row = getContentResolver().query(file, new String[] {OpenableColumns.DISPLAY_NAME}, null, null, null)) {
            if (row != null && row.moveToFirst() && row.getString(0) != null) {
                return row.getString(0);
            }
        } catch (Exception ignored) {
            // some apps share files that can't be asked about
        }
        String last = file.getLastPathSegment();
        return last != null && !last.isEmpty() ? last : "Shared file " + (index + 1);
    }

    private long sizeOf(Uri file) {
        try (Cursor row = getContentResolver().query(file, new String[] {OpenableColumns.SIZE}, null, null, null)) {
            if (row != null && row.moveToFirst() && !row.isNull(0)) {
                return row.getLong(0);
            }
        } catch (Exception ignored) {
            // measured instead
        }
        return -1;
    }

    private static void pipe(InputStream in, OutputStream out) throws IOException {
        byte[] buffer = new byte[64 * 1024];
        int count;
        while ((count = in.read(buffer)) > 0) {
            out.write(buffer, 0, count);
        }
    }

    private void showProgress(String what) {
        progress = new AlertDialog.Builder(this, android.R.style.Theme_DeviceDefault_Dialog_Alert)
                .setTitle("Motorcycle Man").setMessage(what).setCancelable(false).show();
    }

    private void finishOnUi(final String say) {
        runOnUiThread(new Runnable() {
            @Override
            public void run() {
                if (progress != null) {
                    progress.dismiss();
                }
                done(say);
            }
        });
    }

    private void done(String say) {
        Toast.makeText(this, say, Toast.LENGTH_LONG).show();
        finish();
    }
}
