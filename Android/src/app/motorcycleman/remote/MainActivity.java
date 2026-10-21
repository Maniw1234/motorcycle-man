package app.motorcycleman.remote;

import android.Manifest;
import android.app.Activity;
import android.app.AlertDialog;
import android.app.Dialog;
import android.app.DownloadManager;
import android.app.PendingIntent;
import android.bluetooth.BluetoothAdapter;
import android.bluetooth.BluetoothDevice;
import android.bluetooth.BluetoothManager;
import android.content.ClipData;
import android.content.ClipDescription;
import android.content.ClipboardManager;
import android.content.DialogInterface;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageInfo;
import android.content.pm.PackageInstaller;
import android.content.pm.PackageManager;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.ColorDrawable;
import android.graphics.drawable.GradientDrawable;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Environment;
import android.os.Handler;
import android.os.Looper;
import android.provider.Settings;
import android.speech.RecognitionListener;
import android.speech.RecognizerIntent;
import android.speech.SpeechRecognizer;
import android.speech.tts.TextToSpeech;
import android.text.InputFilter;
import android.text.InputType;
import android.view.KeyEvent;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.view.Window;
import android.view.WindowManager;
import android.view.inputmethod.EditorInfo;
import android.webkit.DownloadListener;
import android.webkit.JavascriptInterface;
import android.webkit.URLUtil;
import android.webkit.ValueCallback;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import android.webkit.WebChromeClient;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.EditText;
import android.widget.FrameLayout;
import android.widget.ImageView;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.io.File;
import java.io.FileInputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.security.SecureRandom;
import java.util.ArrayList;
import java.util.List;

// Shows Motorcycle Man's phone page full screen. The app finds Motorcycle Man on the Wi-Fi by itself and asks the PC to
// let it in (the user taps Allow there once, or types the PC's code here; BlueStacks on that PC is let
// in at once); the page's address can also come from the page's "App"
// button, a scanned QR code on Android 11 and older, or be pasted by hand. If the PC's address
// changes, the app finds it again with the link's secret it already has.
// The app's web view has no speech API, so the phone's own speech recognition listens for the page.
// The app updates itself from the PC, or from GitHub's newest release: when either has a newer app, it offers
// it, downloads it and hands it to Android's installer.
public class MainActivity extends Activity {
    // What the widget, the quick-settings tile and the share sheet ask the page to do: "listen", "screen" or
    // "remote" (EXTRA_DO), or a command to ask Motorcycle Man (EXTRA_ASK).
    static final String EXTRA_DO = "app.motorcycleman.remote.DO";
    static final String EXTRA_ASK = "app.motorcycleman.remote.ASK";
    private static final String KEY_URL = "phone_url";
    private static final String KEY_PCS = "pcs";  // every PC this phone has used, newest first: [{"name", "url", "mac", "broadcast"}]
    private static final int MOST_PCS = 8;
    private static final String KEY_UPDATE_LATER = "update_later";  // when the user last said Not now to an update
    private static final String KEY_APP_VERSION = "app_version";  // this app's version when it last started
    private static final String INSTALL_STATUS = "app.motorcycleman.remote.INSTALL_STATUS";  // Android's answer about an update
    private static final String UPDATE_FILE = "MotorcycleMan-update.apk";  // the new app, downloaded into the app's cache
    private static final long UPDATE_REST_MS = 6 * 60 * 60 * 1000L;  // after Not now, it asks again this much later
    private static final String KEY_GITHUB_CHECKED = "github_checked";  // when GitHub last answered about its app
    private static final long GITHUB_CHECK_EVERY_MS = 6 * 60 * 60 * 1000L;
    private static final long GITHUB_RETRY_MS = 10 * 60 * 1000L;  // after GitHub couldn't be reached
    private static final String NOT_FOUND = "Couldn't find Motorcycle Man on this Wi-Fi. Open Motorcycle Man on your PC and make sure "
            + "the phone is on the same Wi-Fi; this keeps looking. Or paste the link from the Motorcycle Man window below.";
    private static final String ALLOW_MICROPHONE = "Allow Motorcycle Man to use the microphone, then hold the helmet again.";

