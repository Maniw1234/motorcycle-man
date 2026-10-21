package app.motorcycleman.remote;

import android.app.DownloadManager;
import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.RemoteInput;
import android.content.ClipData;
import android.content.ClipboardManager;
import android.content.ComponentName;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.net.Uri;
import android.os.BatteryManager;
import android.os.Build;
import android.os.Bundle;
import android.os.Environment;
import android.os.Handler;
import android.os.Looper;
import android.provider.Settings;
import android.service.notification.NotificationListenerService;
import android.service.notification.StatusBarNotification;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.io.IOException;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.util.Arrays;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

// The phone's notifications on the PC: each new one goes to the PC this phone is connected to, which shows
// it by the buddy. Only while the user has given Motorcycle Man notification access in Android's settings and
// switched Phone alerts on in the page's menu. Ongoing ones (music, navigation, downloads), quiet group
// summaries, Android's own and Motorcycle Man's are left out, and the same text isn't sent twice. A message
// that can be answered (it has a reply button) can be answered from the PC; and while Phone alerts are on this
// phone keeps a question open with the PC for what it wants: an answer to send, or to ring (Find my phone).
public class NoticeListener extends NotificationListenerService {
    static final String KEY_ON = "notices_on";
    static final String KEY_FOR = "notices_for";  // the secret of the PC Phone alerts were turned on for
    static final String KEY_CLIP = "clip_last";  // a fingerprint of the last text the clipboard sync moved, either way
    private static final Set<String> SKIPPED = new HashSet<>(Arrays.asList(
            "android", "com.android.systemui", "com.android.providers.downloads", "com.google.android.gms"));
    private final ExecutorService sender = Executors.newSingleThreadExecutor();
    private final Map<String, String> sent = new LinkedHashMap<String, String>() {
        @Override
        protected boolean removeEldestEntry(Map.Entry<String, String> eldest) {
            return size() > 100;
        }
    };
    private final Map<String, Notification.Action> answerable = new LinkedHashMap<String, Notification.Action>() {
        @Override
        protected boolean removeEldestEntry(Map.Entry<String, Notification.Action> eldest) {
            return size() > 50;
        }
    };
    private int counted;
    private volatile boolean listening;
    private Thread inbox;

    static boolean allowed(Context context) {
        String listeners = Settings.Secure.getString(context.getContentResolver(), "enabled_notification_listeners");
        ComponentName me = new ComponentName(context, NoticeListener.class);
        return listeners != null && (listeners.contains(me.flattenToString()) || listeners.contains(me.flattenToShortString()));
    }

    static boolean isOn(Context context) {
        return alertsPc(context) != null && allowed(context);
    }

    // The PC Phone alerts go to: the saved link, as long as it's the PC they were turned on for. A link that a web
    // page or another app opened (see MainActivity.openFromOutside) never gets them unless they're turned on there.
    static Uri alertsPc(Context context) {
        SharedPreferences prefs = prefs(context);
        try {
            Uri link = Uri.parse(prefs.getString("phone_url", ""));
            String token = link.getQueryParameter("token");
            if (!prefs.getBoolean(KEY_ON, false) || link.getHost() == null || token == null
                    || !token.equals(prefs.getString(KEY_FOR, ""))) {
                return null;
            }
            return link;
        } catch (RuntimeException notALink) {
            return null;
        }
    }

    private static SharedPreferences prefs(Context context) {
        return context.getSharedPreferences("motorcycleman", MODE_PRIVATE);
    }

    @Override
    public void onNotificationPosted(StatusBarNotification posted) {
        Uri link = alertsPc(this);
        Notification notice = posted.getNotification();
        if (link == null || notice == null || posted.isOngoing()
                || (notice.flags & Notification.FLAG_GROUP_SUMMARY) != 0 || getPackageName().equals(posted.getPackageName())
                || SKIPPED.contains(posted.getPackageName())) {
            return;
        }
        Bundle extras = notice.extras;
        CharSequence title = extras.getCharSequence(Notification.EXTRA_TITLE);
        CharSequence text = extras.getCharSequence(Notification.EXTRA_BIG_TEXT);
        if (text == null) {
            text = extras.getCharSequence(Notification.EXTRA_TEXT);
        }
        if (title == null && text == null) {
            return;
        }
        String words = (title == null ? "" : title) + "\n" + (text == null ? "" : text);
        synchronized (sent) {
            if (words.equals(sent.get(posted.getKey()))) {
                return;  // the same notification updated with nothing new to read
            }
            sent.put(posted.getKey(), words);
        }
        Notification.Action reply = replyAction(notice);
        String id = null;
        if (reply != null) {
            synchronized (answerable) {
                id = "n" + (++counted);
                answerable.put(id, reply);
            }
        }
        final String body;
        try {
            JSONObject said = new JSONObject().put("app", appName(posted.getPackageName()))
                    .put("title", title == null ? "" : title.toString()).put("text", text == null ? "" : text.toString());
            if (id != null) {
                said.put("id", id).put("reply", true);
            }
            body = said.toString();
        } catch (JSONException e) {
            return;
        }
        final String to = link.getScheme() + "://" + link.getEncodedAuthority() + "/phone_notice?token="
                + PcFinder.encode(String.valueOf(link.getQueryParameter("token")));
        sender.execute(new Runnable() {
            @Override
            public void run() {
                post(to, body);
            }
        });
    }

