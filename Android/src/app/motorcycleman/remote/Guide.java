package app.motorcycleman.remote;

import android.app.Activity;
import android.app.Dialog;
import android.content.SharedPreferences;
import android.graphics.Color;
import android.graphics.drawable.ColorDrawable;
import android.view.ViewGroup;
import android.webkit.JavascriptInterface;
import android.webkit.WebResourceRequest;
import android.webkit.WebView;
import android.webkit.WebViewClient;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;

// The welcome tour and the feature guide, in a window over the app. The page is guide.html and its words are
// guide.json (the app's assets, copied from shared/ by build_apk.ps1); the PC shows the same page to phone
// browsers. The tour opens by itself the first time the app starts and never again: "tour_seen" stays in the
// app's saved settings, which updates keep.
final class Guide {
    static final String KEY_TOUR_SEEN = "tour_seen";

    private Guide() {
    }

    static boolean tourDue(SharedPreferences prefs) {
        return !prefs.getBoolean(KEY_TOUR_SEEN, false);
    }

    // mode "tour" (the five slides) or "guide" (every feature and how it works).
    static void show(final Activity activity, String mode) {
        String page = asset(activity, "guide.html");
        String words = asset(activity, "guide.json");
        if (page == null || words == null || activity.isFinishing()) {
            return;
        }
        page = page.replace("/*GUIDE_JSON*/{}", words.replace("</", "<\\/"))
                .replace("/*MODE*/\"guide\"", "tour".equals(mode) ? "\"tour\"" : "\"guide\"");
        final Dialog dialog = new Dialog(activity, android.R.style.Theme_Black_NoTitleBar);
        WebView web = new WebView(activity);
        web.setBackgroundColor(Color.parseColor("#0B1026"));
        web.getSettings().setJavaScriptEnabled(true);
        web.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                return true;  // the page has no links: it stays put
            }

            @Override
            @SuppressWarnings("deprecation")
            public boolean shouldOverrideUrlLoading(WebView view, String url) {
                return true;  // the same, on Android 7
            }
        });
        // The page's Close and "Let's go!" buttons.
        web.addJavascriptInterface(new Object() {
            @JavascriptInterface
            public void close() {
                activity.runOnUiThread(new Runnable() {
                    @Override
                    public void run() {
                        if (dialog.isShowing()) {
                            dialog.dismiss();
                        }
                    }
                });
            }
        }, "MMGuide");
        web.loadDataWithBaseURL("https://guide.motorcycleman.invalid/", page, "text/html", "utf-8", null);
        dialog.setContentView(web, new ViewGroup.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.MATCH_PARENT));
        dialog.getWindow().setBackgroundDrawable(new ColorDrawable(Color.parseColor("#0B1026")));
        dialog.getWindow().setLayout(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT);
        dialog.show();
    }

    private static String asset(Activity activity, String name) {
        try (InputStream in = activity.getAssets().open(name)) {
            ByteArrayOutputStream out = new ByteArrayOutputStream();
            byte[] buffer = new byte[16 * 1024];
            for (int n = in.read(buffer); n != -1; n = in.read(buffer)) {
                out.write(buffer, 0, n);
            }
            return out.toString("UTF-8");
        } catch (IOException e) {
            return null;
        }
    }
}
