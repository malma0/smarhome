package ru.jarvis.home;

import android.app.NotificationManager;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;

import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;

/**
 * "Стоп" on a reminder's notification: tells Jarvis (POST /api/reminders/stop) to silence that
 * ring at home - the house may be empty, chiming for a minute - and takes the notification away.
 */
public class StopReceiver extends BroadcastReceiver {
    @Override
    public void onReceive(Context context, Intent intent) {
        long ring = intent.getLongExtra("ring", -1);
        int notification = intent.getIntExtra("notification", 0);
        ((NotificationManager) context.getSystemService(Context.NOTIFICATION_SERVICE)).cancel(notification);
        SharedPreferences p = WatchService.prefs(context);
        String address = p.getString("address", ""), pin = p.getString("pin", "");
        if (ring < 0 || address.isEmpty() || pin.isEmpty()) return;
        PendingResult pending = goAsync();  // the network off the main thread, the receiver kept alive meanwhile
        new Thread(() -> {
            try {
                HttpURLConnection c = (HttpURLConnection) new URL("http://" + address + "/api/reminders/stop").openConnection();
                c.setConnectTimeout(4000);
                c.setReadTimeout(6000);
                c.setRequestMethod("POST");
                c.setDoOutput(true);
                c.setRequestProperty("X-Pin", pin);
                c.setRequestProperty("Content-Type", "application/json");
                try (OutputStream out = c.getOutputStream()) {
                    out.write(("{\"ring\": " + ring + "}").getBytes(StandardCharsets.UTF_8));
                }
                c.getResponseCode();
                c.disconnect();
            } catch (Exception ignored) {
                // no house in reach: the chime at home stops by itself after a minute
            } finally {
                pending.finish();
            }
        }).start();
    }
}
