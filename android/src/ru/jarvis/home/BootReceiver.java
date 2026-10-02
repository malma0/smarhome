package ru.jarvis.home;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;

/** The phone was restarted: watching the house again, if it was on - without opening the app. */
public class BootReceiver extends BroadcastReceiver {
    @Override
    public void onReceive(Context context, Intent intent) {
        if (!Intent.ACTION_BOOT_COMPLETED.equals(intent.getAction())) return;
        if (WatchService.prefs(context).getBoolean("watch", false)) WatchService.start(context);
    }
}
