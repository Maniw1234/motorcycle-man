package app.motorcycleman.remote;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.media.AudioAttributes;
import android.media.AudioManager;
import android.media.MediaPlayer;
import android.media.RingtoneManager;
import android.net.Uri;
import android.os.Build;
import android.os.Handler;
import android.os.Looper;
import android.os.VibrationEffect;
import android.os.Vibrator;
import android.provider.Settings;

// Find my phone: the PC asked this phone to ring. It plays the alarm sound at full alarm volume, so silent mode
// doesn't hide it, and vibrates, for 30 seconds or until Found it is tapped or the app is opened. The alarm's
// volume goes back to what it was.
public class FindPhone extends BroadcastReceiver {
    static final String CHANNEL = "find";
    static final String FOUND = "app.motorcycleman.remote.FOUND_IT";
    static final int ID = 43;
    private static final long RINGS_FOR = 30000;
    private static final Handler HANDLER = new Handler(Looper.getMainLooper());
    private static MediaPlayer player;
    private static int volumeBefore = -1;
    private static Runnable quiet;  // stops the ringing when its time is up

    @SuppressWarnings("deprecation")  // the older ways, for Android 7
    static synchronized void ring(Context context) {
        final Context app = context.getApplicationContext();
        stop(app);
        AudioManager audio = app.getSystemService(AudioManager.class);
        try {
            volumeBefore = audio.getStreamVolume(AudioManager.STREAM_ALARM);
            audio.setStreamVolume(AudioManager.STREAM_ALARM, audio.getStreamMaxVolume(AudioManager.STREAM_ALARM), 0);
        } catch (RuntimeException e) {
            volumeBefore = -1;  // this phone won't have it changed
        }
        Uri sound = RingtoneManager.getActualDefaultRingtoneUri(app, RingtoneManager.TYPE_ALARM);
        if (sound == null) {
            sound = RingtoneManager.getActualDefaultRingtoneUri(app, RingtoneManager.TYPE_RINGTONE);
        }
        if (sound == null) {
            sound = Settings.System.DEFAULT_RINGTONE_URI;
        }
        try {
            player = new MediaPlayer();
            player.setAudioAttributes(new AudioAttributes.Builder().setUsage(AudioAttributes.USAGE_ALARM)
                    .setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION).build());
            player.setDataSource(app, sound);
            player.setLooping(true);
            player.prepare();
            player.start();
        } catch (Exception e) {
            if (player != null) {
                player.release();
            }
            player = null;  // no sound to play: it still vibrates and shows the notice
        }
        Vibrator vibrator = app.getSystemService(Vibrator.class);
        if (vibrator != null) {
            long[] pattern = {0, 700, 500};
            if (Build.VERSION.SDK_INT >= 26) {
                vibrator.vibrate(VibrationEffect.createWaveform(pattern, 0));
            } else {
                vibrator.vibrate(pattern, 0);
            }
        }
        NotificationManager manager = app.getSystemService(NotificationManager.class);
        if (Build.VERSION.SDK_INT >= 26) {
            manager.createNotificationChannel(new NotificationChannel(CHANNEL, "Find my phone", NotificationManager.IMPORTANCE_HIGH));
        }
        PendingIntent found = PendingIntent.getBroadcast(app, 30, new Intent(app, FindPhone.class).setAction(FOUND),
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
        Notification.Builder note = Build.VERSION.SDK_INT >= 26 ? new Notification.Builder(app, CHANNEL)
                : new Notification.Builder(app).setPriority(Notification.PRIORITY_HIGH);
        note.setSmallIcon(R.drawable.ic_tile)
                .setContentTitle("Your PC is looking for this phone")
                .setContentText("Tap Found it to stop the ringing.")
                .setCategory(Notification.CATEGORY_ALARM)
                .setOngoing(true)
                .setContentIntent(found)
                .addAction(new Notification.Action.Builder(null, "Found it", found).build());
        try {
            manager.notify(ID, note.build());
        } catch (RuntimeException ignored) {
            // notifications aren't allowed: the ringing stops by itself
        }
        quiet = new Runnable() {
            @Override
            public void run() {
                stop(app);
            }
        };
        HANDLER.postDelayed(quiet, RINGS_FOR);
    }

    static synchronized void stop(Context context) {
        if (quiet != null) {
            HANDLER.removeCallbacks(quiet);
            quiet = null;
        }
        if (context == null) {
            return;
        }
        Context app = context.getApplicationContext();
        if (player != null) {
            try {
                player.stop();
            } catch (RuntimeException ignored) {
                // it had already stopped
            }
            player.release();
            player = null;
        }
        if (volumeBefore >= 0) {
            try {
                app.getSystemService(AudioManager.class).setStreamVolume(AudioManager.STREAM_ALARM, volumeBefore, 0);
            } catch (RuntimeException ignored) {
                // the phone won't have it changed back
            }
            volumeBefore = -1;
        }
        Vibrator vibrator = app.getSystemService(Vibrator.class);
        if (vibrator != null) {
            vibrator.cancel();
        }
        app.getSystemService(NotificationManager.class).cancel(ID);
    }

    @Override
    public void onReceive(Context context, Intent intent) {
        stop(context);
    }
}
