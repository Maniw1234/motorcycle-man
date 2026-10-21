package app.motorcycleman.remote;

import android.app.PendingIntent;
import android.appwidget.AppWidgetManager;
import android.appwidget.AppWidgetProvider;
import android.content.Context;
import android.content.Intent;
import android.widget.RemoteViews;

// The home-screen widget: Talk (opens Motorcycle Man listening), the PC's screen, and the remote.
public class TalkWidget extends AppWidgetProvider {
    @Override
    public void onUpdate(Context context, AppWidgetManager manager, int[] widgetIds) {
        for (int widgetId : widgetIds) {
            RemoteViews views = new RemoteViews(context.getPackageName(), R.layout.widget_talk);
            views.setOnClickPendingIntent(R.id.widget_helmet, action(context, "", 1));
            views.setOnClickPendingIntent(R.id.widget_talk, action(context, "listen", 2));
            views.setOnClickPendingIntent(R.id.widget_screen, action(context, "screen", 3));
            views.setOnClickPendingIntent(R.id.widget_remote, action(context, "remote", 4));
            manager.updateAppWidget(widgetId, views);
        }
    }

    // Opens Motorcycle Man and has its page do `what` ("" just opens it).
    static PendingIntent action(Context context, String what, int code) {
        Intent intent = new Intent(context, MainActivity.class).putExtra(MainActivity.EXTRA_DO, what)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
        return PendingIntent.getActivity(context, code, intent, PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
    }
}
