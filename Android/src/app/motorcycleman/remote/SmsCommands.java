package app.motorcycleman.remote;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.net.Uri;
import android.os.Build;
import android.provider.Telephony;
import android.telephony.SmsManager;
import android.telephony.SmsMessage;

import org.json.JSONObject;

import java.net.SocketTimeoutException;
import java.security.SecureRandom;
import java.util.Locale;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

// Texts to this phone that start with "MM" and its PIN ("MM 482913 pause the music") go to the PC as commands,
// and the answer is texted back. So anyone the user gives the PIN to can reach the PC from any phone, even one
// without the internet. Other texts are left alone; a number that sends wrong PINs is ignored for a day.
public class SmsCommands extends BroadcastReceiver {
    private static final Pattern COMMAND = Pattern.compile("^\\s*mm\\s+(\\d{6})\\s+(.+)$", Pattern.CASE_INSENSITIVE | Pattern.DOTALL);
    private static final int WRONG_LIMIT = 5;
    private static final int ALL_WRONG_LIMIT = 20;  // wrong PINs from all numbers together in a day (numbers can be faked)
    private static final long LOCKED_MS = 24 * 60 * 60 * 1000L;
    private static final int REPLY_LIMIT = 600;  // characters texted back (a few texts' worth)

    static SharedPreferences prefs(Context context) {
        return WatchNotice.prefs(context);
    }

    static boolean isOn(Context context) {
        return prefs(context).getBoolean("sms_on", false);
    }

    static String pin(Context context) {
        String pin = prefs(context).getString("sms_pin", "");
        if (!pin.matches("\\d{6}")) {
            pin = newPin(context);
        }
        return pin;
    }

    static String newPin(Context context) {
        String pin = String.format(Locale.ROOT, "%06d", new SecureRandom().nextInt(1000000));
        prefs(context).edit().putString("sms_pin", pin).apply();
        return pin;
    }

    static void set(Context context, boolean on) {
        prefs(context).edit().putBoolean("sms_on", on).apply();
    }

    @Override
    @SuppressWarnings("deprecation")  // the older way to read a text, for Android 7 and before
    public void onReceive(final Context context, Intent intent) {
        if (!isOn(context) || !Telephony.Sms.Intents.SMS_RECEIVED_ACTION.equals(intent.getAction())) {
            return;
        }
        SmsMessage[] parts = Telephony.Sms.Intents.getMessagesFromIntent(intent);
        if (parts == null || parts.length == 0) {
            return;
        }
        StringBuilder body = new StringBuilder();
        for (SmsMessage part : parts) {
            body.append(part.getMessageBody());
        }
        final String sender = parts[0].getOriginatingAddress();
        Matcher found = COMMAND.matcher(body.toString());
        if (sender == null || !found.matches() || locked(context, sender) || lockedForAll(context)) {
            return;
        }
        if (!found.group(1).equals(pin(context))) {
            wrong(context, sender);
            return;
        }
        final String command = found.group(2).trim();
        final PendingResult pending = goAsync();
        new Thread(new Runnable() {
            @Override
            public void run() {
                String answer;
                try {
                    answer = new JSONObject(WatchNotice.askPc(context, "/cmd", "text=" + Uri.encode(command))).optString("reply", "Done.");
                } catch (SocketTimeoutException slow) {
                    answer = "Working on it on the PC.";
                } catch (Exception error) {
                    answer = "Couldn't reach the PC. Is Motorcycle Man open?";
                }
                reply(context, sender, "Motorcycle Man: " + answer);
                pending.finish();
            }
        }).start();
    }

    private static boolean locked(Context context, String sender) {
        return System.currentTimeMillis() < prefs(context).getLong("sms_locked_" + sender, 0);
    }

    // A sender's number can be faked, so wrong PINs also count across all numbers: after 20 in a day, every text
    // command waits a day (otherwise changing the number each try would get around the lock above).
    private static boolean lockedForAll(Context context) {
        return System.currentTimeMillis() < prefs(context).getLong("sms_locked_all", 0);
    }

    private static void wrong(Context context, String sender) {
        SharedPreferences prefs = prefs(context);
        int wrong = prefs.getInt("sms_wrong_" + sender, 0) + 1;
        SharedPreferences.Editor edit = prefs.edit().putInt("sms_wrong_" + sender, wrong);
        if (wrong >= WRONG_LIMIT) {
            edit.putLong("sms_locked_" + sender, System.currentTimeMillis() + LOCKED_MS).putInt("sms_wrong_" + sender, 0);
        }
        long now = System.currentTimeMillis();
        boolean sameDay = now - prefs.getLong("sms_wrong_all_since", 0) < LOCKED_MS;
        int all = sameDay ? prefs.getInt("sms_wrong_all", 0) + 1 : 1;
        if (!sameDay) {
            edit.putLong("sms_wrong_all_since", now);
        }
        edit.putInt("sms_wrong_all", all);
        if (all >= ALL_WRONG_LIMIT) {
            edit.putLong("sms_locked_all", now + LOCKED_MS).putInt("sms_wrong_all", 0);
        }
        edit.apply();
    }

    @SuppressWarnings("deprecation")  // SmsManager.getDefault, for Android 11 and before
    private static void reply(Context context, String to, String text) {
        try {
            SmsManager sms = Build.VERSION.SDK_INT >= 31 ? context.getSystemService(SmsManager.class) : SmsManager.getDefault();
            String shortened = text.length() > REPLY_LIMIT ? text.substring(0, REPLY_LIMIT - 1) + "…" : text;
            sms.sendMultipartTextMessage(to, null, sms.divideMessage(shortened), null, null);
        } catch (Exception ignored) {
            // no texting on this device, or not allowed to
        }
    }
}