    private WebView web;
    private ScrollView setup;
    private TextView setupMessage;
    private TextView codeView;
    private LinearLayout typedRow;
    private EditText typedCode;
    private Button typedConnect;
    private LinearLayout choices;
    private Button findButton;
    private EditText urlInput;
    private SpeechRecognizer recognizer;
    private TextToSpeech voice;  // Motorcycle Man's spoken replies, in the phone's own voice
    private boolean voiceReady;
    private String voiceWaiting;  // what to say once the voice has started up
    private final Handler handler = new Handler(Looper.getMainLooper());
    private boolean searching;
    private boolean pageReady;  // Motorcycle Man's page has loaded, so what the widget asked for can be done
    private String pendingDo;
    private String pendingAsk;
    private final Runnable doPending = new Runnable() {
        @Override
        public void run() {
            if (pendingDo != null && !pendingDo.isEmpty()) {
                callPage("appAction", JSONObject.quote(pendingDo));
            }
            if (pendingAsk != null) {
                callPage("appAsk", JSONObject.quote(pendingAsk));
            }
            pendingDo = pendingAsk = null;
        }
    };
    private long lastQuietSearch;
    private int appVersion;  // this app's own version number and name
    private String appVersionName = "";
    private boolean updated;  // this app was updated since it last started; the page says so once it's open
    private boolean resumed;
    private boolean checkingUpdate;
    private long gitHubTried;  // when the app last asked GitHub, answered or not
    private static final int PICK_FILES = 7;  // Android's file picker, for files to send to the PC
    private static final int ASK_NOTIFICATIONS = 2;  // asking to show the watch's notification
    private static final int ASK_SMS = 3;  // asking to read and send texts, for commands by text message
    private static final int ASK_BLUETOOTH = 4;  // asking to use Bluetooth, for the PC without Wi-Fi
    private Button bluetoothButton;
    private BluetoothTunnel tunnel;  // the phone page over Bluetooth, while it's open
    private String bluetoothUrl;
    private ValueCallback<Uri[]> pickedFiles;  // where the picked files go, while the picker is open
    private Dialog updateDialog;  // "Update the Motorcycle Man app", while it's up
    private TextView updateMessage;
    private LinearLayout updateButtons;
    private AppUpdate.Offer updateOffer;  // the app on offer, and where it comes from
    private File downloadedUpdate;  // waits for the app to be in front before Android is asked to install it
    private int updateSession = -1;  // the update Android is installing
    private final Runnable searchAgain = new Runnable() {
        @Override
        public void run() {
            if (setup.getVisibility() == View.VISIBLE && !searching) {
                findPc(savedToken());
            }
        }
    };

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        requestWindowFeature(Window.FEATURE_NO_TITLE);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        noteVersion();
        if (WatchNotice.isOn(this)) {
            WatchNotice.show(this, null);  // back, if it was swiped away
        }
        new File(getCacheDir(), UPDATE_FILE).delete();  // left over from the last update
        buildViews();
        noteRequest(getIntent());
        trustCurrentPcForAlerts();
        String outside = urlFromIntent(getIntent());
        String url = prefs().getString(KEY_URL, null);
        if (outside != null) {
            openFromOutside(outside, false);
        } else if (url == null) {
            findPc(null);
        } else {
            openOrFind(url);
        }
    }

    // Opens the saved link if Motorcycle Man answers there; otherwise starts looking right away (the PC may
    // have a new address), instead of a blank page while the old address times out.
    private void openOrFind(final String url) {
        final Uri uri = Uri.parse(url);
        final String token = uri.getQueryParameter("token");
        showSetup("Connecting to Motorcycle Man...");
        new Thread(new Runnable() {
            @Override
            public void run() {
                final boolean works = token != null
                        && PcFinder.answers(uri.getScheme() + "://" + uri.getEncodedAuthority(), token);
                runOnUiThread(new Runnable() {
                    @Override
                    public void run() {
                        if (works) {
                            open(url);
                        } else {
                            findPc(token);
                        }
                    }
                });
            }
        }).start();
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        if (INSTALL_STATUS.equals(intent.getAction())) {
            installerAnswered(intent);
            return;
        }
        noteRequest(intent);
        String url = urlFromIntent(intent);
        if (url != null) {
            openFromOutside(url, true);
        } else if (pageReady) {
            handler.post(doPending);
        }
    }

    private void noteRequest(Intent intent) {
        if (intent != null && intent.hasExtra(EXTRA_DO)) {
            pendingDo = intent.getStringExtra(EXTRA_DO);
        }
        if (intent != null && intent.hasExtra(EXTRA_ASK)) {
            pendingAsk = intent.getStringExtra(EXTRA_ASK);
        }
    }

    // Clipboard sync: what was copied on this phone goes to the PC as the app comes to the front (Android lets only
    // the app in front read the clipboard). Its time is looked at first, so the clipboard itself is read (and
    // Android says so) only when something new was copied; what the PC sent, and what's marked private, stay put.
    @Override
    public void onWindowFocusChanged(boolean focused) {
        super.onWindowFocusChanged(focused);
        if (focused) {
            sendClipboard();
        }
    }

    private void sendClipboard() {
        final Uri link = Uri.parse(prefs().getString(KEY_URL, ""));
        final String token = link.getQueryParameter("token");
        ClipboardManager clipboard = getSystemService(ClipboardManager.class);
        if (link.getHost() == null || token == null || clipboard == null || !clipboard.hasPrimaryClip()) {
            return;
        }
        ClipDescription about = clipboard.getPrimaryClipDescription();
        long when = about != null && Build.VERSION.SDK_INT >= 26 ? about.getTimestamp() : 0;
        if (about == null || (when != 0 && when == prefs().getLong("clip_time", 0))) {
            return;
        }
        prefs().edit().putLong("clip_time", when).apply();
        if (!about.hasMimeType(ClipDescription.MIMETYPE_TEXT_PLAIN) && !about.hasMimeType(ClipDescription.MIMETYPE_TEXT_HTML)) {
            return;
        }
        if (about.getExtras() != null && about.getExtras().getBoolean("android.content.extra.IS_SENSITIVE")) {
            return;  // a password app marked it private
        }
        ClipData clip = clipboard.getPrimaryClip();
        CharSequence text = clip == null || clip.getItemCount() == 0 ? null : clip.getItemAt(0).getText();
        final String words = text == null ? "" : text.toString();
        if (words.trim().isEmpty() || String.valueOf(words.hashCode()).equals(prefs().getString(NoticeListener.KEY_CLIP, ""))) {
            return;
        }
        prefs().edit().putString(NoticeListener.KEY_CLIP, String.valueOf(words.hashCode())).apply();
        final String body;
        try {
            body = new JSONObject().put("text", words).toString();
        } catch (JSONException e) {
            return;
        }
        new Thread(new Runnable() {
            @Override
            public void run() {
                NoticeListener.post(link.getScheme() + "://" + link.getEncodedAuthority() + "/clip_sync?token=" + PcFinder.encode(token), body);
            }
        }).start();
    }

    @Override
    protected void onResume() {
        super.onResume();
        resumed = true;
        FindPhone.stop(this);  // found: the app is open
        if (pageReady && prefs().getBoolean(NoticeListener.KEY_ON, false)) {
            callPage("onNotices", String.valueOf(NoticeListener.isOn(this)));  // back from Android's settings
        }
        if (downloadedUpdate != null) {
            installUpdate();  // it finished downloading while the app was in the background
        }
        checkGitHub();
    }

    @Override
    protected void onPause() {
        super.onPause();
        resumed = false;
        if (recognizer != null) {
            recognizer.cancel();
        }
        if (voice != null) {
            voice.stop();
        }
    }

    @Override
    protected void onDestroy() {
        if (recognizer != null) {
            recognizer.destroy();
        }
        if (voice != null) {
            voice.shutdown();
        }
        if (tunnel != null) {
            tunnel.close();
        }
        closeUpdate();
        super.onDestroy();
    }

    @Override
    public void onRequestPermissionsResult(int requestCode, String[] permissions, int[] results) {
        boolean allowed = results.length > 0 && results[0] == PackageManager.PERMISSION_GRANTED;
        if (requestCode == ASK_NOTIFICATIONS) {
            WatchNotice.set(this, allowed);
            callPage("onWatch", String.valueOf(allowed));
        } else if (requestCode == ASK_BLUETOOTH) {
            if (allowed) {
                connectBluetooth();
            }
        } else if (requestCode == ASK_SMS) {
            SmsCommands.set(this, allowed && results.length > 1 && results[1] == PackageManager.PERMISSION_GRANTED);
            showTexts();
        } else if (allowed) {
            callPage("onAndroidNotice", JSONObject.quote("Microphone allowed. Tap the helmet and talk."));
        }
    }

    // Commands by text message (see SmsCommands): on or off, and the PIN to give to whoever may use it.
    private void showTexts() {
        final boolean on = SmsCommands.isOn(this);
        final String pin = SmsCommands.pin(this);
        String text = on
                ? "On. Anyone you give the PIN to can text this phone, even from a basic phone:\n\nMM " + pin
                  + " pause the music\n\nThe PC's answer is texted back (your plan's texts). Other texts are left alone."
                : "Let people you trust control your PC by texting this phone, even from a phone without the internet. "
                  + "They start the text with MM and a PIN, and the answer is texted back.";
        new AlertDialog.Builder(this, android.R.style.Theme_DeviceDefault_Dialog_Alert)
                .setTitle("Texts to your PC")
                .setMessage(text)
                .setPositiveButton(on ? "Turn off" : "Turn on", new DialogInterface.OnClickListener() {
                    @Override
                    public void onClick(DialogInterface dialog, int which) {
                        if (on) {
                            SmsCommands.set(MainActivity.this, false);
                        } else if (checkSelfPermission(Manifest.permission.RECEIVE_SMS) != PackageManager.PERMISSION_GRANTED
                                || checkSelfPermission(Manifest.permission.SEND_SMS) != PackageManager.PERMISSION_GRANTED) {
                            requestPermissions(new String[] {Manifest.permission.RECEIVE_SMS, Manifest.permission.SEND_SMS}, ASK_SMS);
                        } else {
                            SmsCommands.set(MainActivity.this, true);
                            showTexts();
                        }
                    }
                })
                .setNeutralButton(on ? "New PIN" : null, new DialogInterface.OnClickListener() {
                    @Override
                    public void onClick(DialogInterface dialog, int which) {
                        SmsCommands.newPin(MainActivity.this);
                        showTexts();
                    }
                })
                .setNegativeButton("Close", null)
                .show();
    }

    // The watch's notification (see WatchNotice); Android 13 and newer ask the user first.
    // Phone alerts on the PC need notification access, which only Android's settings can give: the switch
    // is kept, and the page hears how it stands when the user comes back (see onResume).
    private void setNotices(boolean on) {
        SharedPreferences.Editor edit = prefs().edit().putBoolean(NoticeListener.KEY_ON, on);
        if (on && savedToken() != null) {
            edit.putString(NoticeListener.KEY_FOR, savedToken());  // this PC, and no other, gets them
        }
        edit.apply();
        if (on && !NoticeListener.allowed(this)) {
            try {
                startActivity(new Intent(Settings.ACTION_NOTIFICATION_LISTENER_SETTINGS));
                callPage("onNotices", "false, true");
                return;
            } catch (RuntimeException ignored) {
                // no such settings screen on this phone
            }
        }
        callPage("onNotices", String.valueOf(NoticeListener.isOn(this)));
    }

    private void setWatch(boolean on) {
        if (on && Build.VERSION.SDK_INT >= 33
                && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(new String[] {Manifest.permission.POST_NOTIFICATIONS}, ASK_NOTIFICATIONS);
            return;
        }
        WatchNotice.set(this, on);
        callPage("onWatch", String.valueOf(on));
    }

    private static String urlFromIntent(Intent intent) {
        Uri data = intent == null ? null : intent.getData();
        if (data == null) {
            return null;
        }
        String text = data.toString();
        // The page's App button opens motorcycleman://<pc-address>:<port>/?token=..., or with a name
        // like ….trycloudflare.com for Motorcycle Man's internet link, which is https.
        if ("motorcycleman".equals(data.getScheme())) {
            String host = data.getHost() == null ? "" : data.getHost();
            text = (host.matches("[0-9.]+") ? "http" : "https") + text.substring("motorcycleman".length());
        }
        return validUrl(text);
    }

    // Motorcycle Man's phone links look like http://192.168.1.20:8080/?token=... on the Wi-Fi, or
    // https://<words>.trycloudflare.com/?token=... through its internet link.
    private static String validUrl(String text) {
        if (text == null) {
            return null;
        }
        Uri uri = Uri.parse(text.trim());
        boolean web = "http".equals(uri.getScheme()) || "https".equals(uri.getScheme());
        if (!web || uri.getHost() == null || uri.getQueryParameter("token") == null) {
            return null;
        }
        return uri.toString();
    }

    // A link from outside the app (a scanned QR code or the page's App button, but also any web page or app, since
    // Android lets anyone open these) for a PC this phone hasn't connected to is followed only once the user says
    // so: a stranger's link could otherwise put their own "PC" in place of the user's (found in October 2026).
    private void openFromOutside(final String url, final boolean running) {
        if (knownToken(tokenOf(url))) {
            follow(url, running);
            return;
        }
        if (!running) {
            showSetup("Connect to a new PC?");
        }
        new AlertDialog.Builder(this, android.R.style.Theme_DeviceDefault_Dialog_Alert)
                .setTitle("Connect to a new PC?")
                .setMessage("This link is for Motorcycle Man at " + Uri.parse(url).getHost() + ".\n\nConnect only if you "
                        + "just scanned or tapped the code or link of your own PC.")
                .setPositiveButton("Connect", new DialogInterface.OnClickListener() {
                    @Override
                    public void onClick(DialogInterface dialog, int which) {
                        follow(url, running);
                    }
                })
                .setNegativeButton("Cancel", new DialogInterface.OnClickListener() {
                    @Override
                    public void onClick(DialogInterface dialog, int which) {
                        stayPut(running);
                    }
                })
                .setOnCancelListener(new DialogInterface.OnCancelListener() {
                    @Override
                    public void onCancel(DialogInterface dialog) {
                        stayPut(running);
                    }
                })
                .show();
    }

    private void follow(String url, boolean running) {
        if (running) {
            open(url);
        } else {
            openOrFind(url);
        }
    }

    private void stayPut(boolean running) {
        if (running) {
            return;  // the page that was open stays
        }
        String saved = prefs().getString(KEY_URL, null);
        if (saved == null) {
            findPc(null);
        } else {
            openOrFind(saved);
        }
    }

    // Whether this phone has connected to the PC with this secret before.
    private boolean knownToken(String token) {
        if (token == null) {
            return false;
        }
        if (token.equals(savedToken())) {
            return true;
        }
        JSONArray pcs = knownPcs();
        for (int i = 0; i < pcs.length(); i++) {
            JSONObject pc = pcs.optJSONObject(i);
            if (pc != null && token.equals(tokenOf(pc.optString("url")))) {
                return true;
            }
        }
        return false;
    }

    // Phone alerts go only to the PC they were turned on for (see NoticeListener.alertsPc). Coming from an earlier
    // version, where they followed whichever PC was saved, they stay with the PC saved now.
    private void trustCurrentPcForAlerts() {
        String token = savedToken();
        if (token != null && prefs().getBoolean(NoticeListener.KEY_ON, false) && !prefs().contains(NoticeListener.KEY_FOR)) {
            prefs().edit().putString(NoticeListener.KEY_FOR, token).apply();
        }
    }

    private boolean isPcAddress(Uri uri) {
        Uri saved = Uri.parse(prefs().getString(KEY_URL, ""));
        if (uri.getHost() != null && uri.getHost().equals(saved.getHost()) && uri.getPort() == saved.getPort()) {
            return true;
        }
        Uri bluetooth = bluetoothUrl == null ? null : Uri.parse(bluetoothUrl);  // the page over Bluetooth
        return bluetooth != null && "127.0.0.1".equals(uri.getHost()) && uri.getPort() == bluetooth.getPort();
    }

    // The phone page over Bluetooth (see BluetoothTunnel), for when the phone and the PC share no Wi-Fi or
    // internet. The phone must be paired with the PC, and have connected once on the Wi-Fi, for the link's secret.
    private BluetoothAdapter bluetooth() {
        BluetoothManager manager = (BluetoothManager) getSystemService(BLUETOOTH_SERVICE);
        return manager == null ? null : manager.getAdapter();
    }

    private void connectBluetooth() {
        final String token = savedToken();
        if (token == null) {
            showSetup("Connect once on the same Wi-Fi as the PC first. After that, Bluetooth works anywhere.");
            bluetoothButton.setVisibility(View.VISIBLE);
            return;
        }
        if (Build.VERSION.SDK_INT >= 31 && checkSelfPermission(Manifest.permission.BLUETOOTH_CONNECT) != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(new String[] {Manifest.permission.BLUETOOTH_CONNECT}, ASK_BLUETOOTH);
            return;
        }
        BluetoothAdapter adapter = bluetooth();
        if (adapter == null || !adapter.isEnabled()) {
            showSetup("Turn on Bluetooth on this phone, then try again.");
            bluetoothButton.setVisibility(View.VISIBLE);
            return;
        }
        final List<BluetoothDevice> paired = new ArrayList<>(adapter.getBondedDevices());
        if (paired.isEmpty()) {
            showSetup("Pair this phone with your PC first: on the PC, open Settings > Bluetooth & devices > Add device.");
            bluetoothButton.setVisibility(View.VISIBLE);
            return;
        }
        String[] names = new String[paired.size()];
        for (int i = 0; i < names.length; i++) {
            String name = paired.get(i).getName();
            names[i] = name == null ? paired.get(i).getAddress() : name;
        }
        new AlertDialog.Builder(this, android.R.style.Theme_DeviceDefault_Dialog_Alert)
                .setTitle("Which one is your PC?")
                .setItems(names, new DialogInterface.OnClickListener() {
                    @Override
                    public void onClick(DialogInterface dialog, int which) {
                        openBluetooth(paired.get(which), token);
                    }
                })
                .setNegativeButton("Close", null)
                .show();
    }

    private void openBluetooth(final BluetoothDevice pc, final String token) {
        handler.removeCallbacks(searchAgain);
        showSetup("Connecting over Bluetooth...");
        new Thread(new Runnable() {
            @Override
            public void run() {
                BluetoothTunnel opened = null;
                try {
                    opened = BluetoothTunnel.open(pc);
                } catch (IOException | SecurityException error) {
                    opened = null;
                }
                final BluetoothTunnel ready = opened;
                runOnUiThread(new Runnable() {
                    @Override
                    public void run() {
                        if (ready == null) {
                            showSetup("Couldn't connect over Bluetooth. On the PC, turn on Bluetooth in Motorcycle Man "
                                    + "(Ways to connect), and make sure this phone is paired with it.");
                            bluetoothButton.setVisibility(View.VISIBLE);
                            return;
                        }
                        if (tunnel != null) {
                            tunnel.close();
                        }
                        tunnel = ready;
                        bluetoothUrl = "http://127.0.0.1:" + ready.port() + "/?token=" + Uri.encode(token);
                        pageReady = false;
                        setup.setVisibility(View.GONE);
                        web.setVisibility(View.VISIBLE);
                        web.loadUrl(bluetoothUrl);
                    }
                });
            }
        }).start();
    }

    private void open(String url) {
        handler.removeCallbacks(searchAgain);
        pageReady = false;
        rememberPc(url);
        prefs().edit().putString(KEY_URL, url).apply();
        urlInput.setText(url);
        setup.setVisibility(View.GONE);
        web.setVisibility(View.VISIBLE);
        web.loadUrl(url);
    }

    private void showSetup(String message) {
        setupMessage.setText(message);
        codeView.setVisibility(View.GONE);
        typedRow.setVisibility(View.GONE);
        choices.removeAllViews();
        findButton.setVisibility(View.GONE);
        bluetoothButton.setVisibility(bluetooth() != null && !message.startsWith("Connecting") ? View.VISIBLE : View.GONE);
        web.setVisibility(View.GONE);
        setup.setVisibility(View.VISIBLE);
    }

    // Several PCs: each one this phone connects to is remembered by its link's secret (its address may
    // change), with the name it gives, so the page's PCs button can switch between them.
    private JSONArray knownPcs() {
        try {
            return new JSONArray(prefs().getString(KEY_PCS, "[]"));
        } catch (JSONException error) {
            return new JSONArray();
        }
    }

    private static String tokenOf(String url) {
        return url == null ? null : Uri.parse(url).getQueryParameter("token");
    }

    private void rememberPc(final String url) {
        final Uri uri = Uri.parse(url);
        final String token = uri.getQueryParameter("token");
        if (token == null) {
            return;
        }
        new Thread(new Runnable() {
            @Override
            public void run() {
                String base = uri.getScheme() + "://" + uri.getEncodedAuthority();
                String reply = PcFinder.get(base + "/hello", 3000, 5000);
                final String name = reply == null ? null : PcFinder.field(reply, "name");
                // What waking it from sleep takes: told only to a phone on the PC's own Wi-Fi.
                String wake = PcFinder.get(base + "/power?do=wake_info&token=" + PcFinder.encode(token), 3000, 8000);
                final String mac = wake == null ? null : PcFinder.field(wake, "mac");
                final String broadcast = wake == null ? null : PcFinder.field(wake, "broadcast");
                runOnUiThread(new Runnable() {
                    @Override
                    public void run() {
                        savePc(token, url, name, mac, broadcast);
                    }
                });
            }
        }).start();
    }

    private void savePc(String token, String url, String name, String mac, String broadcast) {
        JSONArray old = knownPcs();
        JSONArray now = new JSONArray();
        try {
            String keep = name;
            String keepMac = mac;
            String keepBroadcast = broadcast;
            for (int i = 0; i < old.length(); i++) {
                JSONObject pc = old.optJSONObject(i);
                if (pc == null || !token.equals(tokenOf(pc.optString("url")))) {
                    continue;
                }
                if (keep == null || keep.isEmpty()) {
                    keep = pc.optString("name");  // it didn't say its name this time
                }
                if (keepMac == null || keepMac.isEmpty()) {  // reached through the internet this time
                    keepMac = pc.optString("mac");
                    keepBroadcast = pc.optString("broadcast");
                }
            }
            now.put(new JSONObject().put("name", keep == null || keep.isEmpty() ? Uri.parse(url).getHost() : keep).put("url", url)
                    .put("mac", keepMac == null ? "" : keepMac).put("broadcast", keepBroadcast == null ? "" : keepBroadcast));
            for (int i = 0; i < old.length() && now.length() < MOST_PCS; i++) {
                JSONObject pc = old.optJSONObject(i);
                if (pc != null && !token.equals(tokenOf(pc.optString("url")))) {
                    now.put(pc);
                }
            }
        } catch (JSONException error) {
            return;
        }
        prefs().edit().putString(KEY_PCS, now.toString()).apply();
    }

    private void choosePc() {
        final JSONArray pcs = knownPcs();
        final String current = savedToken();
        final List<String> urls = new ArrayList<>();
        List<String> labels = new ArrayList<>();
        for (int i = 0; i < pcs.length(); i++) {
            JSONObject pc = pcs.optJSONObject(i);
            if (pc == null) {
                continue;
            }
            urls.add(pc.optString("url"));
            boolean here = current != null && current.equals(tokenOf(pc.optString("url")));
            labels.add((here ? "\u2713  " : "      ") + pc.optString("name"));
        }
        labels.add("+  Connect another PC");
        new AlertDialog.Builder(this, android.R.style.Theme_DeviceDefault_Dialog_Alert)
                .setTitle("Your PCs")
                .setItems(labels.toArray(new String[0]), new DialogInterface.OnClickListener() {
                    @Override
                    public void onClick(DialogInterface dialog, int which) {
                        if (which < urls.size()) {
                            openOrFind(urls.get(which));
                        } else {
                            findPc(null);  // finds the PCs on this Wi-Fi, and asks the one picked to let this phone in
                        }
                    }
                })
                .setNeutralButton("Forget this PC", new DialogInterface.OnClickListener() {
                    @Override
                    public void onClick(DialogInterface dialog, int which) {
                        forgetPc(current);
                    }
                })
                .setNegativeButton("Close", null)
                .show();
    }

    private void forgetPc(String token) {
        JSONArray old = knownPcs();
        JSONArray now = new JSONArray();
        for (int i = 0; i < old.length(); i++) {
            JSONObject pc = old.optJSONObject(i);
            if (pc != null && (token == null || !token.equals(tokenOf(pc.optString("url"))))) {
                now.put(pc);
            }
        }
        prefs().edit().putString(KEY_PCS, now.toString()).remove(KEY_URL).apply();
        JSONObject next = now.optJSONObject(0);
        if (next != null) {
            openOrFind(next.optString("url"));
        } else {
            findPc(null);
        }
    }

    private String savedToken() {
        String url = prefs().getString(KEY_URL, null);
        return url == null ? null : Uri.parse(url).getQueryParameter("token");
    }

    // Looks for Motorcycle Man on the Wi-Fi. With a saved secret it first tries it on every Motorcycle Man found, since
    // the PC may only have a new address; otherwise it asks the PC it finds to let this phone in.
    private void findPc(final String token) {
        if (searching) {
            return;
        }
        searching = true;
        handler.removeCallbacks(searchAgain);
        showSetup("Looking for Motorcycle Man on your Wi-Fi…");
        new Thread(new Runnable() {
            @Override
            public void run() {
                final List<PcFinder.Pc> found = PcFinder.find(PcFinder.localNetworks());
                String moved = null;
                for (PcFinder.Pc pc : found) {
                    if (token != null && PcFinder.accepts(pc.address, token)) {
                        moved = PcFinder.link(pc.address, token);
                        break;
                    }
                }
                final String link = moved;
                runOnUiThread(new Runnable() {
                    @Override
                    public void run() {
                        searching = false;
                        if (link != null) {
                            open(link);
                        } else {
                            showFound(found);
                        }
                    }
                });
            }
        }).start();
    }

    private void showFound(List<PcFinder.Pc> found) {
        if (found.isEmpty()) {
            showSetup(NOT_FOUND);
            showFindButton("Search again");
            offerWake();
            handler.postDelayed(searchAgain, 8000);  // keeps looking until Motorcycle Man is opened on the PC
        } else if (found.size() == 1) {
            pairWith(found.get(0));
        } else {
            showSetup("Found Motorcycle Man on more than one PC. Which one is yours?");
            for (final PcFinder.Pc pc : found) {
                Button choice = pillButton(pc.name, false);
                choice.setOnClickListener(new View.OnClickListener() {
                    @Override
                    public void onClick(View v) {
                        pairWith(pc);
                    }
                });
                choices.addView(choice, spaced(LinearLayout.LayoutParams.MATCH_PARENT));
            }
        }
    }

    // The PC this phone last used may only be asleep. With its network card's address (which the PC tells a
    // phone on its own Wi-Fi), a Wake button sends it the Wake-on-LAN packet, then looks for it again.
    private void offerWake() {
        final JSONObject pc = currentPc();
        if (pc == null || pc.optString("mac").isEmpty()) {
            return;
        }
        Button wake = pillButton("Wake " + pc.optString("name"), true);
        wake.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                wakePc(pc);
            }
        });
        choices.addView(wake, spaced(LinearLayout.LayoutParams.MATCH_PARENT));
    }

    private JSONObject currentPc() {
        String token = savedToken();
        JSONArray pcs = knownPcs();
        for (int i = 0; token != null && i < pcs.length(); i++) {
            JSONObject pc = pcs.optJSONObject(i);
            if (pc != null && token.equals(tokenOf(pc.optString("url")))) {
                return pc;
            }
        }
        return null;
    }

    private void wakePc(final JSONObject pc) {
        final String name = pc.optString("name");
        setupMessage.setText("Waking " + name + "…");
        new Thread(new Runnable() {
            @Override
            public void run() {
                final boolean sent = PcFinder.wake(pc.optString("mac"), pc.optString("broadcast"));
                runOnUiThread(new Runnable() {
                    @Override
                    public void run() {
                        setupMessage.setText(sent ? "Sent " + name + " a wake-up. It can take half a minute; looking for it…"
                                : "Couldn't send the wake-up. Is this phone on the PC's Wi-Fi?");
                        handler.removeCallbacks(searchAgain);
                        handler.postDelayed(searchAgain, 6000);
                    }
                });
            }
        }).start();
    }

    // Asks the PC to let this phone in. The PC shows the same four digits; the user taps Allow there,
    // or types here the six digits the PC shows under Allow. An emulator on the PC itself, such as
    // BlueStacks, is let in at once, so the steps only show when the PC doesn't answer right away.
    private void pairWith(final PcFinder.Pc pc) {
        final String code = String.format(java.util.Locale.ROOT, "%04d", new SecureRandom().nextInt(10000));
        final boolean[] answered = {false};
        showSetup("Connecting to " + pc.name + "…");
        handler.postDelayed(new Runnable() {
            @Override
            public void run() {
                if (answered[0]) {
                    return;
                }
                setupMessage.setText("Found " + pc.name + ". On your PC, tap Allow. It shows the same code:");
                codeView.setText(code.replace("", " ").trim());
                codeView.setVisibility(View.VISIBLE);
                if (pc.version >= 2) {
                    typedCode.setText("");
                    typedConnect.setOnClickListener(new View.OnClickListener() {
                        @Override
                        public void onClick(View v) {
                            sendTypedCode(pc, code);
                        }
                    });
                    typedRow.setVisibility(View.VISIBLE);
                }
            }
        }, 1200);
        new Thread(new Runnable() {
            @Override
            public void run() {
                final String link = PcFinder.pair(pc, deviceName(), code);
                runOnUiThread(new Runnable() {
                    @Override
                    public void run() {
                        answered[0] = true;
                        if (link != null) {
                            open(link);
                        } else {
                            showSetup(pc.name + " didn't let this phone in. Tap Try again, then Allow on the PC.");
                            showFindButton("Try again");
                        }
                    }
                });
            }
        }).start();
    }

    // The six digits the PC shows under Allow, typed on the phone: the way in when the PC is out of reach.
    private void sendTypedCode(final PcFinder.Pc pc, final String code) {
        final String digits = typedCode.getText().toString().replaceAll("\\D", "");
        if (digits.length() != 6) {
            setupMessage.setText("Type the 6 digits under Allow on " + pc.name + ".");
            return;
        }
        setupMessage.setText("Checking the code…");
        new Thread(new Runnable() {
            @Override
            public void run() {
                final String result = PcFinder.answer(pc, code, digits);
                runOnUiThread(new Runnable() {
                    @Override
                    public void run() {
                        if (setup.getVisibility() != View.VISIBLE) {
                            return;  // already connected
                        }
                        if ("ok".equals(result)) {
                            setupMessage.setText("Connecting to " + pc.name + "…");
                        } else if ("wrong code".equals(result)) {
                            setupMessage.setText("That code didn't match. Check the 6 digits under Allow on " + pc.name + ".");
                        } else {
                            setupMessage.setText("Couldn't reach " + pc.name + ". Tap Allow there, or try again.");
                        }
                    }
                });
            }
        }).start();
    }

    private String deviceName() {
        String name = Build.VERSION.SDK_INT >= 25 ? Settings.Global.getString(getContentResolver(), "device_name") : null;
        return name == null || name.trim().isEmpty() ? Build.MANUFACTURER + " " + Build.MODEL : name;
    }

    // The page asks for this when it has lost the PC for a while: if Motorcycle Man is now at another address
    // (the PC got a new one on the Wi-Fi), the app moves there with the same secret, without a word.
    private void findMovedPc() {
        final String token = savedToken();
        final String current = Uri.parse(String.valueOf(web.getUrl())).getHost();
        if (searching || token == null || System.currentTimeMillis() - lastQuietSearch < 30000) {
            return;
        }
        lastQuietSearch = System.currentTimeMillis();
        new Thread(new Runnable() {
            @Override
            public void run() {
                for (PcFinder.Pc pc : PcFinder.find(PcFinder.localNetworks())) {
                    if (!pc.address.equals(current) && PcFinder.accepts(pc.address, token)) {
                        final String link = PcFinder.link(pc.address, token);
                        runOnUiThread(new Runnable() {
                            @Override
                            public void run() {
                                open(link);
                            }
                        });
                        return;
                    }
                }
            }
        }).start();
    }

    // This app's version, and whether it is newer than when the app last started: then it was just
    // updated, and once the page is open Motorcycle Man says so. (versionCode is the way Android 7 and 8 have.)
    @SuppressWarnings("deprecation")
    private void noteVersion() {
        try {
            PackageInfo info = getPackageManager().getPackageInfo(getPackageName(), 0);
            appVersion = info.versionCode;
            appVersionName = info.versionName == null ? "" : info.versionName;
        } catch (PackageManager.NameNotFoundException e) {
            return;  // can't happen: this is the app itself
        }
        int last = prefs().getInt(KEY_APP_VERSION, 0);
        if (last != appVersion) {
            prefs().edit().putInt(KEY_APP_VERSION, appVersion).apply();
        }
        // Version 1.3 and older didn't note theirs; a saved link shows that one of them ran here.
        updated = appVersion > last && (last > 0 || prefs().contains(KEY_URL));
    }

    private void sayUpdated() {
        String words = JSONObject.quote("Ta-da! I'm the new Motorcycle Man app, version " + appVersionName + ".");
        web.evaluateJavascript("window.onAndroidNotice ? (window.onAndroidNotice(" + words + "), true) : false",
                new ValueCallback<String>() {
                    @Override
                    public void onReceiveValue(String value) {
                        if ("true".equals(value)) {
                            updated = false;  // said once, on Motorcycle Man's page (not the page for connecting)
                        }
                    }
                });
    }

    // The phone page says which version of the app Motorcycle Man on the PC has. When that is newer than this
    // app, the app asks the PC about it and offers it, unless the user said Not now a short while ago.
    private void pcHasApp(int version) {
        long later = prefs().getLong(KEY_UPDATE_LATER, 0), now = System.currentTimeMillis();
        String url = prefs().getString(KEY_URL, null);
        if (version <= appVersion || url == null || checkingUpdate || updateDialog != null
                || (now >= later && now - later < UPDATE_REST_MS)
                || !isPcAddress(Uri.parse(String.valueOf(web.getUrl())))) {
            return;
        }
        final Uri uri = Uri.parse(url);
        final String base = uri.getScheme() + "://" + uri.getEncodedAuthority();
        final String token = uri.getQueryParameter("token");
        if (token == null) {
            return;
        }
        checkingUpdate = true;
        new Thread(new Runnable() {
            @Override
            public void run() {
                final AppUpdate.Offer offer = AppUpdate.check(base, token);
                runOnUiThread(new Runnable() {
                    @Override
                    public void run() {
                        checkingUpdate = false;
                        if (offer != null && offer.code > appVersion && updateDialog == null && !isFinishing()
                                && !isDestroyed()) {
                            offerUpdate(offer);
                        }
                    }
                });
            }
        }).start();
    }

    // GitHub's newest release, looked at when the app comes to the front: at most every six hours, or ten minutes
    // after GitHub couldn't be reached. When its app is newer than this one, the app offers it, unless the user
    // said Not now a short while ago.
    private void checkGitHub() {
        long now = System.currentTimeMillis(), later = prefs().getLong(KEY_UPDATE_LATER, 0);
        long checked = prefs().getLong(KEY_GITHUB_CHECKED, 0);
        if (checkingUpdate || updateDialog != null || (now >= later && now - later < UPDATE_REST_MS)
                || (now >= checked && now - checked < GITHUB_CHECK_EVERY_MS) || now - gitHubTried < GITHUB_RETRY_MS) {
            return;
        }
        gitHubTried = now;
        checkingUpdate = true;
        new Thread(new Runnable() {
            @Override
            public void run() {
                final AppUpdate.Offer offer = AppUpdate.checkGitHub();
                runOnUiThread(new Runnable() {
                    @Override
                    public void run() {
                        checkingUpdate = false;
                        if (offer != null) {
                            prefs().edit().putLong(KEY_GITHUB_CHECKED, System.currentTimeMillis()).apply();
                        }
                        if (offer != null && offer.code > appVersion && updateDialog == null && !isFinishing()
                                && !isDestroyed()) {
                            offerUpdate(offer);
                        }
                    }
                });
            }
        }).start();
    }

    // "Update the Motorcycle Man app": a small window in Motorcycle Man's colours, like the rest of the app.
    private void offerUpdate(AppUpdate.Offer offer) {
        updateOffer = offer;
        LinearLayout box = new LinearLayout(this);
        box.setOrientation(LinearLayout.VERTICAL);
        box.setGravity(Gravity.CENTER_HORIZONTAL);
        box.setPadding(dp(26), dp(22), dp(26), dp(22));
        GradientDrawable shape = new GradientDrawable();
        shape.setCornerRadius(dp(22));
        shape.setColor(Color.parseColor("#121A3A"));
        shape.setStroke(dp(1), Color.parseColor("#232D5C"));
        box.setBackground(shape);

        ImageView logo = new ImageView(this);
        logo.setImageDrawable(getApplicationInfo().loadIcon(getPackageManager()));
        box.addView(logo, new LinearLayout.LayoutParams(dp(64), dp(64)));

        TextView title = new TextView(this);
        title.setText("Update the Motorcycle Man app");
        title.setTextColor(Color.WHITE);
        title.setTextSize(20);
        title.setTypeface(Typeface.DEFAULT_BOLD);
        title.setGravity(Gravity.CENTER);
        title.setPadding(0, dp(8), 0, 0);
        box.addView(title);

        updateMessage = new TextView(this);
        updateMessage.setTextColor(Color.parseColor("#CBD5E1"));
        updateMessage.setTextSize(15);
        updateMessage.setGravity(Gravity.CENTER);
        updateMessage.setPadding(0, dp(8), 0, dp(4));
        box.addView(updateMessage);

        updateButtons = new LinearLayout(this);
        updateButtons.setOrientation(LinearLayout.HORIZONTAL);
        updateButtons.setGravity(Gravity.CENTER);
        box.addView(updateButtons, spaced(ViewGroup.LayoutParams.WRAP_CONTENT));

        updateDialog = new Dialog(this);
        updateDialog.requestWindowFeature(Window.FEATURE_NO_TITLE);
        updateDialog.setContentView(box);
        updateDialog.getWindow().setBackgroundDrawable(new ColorDrawable(Color.TRANSPARENT));
        updateDialog.getWindow().setLayout(Math.min(dp(440), getResources().getDisplayMetrics().widthPixels - dp(32)),
                ViewGroup.LayoutParams.WRAP_CONTENT);
        updateDialog.setCanceledOnTouchOutside(false);
        updateDialog.setOnCancelListener(new DialogInterface.OnCancelListener() {
            @Override
            public void onCancel(DialogInterface dialog) {
                updateLater();  // the back button
            }
        });
        String words = (offer.name.isEmpty() ? "A new Motorcycle Man app" : "Motorcycle Man " + offer.name)
                + (offer.fromPc ? " is ready on your PC. " : " is ready. ")
                + "Android asks you to OK the update, and the app closes while it updates; then just open Motorcycle Man "
                + "again. Your link and settings stay.";
        if (Build.VERSION.SDK_INT >= 26 && !getPackageManager().canRequestPackageInstalls()) {
            words += "\n\nThe first time, Android asks to allow updates from Motorcycle Man: tap Settings, turn on "
                    + "“Allow from this source”, then go back.";
        }
        updateChoice(words, "Update");
        updateDialog.show();
    }

    // What the update window says, with the button that goes on (Update, Try again) and Not now.
    private void updateChoice(String words, String go) {
        if (updateDialog == null) {
            return;
        }
        updateMessage.setText(words);
        updateButtons.removeAllViews();
        Button goButton = pillButton(go, true);
        goButton.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                downloadUpdate();
            }
        });
        updateButtons.addView(goButton);
        Button later = pillButton("Not now", false);
        later.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                updateLater();
            }
        });
        LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT,
                ViewGroup.LayoutParams.WRAP_CONTENT);
        params.leftMargin = dp(10);
        updateButtons.addView(later, params);
        updateButtons.setVisibility(View.VISIBLE);
        updateDialog.setCancelable(true);
    }

    // What the update window says while the update is on its way: no buttons, and it stays up.
    private void updateWorking(String words) {
        if (updateDialog != null) {
            updateMessage.setText(words);
            updateButtons.setVisibility(View.GONE);
            updateDialog.setCancelable(false);
        }
    }

    // Downloads the new app from the PC or GitHub into the app's cache, showing how far it got.
    private void downloadUpdate() {
        final AppUpdate.Offer offer = updateOffer;
        final String getting = offer.fromPc ? "Getting the new app from your PC…" : "Getting the new app…";
        updateWorking(getting);
        final File file = new File(getCacheDir(), UPDATE_FILE);
        new Thread(new Runnable() {
            @Override
            public void run() {
                final String problem = AppUpdate.download(offer, file, new AppUpdate.Progress() {
                    private long shown = -1;

                    @Override
                    public void downloaded(long done, long total) {
                        final long percent = total > 0 ? Math.min(100, done * 100 / total) : -1;
                        if (percent == shown) {
                            return;
                        }
                        shown = percent;
                        runOnUiThread(new Runnable() {
                            @Override
                            public void run() {
                                if (percent >= 0) {
                                    updateWorking(getting + " " + percent + "%");
                                }
                            }
                        });
                    }
                });
                runOnUiThread(new Runnable() {
                    @Override
                    public void run() {
                        if (updateDialog == null) {
                            file.delete();  // the app closed meanwhile
                        } else if (problem != null) {
                            file.delete();
                            updateChoice(problem, "Try again");
                        } else {
                            downloadedUpdate = file;
                            if (resumed) {
                                installUpdate();
                            }
                        }
                    }
                });
            }
        }).start();
    }

    // Hands the downloaded app to Android's installer. Android makes sure it is signed with the same key
    // as this app, asks the user, and updates the app; its saved link and settings stay.
    @SuppressWarnings("deprecation")
    private void installUpdate() {
        File file = downloadedUpdate;
        downloadedUpdate = null;
        PackageInfo info = getPackageManager().getPackageArchiveInfo(file.getPath(), 0);
        if (info == null || !getPackageName().equals(info.packageName) || info.versionCode <= appVersion) {
            file.delete();
            updateChoice(info == null ? "The new app arrived damaged. Try again."
                    : updateOffer.fromPc ? "The app on your PC isn't newer than this one after all."
                    : "That app isn't newer than this one after all.", "Try again");
            return;
        }
        updateWorking("Handing it to Android…");
        PackageInstaller installer = getPackageManager().getPackageInstaller();
        int session = -1;
        try {
            PackageInstaller.SessionParams params = new PackageInstaller.SessionParams(
                    PackageInstaller.SessionParams.MODE_FULL_INSTALL);
            params.setAppPackageName(getPackageName());
            params.setSize(file.length());
            session = installer.createSession(params);
            try (PackageInstaller.Session writing = installer.openSession(session)) {
                try (InputStream in = new FileInputStream(file);
                     OutputStream out = writing.openWrite(UPDATE_FILE, 0, file.length())) {
                    byte[] buffer = new byte[64 * 1024];
                    for (int n = in.read(buffer); n != -1; n = in.read(buffer)) {
                        out.write(buffer, 0, n);
                    }
                    writing.fsync(out);
                }
                // Android answers through this, filling in its answer: so on Android 12 and newer it
                // has to be mutable.
                Intent answer = new Intent(this, MainActivity.class).setAction(INSTALL_STATUS);
                PendingIntent status = PendingIntent.getActivity(this, session, answer, PendingIntent.FLAG_UPDATE_CURRENT
                        | (Build.VERSION.SDK_INT >= 31 ? PendingIntent.FLAG_MUTABLE : 0));
                writing.commit(status.getIntentSender());
                updateSession = session;
                if (updateDialog != null) {
                    updateDialog.setCancelable(true);  // Android has it now; Back closes this window
                }
            }
        } catch (IOException | RuntimeException e) {
            if (session != -1 && updateSession != session) {
                try {
                    installer.abandonSession(session);
                } catch (RuntimeException ignored) {
                    // Already gone.
                }
            }
            updateChoice("Android couldn't take the new app. Try again.", "Try again");
        } finally {
            file.delete();  // Android has its own copy now
        }
    }

    // Android's answers about the update: it needs the user's OK (Android shows its own question, and
    // the first time asks to allow updates from Motorcycle Man), it's done, or it didn't happen.
    @SuppressWarnings("deprecation")
    private void installerAnswered(Intent intent) {
        if (updateSession == -1 || intent.getIntExtra(PackageInstaller.EXTRA_SESSION_ID, -1) != updateSession) {
            return;  // not about the update this app asked for
        }
        int status = intent.getIntExtra(PackageInstaller.EXTRA_STATUS, PackageInstaller.STATUS_FAILURE);
        if (status == PackageInstaller.STATUS_PENDING_USER_ACTION) {
            Intent question = intent.getParcelableExtra(Intent.EXTRA_INTENT);
            // Other apps can open this app too, so only Android's question about an install is opened
            // (android.content.pm.action.CONFIRM_INSTALL, or CONFIRM_PERMISSIONS on Android 7 and 8).
            if (question != null && String.valueOf(question.getAction()).startsWith("android.content.pm.action.CONFIRM_")) {
                // and never with a right to read or write this app's files passed along with it (setFlags:
                // removeFlags is Android 8's, and this app runs on 7)
                question.setFlags(question.getFlags() & ~(Intent.FLAG_GRANT_READ_URI_PERMISSION
                        | Intent.FLAG_GRANT_WRITE_URI_PERMISSION | Intent.FLAG_GRANT_PERSISTABLE_URI_PERMISSION
                        | Intent.FLAG_GRANT_PREFIX_URI_PERMISSION));
                try {
                    startActivity(question);  // Android's own question, over this app
                    return;
                } catch (RuntimeException e) {
                    // Android's question didn't open; said below.
                }
            }
            try {
                getPackageManager().getPackageInstaller().abandonSession(updateSession);
            } catch (RuntimeException ignored) {
                // Already gone.
            }
        }
        updateSession = -1;
        if (status == PackageInstaller.STATUS_SUCCESS) {
            closeUpdate();  // Android usually closes the app to update it, so this is rarely seen
        } else if (status == PackageInstaller.STATUS_FAILURE_ABORTED) {
            updateChoice("The app wasn't updated. Tap Update when you're ready.", "Update");
        } else if (status == PackageInstaller.STATUS_FAILURE_STORAGE) {
            updateChoice("There isn't enough space on the phone for the update.", "Try again");
        } else {
            String detail = intent.getStringExtra(PackageInstaller.EXTRA_STATUS_MESSAGE);
            updateChoice("Android didn't install the update" + (detail == null ? "." : ": " + detail), "Try again");
        }
    }

    private void updateLater() {
        prefs().edit().putLong(KEY_UPDATE_LATER, System.currentTimeMillis()).apply();
        closeUpdate();
    }

    private void closeUpdate() {
        if (updateDialog != null) {
            updateDialog.dismiss();
            updateDialog = null;
        }
        if (downloadedUpdate != null) {
            downloadedUpdate.delete();
            downloadedUpdate = null;
        }
    }

    private void showFindButton(String text) {
        findButton.setText(text);
        findButton.setVisibility(View.VISIBLE);
    }

    private Button pillButton(String text, boolean main) {
        Button button = new Button(this);
        button.setText(text);
        button.setAllCaps(false);
        button.setTextSize(16);
        button.setTypeface(Typeface.DEFAULT_BOLD);
        button.setTextColor(Color.parseColor(main ? "#1A1305" : "#E2E8F0"));
        GradientDrawable shape = new GradientDrawable();
        shape.setCornerRadius(dp(14));
        shape.setColor(Color.parseColor(main ? "#FFC83D" : "#1C2550"));
        if (!main) {
            shape.setStroke(dp(1), Color.parseColor("#2E3A72"));
        }
        button.setBackground(shape);
        button.setPadding(dp(22), dp(12), dp(22), dp(12));
        return button;
    }

    private LinearLayout.LayoutParams spaced(int width) {
        LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(width, ViewGroup.LayoutParams.WRAP_CONTENT);
        params.topMargin = dp(10);
        return params;
    }

    private SharedPreferences prefs() {
        return getSharedPreferences("motorcycleman", MODE_PRIVATE);
    }

    private int dp(int value) {
        return (int) (value * getResources().getDisplayMetrics().density);
    }

    // Calls window.<function>(<arguments>) on the phone page, if the page has that function.
    private void callPage(String function, String arguments) {
        web.evaluateJavascript("window." + function + " && window." + function + "(" + arguments + ")", null);
    }

    private void startSpeech(String language) {
        // Only Motorcycle Man's own page may use the microphone.
        if (!isPcAddress(Uri.parse(String.valueOf(web.getUrl())))) {
            return;
        }
        if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(new String[] {Manifest.permission.RECORD_AUDIO}, 1);
            callPage("onAndroidSpeechError", JSONObject.quote(ALLOW_MICROPHONE));
            return;
        }
        if (recognizer == null) {
            if (SpeechRecognizer.isRecognitionAvailable(this)) {
                recognizer = SpeechRecognizer.createSpeechRecognizer(this);
            } else if (Build.VERSION.SDK_INT >= 33 && SpeechRecognizer.isOnDeviceRecognitionAvailable(this)) {
                recognizer = SpeechRecognizer.createOnDeviceSpeechRecognizer(this);
            } else {
                callPage("onAndroidSpeechError", JSONObject.quote("This phone has no speech recognition service."));
                return;
            }
            recognizer.setRecognitionListener(new SpeechListener());
        }
        Intent intent = new Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH);
        intent.putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM);
        intent.putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, true);
        intent.putExtra(RecognizerIntent.EXTRA_CALLING_PACKAGE, getPackageName());
        if (language != null && !language.isEmpty()) {
            intent.putExtra(RecognizerIntent.EXTRA_LANGUAGE, language);
        }
        recognizer.cancel();
        recognizer.startListening(intent);
    }

    // The phone page reaches this as window.MotorcycleManAndroid. JavaScript calls arrive on a background
    // thread, and SpeechRecognizer only works on the main thread.
    private class SpeechBridge {
        @JavascriptInterface
        public boolean isAvailable() {
            return SpeechRecognizer.isRecognitionAvailable(MainActivity.this)
                    || (Build.VERSION.SDK_INT >= 33 && SpeechRecognizer.isOnDeviceRecognitionAvailable(MainActivity.this));
        }

        @JavascriptInterface
        public void startListening(final String language) {
            runOnUiThread(new Runnable() {
                @Override
                public void run() {
                    startSpeech(language);
                }
            });
        }

        @JavascriptInterface
        public void stopListening() {
            runOnUiThread(new Runnable() {
                @Override
                public void run() {
                    if (recognizer != null) {
                        recognizer.stopListening();
                    }
                }
            });
        }

        @JavascriptInterface
        public void findMovedPc() {
            runOnUiThread(new Runnable() {
                @Override
                public void run() {
                    MainActivity.this.findMovedPc();
                }
            });
        }

        // The page's Copy: puts text from the PC's clipboard on the phone's.
        @JavascriptInterface
        public void copyText(final String text) {
            runOnUiThread(new Runnable() {
                @Override
                public void run() {
                    ClipboardManager clipboard = (ClipboardManager) getSystemService(CLIPBOARD_SERVICE);
                    if (clipboard != null) {
                        clipboard.setPrimaryClip(ClipData.newPlainText("From your PC", text));
                    }
                }
            });
        }

        // A reply read aloud (the app's web view has no speech of its own), and quiet again.
        @JavascriptInterface
        public void speak(final String text) {
            runOnUiThread(new Runnable() {
                @Override
                public void run() {
                    speakNow(text);
                }
            });
        }

        @JavascriptInterface
        public void stopSpeaking() {
            runOnUiThread(new Runnable() {
                @Override
                public void run() {
                    voiceWaiting = null;
                    if (voice != null) {
                        voice.stop();
                    }
                }
            });
        }

        // The page's Watch button: Motorcycle Man's notification, which a watch shows with its buttons.
        @JavascriptInterface
        public boolean watchOn() {
            return WatchNotice.isOn(MainActivity.this);
        }

        @JavascriptInterface
        public void watch(final boolean on) {
            runOnUiThread(new Runnable() {
                @Override
                public void run() {
                    setWatch(on);
                }
            });
        }

        // The page's Phone alerts button: this phone's notifications shown on the PC.
        @JavascriptInterface
        public boolean noticesOn() {
            return NoticeListener.isOn(MainActivity.this);
        }

        @JavascriptInterface
        public void notices(final boolean on) {
            runOnUiThread(new Runnable() {
                @Override
                public void run() {
                    setNotices(on);
                }
            });
        }

        // The page's Texts button: commands by text message to this phone.
        @JavascriptInterface
        public void smsSettings() {
            runOnUiThread(new Runnable() {
                @Override
                public void run() {
                    showTexts();
                }
            });
        }

        // The page's PCs button: the PCs this phone knows, to switch to, or another to connect.
        @JavascriptInterface
        public void choosePc() {
            runOnUiThread(new Runnable() {
                @Override
                public void run() {
                    MainActivity.this.choosePc();
                }
            });
        }

        // Which version of the app Motorcycle Man on the PC has; the app updates itself when it is newer.
        @JavascriptInterface
        public void pcHasApp(final int version) {
            runOnUiThread(new Runnable() {
                @Override
                public void run() {
                    MainActivity.this.pcHasApp(version);
                }
            });
        }
    }

    private void speakNow(String text) {
        if (text == null || text.trim().isEmpty()) {
            return;
        }
        if (voice == null) {
            voiceWaiting = text;
            voice = new TextToSpeech(this, new TextToSpeech.OnInitListener() {
                @Override
                public void onInit(int status) {
                    voiceReady = status == TextToSpeech.SUCCESS;
                    if (voiceReady && voiceWaiting != null) {
                        voice.speak(voiceWaiting, TextToSpeech.QUEUE_FLUSH, null, "reply");
                    }
                    voiceWaiting = null;
                }
            });
            return;
        }
        if (voiceReady) {
            voice.speak(text, TextToSpeech.QUEUE_FLUSH, null, "reply");
        } else {
            voiceWaiting = text;  // still starting up
        }
    }

    @Override
    protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        super.onActivityResult(requestCode, resultCode, data);
        if (requestCode != PICK_FILES || pickedFiles == null) {
            return;
        }
        Uri[] picked = null;  // nothing, when the picker was closed
        if (resultCode == RESULT_OK && data != null) {
            if (data.getClipData() != null) {
                picked = new Uri[data.getClipData().getItemCount()];
                for (int i = 0; i < picked.length; i++) {
                    picked[i] = data.getClipData().getItemAt(i).getUri();
                }
            } else if (data.getData() != null) {
                picked = new Uri[] {data.getData()};
            }
        }
        pickedFiles.onReceiveValue(picked);
        pickedFiles = null;
    }

    private class SpeechListener implements RecognitionListener {
        @Override
        public void onPartialResults(Bundle partialResults) {
            sendHeard(partialResults, false);
        }

        @Override
        public void onResults(Bundle results) {
            sendHeard(results, true);
        }

        @Override
        public void onError(int error) {
            // No match, silence and the client error some phones report after stopping all mean
            // "nothing (more) was heard"; the page keeps whatever it already has.
            String message = "";
            if (error == SpeechRecognizer.ERROR_INSUFFICIENT_PERMISSIONS) {
                message = ALLOW_MICROPHONE;
            } else if (error != SpeechRecognizer.ERROR_NO_MATCH && error != SpeechRecognizer.ERROR_SPEECH_TIMEOUT
                    && error != SpeechRecognizer.ERROR_CLIENT) {
                message = "The phone's speech recognition failed (error " + error + "). Try again.";
            }
            callPage("onAndroidSpeechError", JSONObject.quote(message));
        }

        @Override
        public void onReadyForSpeech(Bundle params) {
        }

        @Override
        public void onBeginningOfSpeech() {
        }

        @Override
        public void onRmsChanged(float rmsdB) {
        }

        @Override
        public void onBufferReceived(byte[] buffer) {
        }

        @Override
        public void onEndOfSpeech() {
        }

        @Override
        public void onEvent(int eventType, Bundle params) {
        }

        private void sendHeard(Bundle bundle, boolean done) {
            ArrayList<String> texts = bundle.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION);
            String text = texts == null || texts.isEmpty() ? "" : texts.get(0);
            callPage("onAndroidSpeech", JSONObject.quote(text) + ", " + done);
        }
    }

    private void buildViews() {
        web = new WebView(this);
        WebSettings settings = web.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        // Lets the page hide its "App" button when it is already inside this app.
        settings.setUserAgentString(settings.getUserAgentString() + " MotorcycleManApp/1.0");
        web.addJavascriptInterface(new SpeechBridge(), "MotorcycleManAndroid");
        web.setWebChromeClient(new WebChromeClient() {
            @Override
            public boolean onShowFileChooser(WebView view, ValueCallback<Uri[]> callback, FileChooserParams params) {
                // The page's "Choose photos or files": Android's own picker, several at once.
                if (pickedFiles != null) {
                    pickedFiles.onReceiveValue(null);
                }
                pickedFiles = callback;
                Intent pick = new Intent(Intent.ACTION_GET_CONTENT);
                pick.addCategory(Intent.CATEGORY_OPENABLE);
                pick.setType("*/*");
                pick.putExtra(Intent.EXTRA_ALLOW_MULTIPLE, true);
                try {
                    startActivityForResult(Intent.createChooser(pick, "Send to your PC"), PICK_FILES);
                } catch (RuntimeException e) {
                    pickedFiles = null;
                    return false;
                }
                return true;
            }
        });
        web.setDownloadListener(new DownloadListener() {
            @Override
            public void onDownloadStart(String url, String userAgent, String disposition, String mimeType, long length) {
                // A file from the PC: Android's download manager saves it in Downloads and says so in a
                // notification. (Before Android 10 only into the app's own Downloads, which needs no permission.)
                String name = URLUtil.guessFileName(url, disposition, mimeType);
                try {
                    DownloadManager.Request request = new DownloadManager.Request(Uri.parse(url));
                    request.setMimeType(mimeType);
                    request.setTitle(name);
                    request.setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED);
                    if (Build.VERSION.SDK_INT >= 29) {
                        request.setDestinationInExternalPublicDir(Environment.DIRECTORY_DOWNLOADS, name);
                    } else {
                        request.setDestinationInExternalFilesDir(MainActivity.this, Environment.DIRECTORY_DOWNLOADS, name);
                    }
                    ((DownloadManager) getSystemService(DOWNLOAD_SERVICE)).enqueue(request);
                    callPage("onAndroidNotice", JSONObject.quote("Saving " + name + " in this phone's Downloads."));
                } catch (RuntimeException e) {
                    callPage("onAndroidNotice", JSONObject.quote("This phone couldn't save " + name + "."));
                }
            }
        });
        web.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                // Only Motorcycle Man's own page runs inside the app (it can use the microphone);
                // any other link opens in the browser.
                if (isPcAddress(request.getUrl())) {
                    return false;
                }
                try {
                    startActivity(new Intent(Intent.ACTION_VIEW, request.getUrl()));
                } catch (RuntimeException ignored) {
                    // No app can open that link.
                }
                return true;
            }

            @Override
            public void onReceivedHttpError(WebView view, WebResourceRequest request, WebResourceResponse response) {
                if (request.isForMainFrame() && response.getStatusCode() == 403) {
                    // Motorcycle Man doesn't know this phone's secret any more: ask the PC to let it in again.
                    urlInput.setText("");
                    findPc(null);
                }
            }

            @Override
            public void onReceivedError(WebView view, WebResourceRequest request, WebResourceError error) {
                if (request.isForMainFrame()) {
                    findPc(savedToken());  // Motorcycle Man is closed, or the PC has a new address on the Wi-Fi
                }
            }

            @Override
            public void onPageFinished(WebView view, String url) {
                if (updated && isPcAddress(Uri.parse(String.valueOf(url)))) {
                    sayUpdated();
                }
                if (isPcAddress(Uri.parse(String.valueOf(url)))) {
                    pageReady = true;
                    handler.postDelayed(doPending, 700);  // once the page has set itself up
                }
            }
        });

        LinearLayout content = new LinearLayout(this);
        content.setOrientation(LinearLayout.VERTICAL);
        content.setGravity(Gravity.CENTER_HORIZONTAL);
        content.setPadding(dp(28), dp(24), dp(28), dp(24));

        ImageView logo = new ImageView(this);
        logo.setImageDrawable(getApplicationInfo().loadIcon(getPackageManager()));
        content.addView(logo, new LinearLayout.LayoutParams(dp(84), dp(84)));

        TextView title = new TextView(this);
        title.setText("Connect to Motorcycle Man");
        title.setTextColor(Color.WHITE);
        title.setTextSize(24);
        title.setTypeface(Typeface.DEFAULT_BOLD);
        title.setGravity(Gravity.CENTER);
        title.setPadding(0, dp(10), 0, 0);
        content.addView(title);

        setupMessage = new TextView(this);
        setupMessage.setTextColor(Color.parseColor("#CBD5E1"));
        setupMessage.setTextSize(16);
        setupMessage.setGravity(Gravity.CENTER);
        setupMessage.setPadding(0, dp(8), 0, dp(6));
        content.addView(setupMessage, new LinearLayout.LayoutParams(dp(460), ViewGroup.LayoutParams.WRAP_CONTENT));

        codeView = new TextView(this);
        codeView.setTextColor(Color.parseColor("#FFC83D"));
        codeView.setTextSize(42);
        codeView.setTypeface(Typeface.DEFAULT_BOLD);
        codeView.setGravity(Gravity.CENTER);
        content.addView(codeView);

        // Not at the PC? The six digits it shows under Allow, typed here, let the phone in instead.
        typedRow = new LinearLayout(this);
        typedRow.setOrientation(LinearLayout.VERTICAL);
        typedRow.setGravity(Gravity.CENTER_HORIZONTAL);
        TextView away = new TextView(this);
        away.setText("Not at your PC? Type the 6 digits under Allow there:");
        away.setTextColor(Color.parseColor("#94A3B8"));
        away.setTextSize(14);
        away.setGravity(Gravity.CENTER);
        away.setPadding(0, dp(14), 0, dp(4));
        typedRow.addView(away);
        LinearLayout typedLine = new LinearLayout(this);
        typedLine.setOrientation(LinearLayout.HORIZONTAL);
        typedLine.setGravity(Gravity.CENTER_VERTICAL);
        typedCode = new EditText(this);
        typedCode.setHint("000 000");
        typedCode.setSingleLine(true);
        typedCode.setInputType(InputType.TYPE_CLASS_NUMBER);
        typedCode.setImeOptions(EditorInfo.IME_ACTION_DONE | EditorInfo.IME_FLAG_NO_EXTRACT_UI);
        typedCode.setFilters(new InputFilter[] {new InputFilter.LengthFilter(6)});
        typedCode.setTextColor(Color.WHITE);
        typedCode.setHintTextColor(Color.parseColor("#64748B"));
        typedCode.setTextSize(22);
        typedCode.setGravity(Gravity.CENTER);
        typedCode.setOnEditorActionListener(new TextView.OnEditorActionListener() {
            @Override
            public boolean onEditorAction(TextView v, int actionId, KeyEvent event) {
                typedConnect.performClick();
                return true;
            }
        });
        typedLine.addView(typedCode, new LinearLayout.LayoutParams(dp(170), ViewGroup.LayoutParams.WRAP_CONTENT));
        typedConnect = pillButton("Connect", true);
        typedLine.addView(typedConnect);
        typedRow.addView(typedLine);
        typedRow.setVisibility(View.GONE);
        content.addView(typedRow);

        choices = new LinearLayout(this);
        choices.setOrientation(LinearLayout.VERTICAL);
        content.addView(choices, new LinearLayout.LayoutParams(dp(320), ViewGroup.LayoutParams.WRAP_CONTENT));

        findButton = pillButton("Search again", true);
        findButton.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                findPc(savedToken());
            }
        });
        content.addView(findButton, spaced(ViewGroup.LayoutParams.WRAP_CONTENT));

        bluetoothButton = pillButton("Connect by Bluetooth", false);  // no Wi-Fi in common with the PC
        bluetoothButton.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                connectBluetooth();
            }
        });
        bluetoothButton.setVisibility(View.GONE);
        content.addView(bluetoothButton, spaced(ViewGroup.LayoutParams.WRAP_CONTENT));

        TextView manual = new TextView(this);
        manual.setText("Or paste the link from the Motorcycle Man window on your PC:");
        manual.setTextColor(Color.parseColor("#94A3B8"));
        manual.setTextSize(13);
        manual.setPadding(0, dp(22), 0, dp(4));
        content.addView(manual);

        LinearLayout row = new LinearLayout(this);
        row.setOrientation(LinearLayout.HORIZONTAL);
        row.setGravity(Gravity.CENTER_VERTICAL);
        urlInput = new EditText(this);
        urlInput.setHint("http://192.168.x.x:8080/?token=...");
        urlInput.setSingleLine(true);
        urlInput.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_URI);
        urlInput.setTextColor(Color.WHITE);
        urlInput.setHintTextColor(Color.parseColor("#64748B"));
        urlInput.setTextSize(14);
        row.addView(urlInput, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1));
        Button connect = pillButton("Connect", false);
        connect.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                String url = validUrl(urlInput.getText().toString());
                if (url == null) {
                    setupMessage.setText("That isn't Motorcycle Man's link. It starts with http:// and ends with ?token=...");
                } else {
                    open(url);
                }
            }
        });
        row.addView(connect);
        content.addView(row, new LinearLayout.LayoutParams(dp(460), ViewGroup.LayoutParams.WRAP_CONTENT));

        setup = new ScrollView(this);
        setup.setFillViewport(true);
        setup.setBackgroundColor(Color.parseColor("#0B1026"));
        FrameLayout centre = new FrameLayout(this);
        centre.addView(content, new FrameLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT,
                ViewGroup.LayoutParams.WRAP_CONTENT, Gravity.CENTER));
        setup.addView(centre, new ScrollView.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));

        FrameLayout root = new FrameLayout(this);
        root.addView(web, new FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
        root.addView(setup, new FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
        setContentView(root);
    }
}
