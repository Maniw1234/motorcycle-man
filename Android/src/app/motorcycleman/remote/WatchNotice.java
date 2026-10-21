package app.motorcycleman.remote;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.RemoteInput;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.graphics.drawable.Icon;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;

import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.SocketTimeoutException;
import java.net.URL;

// Motorcycle Man on a watch: a notification with "Tell PC" (a reply, typed or said) and "Stop". Wear OS and
// Galaxy watches show the phone's notifications with their buttons, so the watch talks to the PC through
// this phone, with no app of its own. The reply goes to the PC like a typed command, and its answer comes
// back in the notification.
public class WatchNotice extends BroadcastReceiver {
    static final String CHANNEL = "watch";
    static final int ID = 42;
    static final String KEY = "command";
    static final String TELL = "app.motorcycleman.remote.WATCH_TELL";
    static final String STOP = "app.motorcycleman.remote.WATCH_STOP";
    private static final int WAIT_MS = 8000;  // Android gives a notification's button about ten seconds

    static SharedPreferences prefs(Context context) {
        return context.getSharedPreferences("motorcycleman", Context.MODE_PRIVATE);
    }

    static boolean isOn(Context context) {
        return prefs(context).getBoolean("watch", false);
    }

    static void set(Context context, boolean on) {
        prefs(context).edit().putBoolean("watch", on).apply();
        if (on) {
            show(context, null);
        } else {
            context.getSystemService(NotificationManager.class).cancel(ID);
        }
    }

    @SuppressWarnings("deprecation")  // the older ways, for Android 7, and the watch's own button list
    static void show(Context context, String last) {
        NotificationManager manager = context.getSystemService(NotificationManager.class);
        if (Build.VERSION.SDK_INT >= 26) {
            manager.createNotificationChannel(new NotificationChannel(CHANNEL, "Motorcycle Man on your watch",
                    NotificationManager.IMPORTANCE_LOW));
        }
        Icon icon = Icon.createWithResource(context, R.drawable.ic_tile);
        RemoteInput input = new RemoteInput.Builder(KEY).setLabel("What should I do?")
                .setChoices(new CharSequence[] {"Pause the music", "What are my reminders", "Lock the PC"}).build();
        Notification.Action tell = new Notification.Action.Builder(icon, "Tell PC", button(context, TELL, 10, true))
                .addRemoteInput(input).build();
        Notification.Action stop = new Notification.Action.Builder(icon, "Stop", button(context, STOP, 11, false)).build();
        String text = last != null ? last : "Reply to tell your PC what to do, here or on your watch.";
        Notification.Builder note = Build.VERSION.SDK_INT >= 26 ? new Notification.Builder(context, CHANNEL)
                : new Notification.Builder(context).setPriority(Notification.PRIORITY_LOW);
        note.setSmallIcon(R.drawable.ic_tile)
                .setContentTitle("Motorcycle Man")
                .setContentText(text)
                .setStyle(new Notification.BigTextStyle().bigText(text))
                .setOnlyAlertOnce(true)
                .setShowWhen(last != null)
                .setContentIntent(PendingIntent.getActivity(context, 12, new Intent(context, MainActivity.class),
                        PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE))
                .addAction(tell)
                .addAction(stop)
                .extend(new Notification.WearableExtender().addAction(tell).addAction(stop));
        manager.notify(ID, note.build());
    }

    private static PendingIntent button(Context context, String action, int code, boolean reply) {
        Intent intent = new Intent(context, WatchNotice.class).setAction(action);
        // A reply is written into the intent, so it has to stay changeable (Android 12 and newer ask).
        int flags = PendingIntent.FLAG_UPDATE_CURRENT
                | (reply && Build.VERSION.SDK_INT >= 31 ? PendingIntent.FLAG_MUTABLE : PendingIntent.FLAG_IMMUTABLE);
        return PendingIntent.getBroadcast(context, code, intent, flags);
    }

    @Override
    public void onReceive(final Context context, Intent intent) {
        Bundle results = RemoteInput.getResultsFromIntent(intent);
        CharSequence said = results == null ? null : results.getCharSequence(KEY);
        final boolean stopping = STOP.equals(intent.getAction());
        final String command = said == null ? "" : said.toString().trim();
        if (!stopping && command.isEmpty()) {
            show(context, null);
            return;
        }
        show(context, stopping ? "Stopping..." : "“" + command + "” …");
        final PendingResult pending = goAsync();
        new Thread(new Runnable() {
            @Override
            public void run() {
                String answer;
                try {
                    String reply = askPc(context, stopping ? "/stop" : "/cmd", stopping ? "" : "text=" + Uri.encode(command));
                    answer = stopping ? "Stopped." : "“" + command + "”\n" + new JSONObject(reply).optString("reply", "Done.");
                } catch (SocketTimeoutException slow) {
                    answer = "“" + command + "”\nWorking on it on your PC.";
                } catch (Exception error) {
                    answer = "Couldn't reach your PC. Is Motorcycle Man open?";
                }
                show(context, answer);
                pending.finish();
            }
        }).start();
    }

    static String askPc(Context context, String path, String query) throws Exception {
        Uri saved = Uri.parse(prefs(context).getString("phone_url", ""));
        String token = saved.getQueryParameter("token");
        if (saved.getHost() == null || token == null) {
            throw new IllegalStateException("not connected yet");
        }
        URL url = new URL(saved.getScheme() + "://" + saved.getEncodedAuthority() + path + "?"
                + (query.isEmpty() ? "" : query + "&") + "token=" + Uri.encode(token));
        HttpURLConnection connection = (HttpURLConnection) url.openConnection();
        connection.setConnectTimeout(4000);
        connection.setReadTimeout(WAIT_MS);
        try (InputStream in = connection.getInputStream()) {
            ByteArrayOutputStream body = new ByteArrayOutputStream();
            byte[] buffer = new byte[8192];
            int count;
            while ((count = in.read(buffer)) > 0) {
                body.write(buffer, 0, count);
            }
            return body.toString("UTF-8");
        } finally {
            connection.disconnect();
        }
    }
}