    // The notification's own reply button (a chat app's "Reply"), if it has one that takes typed words.
    private static Notification.Action replyAction(Notification notice) {
        if (notice.actions == null) {
            return null;
        }
        for (Notification.Action action : notice.actions) {
            RemoteInput[] inputs = action.getRemoteInputs();
            if (inputs != null && action.actionIntent != null) {
                for (RemoteInput input : inputs) {
                    if (input.getAllowFreeFormInput()) {
                        return action;
                    }
                }
            }
        }
        return null;
    }

    // Something the PC needs the user to know now (its guard caught someone using it): a notification that makes noise.
    @SuppressWarnings("deprecation")  // the older way, for Android 7
    private void alert(String text) {
        NotificationManager manager = getSystemService(NotificationManager.class);
        if (manager == null || text.isEmpty()) {
            return;
        }
        if (Build.VERSION.SDK_INT >= 26) {
            manager.createNotificationChannel(new NotificationChannel("pc_alerts", "Alerts from your PC", NotificationManager.IMPORTANCE_HIGH));
        }
        Notification.Builder note = Build.VERSION.SDK_INT >= 26 ? new Notification.Builder(this, "pc_alerts")
                : new Notification.Builder(this).setPriority(Notification.PRIORITY_HIGH).setDefaults(Notification.DEFAULT_ALL);
        note.setSmallIcon(R.drawable.ic_tile)
                .setContentTitle("Motorcycle Man")
                .setContentText(text)
                .setStyle(new Notification.BigTextStyle().bigText(text))
                .setAutoCancel(true)
                .setContentIntent(PendingIntent.getActivity(this, 44, new Intent(this, MainActivity.class),
                        PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE));
        try {
            manager.notify(44, note.build());
        } catch (RuntimeException ignored) {
            // notifications aren't allowed for the app
        }
    }

    // Text copied on the PC, on this phone's clipboard (the clipboard sync). Remembered, so the app doesn't
    // send it straight back when it comes to the front.
    private void putOnClipboard(final String text) {
        if (text.isEmpty()) {
            return;
        }
        prefs(this).edit().putString(KEY_CLIP, String.valueOf(text.hashCode())).apply();
        new Handler(Looper.getMainLooper()).post(new Runnable() {
            @Override
            public void run() {
                ClipboardManager clipboard = getSystemService(ClipboardManager.class);
                if (clipboard != null) {
                    clipboard.setPrimaryClip(ClipData.newPlainText("From your PC", text));
                }
            }
        });
    }

