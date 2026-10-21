package app.motorcycleman.remote;

import android.content.Intent;
import android.os.Build;
import android.service.quicksettings.Tile;
import android.service.quicksettings.TileService;

// A quick-settings tile ("Talk to PC") in the phone's pull-down shade: one tap opens Motorcycle Man listening.
public class TalkTile extends TileService {
    @Override
    public void onStartListening() {
        Tile tile = getQsTile();
        if (tile != null) {
            tile.setLabel("Talk to PC");
            tile.setState(Tile.STATE_INACTIVE);
            tile.updateTile();
        }
    }

    @Override
    @SuppressWarnings("deprecation")  // the older way, for Android 13 and before
    public void onClick() {
        if (Build.VERSION.SDK_INT >= 34) {
            startActivityAndCollapse(TalkWidget.action(this, "listen", 5));  // Android 14 wants it this way
        } else {
            startActivityAndCollapse(new Intent(this, MainActivity.class).putExtra(MainActivity.EXTRA_DO, "listen")
                    .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));
        }
    }
}