    // A file sent from the PC (File Explorer's Send to > My phone): Android's download manager saves it in
    // Downloads and says so in a notification. (Before Android 10 only into the app's own Downloads.)
    private void save(String url, String name) {
        String safe = name.replaceAll("[\\\\/:*?\"<>|]", "_");
        try {
            DownloadManager.Request request = new DownloadManager.Request(Uri.parse(url));
            request.setTitle(safe);
            request.setDescription("From your PC");
            request.setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED);
            if (Build.VERSION.SDK_INT >= 29) {
                request.setDestinationInExternalPublicDir(Environment.DIRECTORY_DOWNLOADS, safe);
            } else {
                request.setDestinationInExternalFilesDir(this, Environment.DIRECTORY_DOWNLOADS, safe);
            }
            getSystemService(DownloadManager.class).enqueue(request);
        } catch (RuntimeException ignored) {
            // the phone can't save it now; it's still in the page's Send panel
        }
    }

    // This phone's battery, for the PC to show and warn about: "&battery=64&charging=1", or "" when Android won't say.
    private String battery() {
        Intent state = registerReceiver(null, new IntentFilter(Intent.ACTION_BATTERY_CHANGED));
        if (state == null) {
            return "";
        }
        int level = state.getIntExtra(BatteryManager.EXTRA_LEVEL, -1);
        int scale = state.getIntExtra(BatteryManager.EXTRA_SCALE, 100);
        if (level < 0 || scale <= 0) {
            return "";
        }
        boolean charging = state.getIntExtra(BatteryManager.EXTRA_PLUGGED, 0) != 0;
        return "&battery=" + (level * 100 / scale) + "&charging=" + (charging ? "1" : "0");
    }

    // Sends words through a notification's reply button, as if they were typed into it on this phone.
    private boolean answer(String id, String words) {
        Notification.Action action;
        synchronized (answerable) {
            action = answerable.get(id);
        }
        if (action == null || words.isEmpty()) {
            return false;
        }
        RemoteInput[] inputs = action.getRemoteInputs();
        Bundle typed = new Bundle();
        for (RemoteInput input : inputs) {
            typed.putCharSequence(input.getResultKey(), words);
        }
        Intent intent = new Intent();
        RemoteInput.addResultsToIntent(inputs, intent, typed);
        try {
            action.actionIntent.send(this, 0, intent);
            return true;
        } catch (PendingIntent.CanceledException | RuntimeException e) {
            return false;
        }
    }

    @Override
    public void onListenerConnected() {
        listening = true;
        inbox = new Thread(new Runnable() {
            @Override
            public void run() {
                listen();
            }
        }, "pc-inbox");
        inbox.start();
    }

    @Override
    public void onListenerDisconnected() {
        listening = false;
        if (inbox != null) {
            inbox.interrupt();
        }
    }

    // What the PC wants of this phone, asked with a question the PC holds open for up to 50 seconds while there's
    // nothing, so waiting costs next to nothing. The answer about the last thing it asked for goes with the next.
    private void listen() {
        String done = "";
        long pause = 5000;
        while (listening) {
            try {
                Uri link = alertsPc(this);
                String token = link == null ? null : link.getQueryParameter("token");
                if (link == null || token == null) {
                    Thread.sleep(30000);
                    continue;
                }
                String reply = PcFinder.get(link.getScheme() + "://" + link.getEncodedAuthority() + "/phone_inbox?wait=50&token="
                        + PcFinder.encode(token) + (done.isEmpty() ? "" : "&done=" + PcFinder.encode(done)) + battery(), 5000, 70000);
                if (reply == null) {  // the PC is off or away: ask again later, less often
                    Thread.sleep(pause);
                    pause = Math.min(pause * 2, 120000);
                    continue;
                }
                pause = 5000;
                done = "";
                JSONArray todo = new JSONObject(reply).optJSONArray("do");
                for (int i = 0; todo != null && i < todo.length(); i++) {
                    JSONObject job = todo.optJSONObject(i);
                    String kind = job == null ? "" : job.optString("kind");
                    if ("ring".equals(kind)) {
                        FindPhone.ring(this);
                    } else if ("alert".equals(kind)) {
                        alert(job.optString("text"));
                    } else if ("clip".equals(kind)) {
                        putOnClipboard(job.optString("text"));
                    } else if ("file".equals(kind)) {
                        save(link.getScheme() + "://" + link.getEncodedAuthority() + "/file?id=" + PcFinder.encode(job.optString("id"))
                                + "&token=" + PcFinder.encode(token), job.optString("name", "From your PC"));
                    } else if ("reply".equals(kind)) {
                        String id = job.optString("id");
                        done = id + (answer(id, job.optString("text").trim()) ? ":1" : ":0");
                    }
                }
            } catch (InterruptedException e) {
                return;
            } catch (JSONException | RuntimeException e) {
                try {
                    Thread.sleep(pause);
                } catch (InterruptedException stopped) {
                    return;
                }
            }
        }
    }

    private String appName(String packageName) {
        PackageManager packages = getPackageManager();
        try {
            return packages.getApplicationLabel(packages.getApplicationInfo(packageName, 0)).toString();
        } catch (PackageManager.NameNotFoundException e) {
            return packageName;
        }
    }

    static void post(String url, String body) {
        HttpURLConnection connection = null;
        try {
            connection = (HttpURLConnection) new URL(url).openConnection();
            connection.setConnectTimeout(4000);
            connection.setReadTimeout(6000);
            connection.setDoOutput(true);
            connection.setRequestMethod("POST");
            connection.setRequestProperty("Content-Type", "application/json; charset=utf-8");
            byte[] bytes = body.getBytes("UTF-8");
            connection.setFixedLengthStreamingMode(bytes.length);
            try (OutputStream out = connection.getOutputStream()) {
                out.write(bytes);
            }
            connection.getResponseCode();
        } catch (IOException | RuntimeException ignored) {
            // the PC is off or away: this one is skipped
        } finally {
            if (connection != null) {
                connection.disconnect();
            }
        }
    }
}
