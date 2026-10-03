package ru.jarvis.home;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.ServiceInfo;
import android.os.Build;
import android.os.IBinder;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.util.HashMap;
import java.util.HashSet;
import java.util.Map;
import java.util.Set;

/**
 * Watches the house while the app is closed: asks Jarvis for the dangers going on
 * (GET /api/alerts) every POLL_SECONDS and puts each new one up as a loud notification;
 * a danger that's over takes its notification away. Timers and reminders that rang at
 * home come in the same answer and go up once each, on their own channel. Runs as a foreground service -
 * Android keeps it alive, with a quiet "Jarvis следит за домом" line in the shade.
 */
public class WatchService extends Service {
    static final int POLL_SECONDS = 10;
    static final String WATCH_CHANNEL = "watch", ALARM_CHANNEL = "alarms", REMINDER_CHANNEL = "reminders";
    static final int WATCH_ID = 1;

    private volatile boolean running;
    private Thread loop;
    private final Map<String, Integer> shown = new HashMap<>();  // alert key@since -> its notification id
    private int nextId = 100;

    static void start(Context context) {
        Intent intent = new Intent(context, WatchService.class);
        if (Build.VERSION.SDK_INT >= 26) context.startForegroundService(intent);
        else context.startService(intent);
    }

    static void stop(Context context) {
        context.stopService(new Intent(context, WatchService.class));
    }

    static SharedPreferences prefs(Context context) {
        return context.getSharedPreferences("jarvis", MODE_PRIVATE);
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        channels();
        Notification quiet = watchNotification("Jarvis следит за домом");
        if (Build.VERSION.SDK_INT >= 34) startForeground(WATCH_ID, quiet, ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE);
        else startForeground(WATCH_ID, quiet);
        if (!running) {
            running = true;
            loop = new Thread(this::watch);
            loop.start();
        }
        return START_STICKY;  // killed for memory - brought back
    }

    @Override
    public void onDestroy() {
        running = false;
        if (loop != null) loop.interrupt();
        super.onDestroy();
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }

    private void watch() {
        String lastLine = "";
        while (running) {
            SharedPreferences p = prefs(this);
            String address = p.getString("address", ""), pin = p.getString("pin", "");
            String line;
            if (address.isEmpty() || pin.isEmpty()) {
                line = "Открой приложение, чтобы подключиться к дому";
            } else {
                try {
                    int code = check(address, pin);
                    line = code == 200 ? "Jarvis следит за домом"
                            : code == 401 || code == 429 ? "PIN изменился — открой приложение и войди заново"
                            : "Джарвис не отвечает — пробую снова";
                    if (code == 401 || code == 429) p.edit().remove("pin").apply();  // no lockout from retrying a wrong PIN
                } catch (Exception e) {
                    line = "Нет связи с домом — пробую снова";
                }
            }
            if (!line.equals(lastLine)) {
                manager().notify(WATCH_ID, watchNotification(line));
                lastLine = line;
            }
            try {
                Thread.sleep(POLL_SECONDS * 1000L);
            } catch (InterruptedException e) {
                return;
            }
        }
    }

    /** One look at the house; new dangers become notifications, finished ones go. Returns the HTTP code. */
    private int check(String address, String pin) throws Exception {
        String owner = prefs(this).getString("owner", "");  // whose phone: their reminders and everyone's
        String query = owner.isEmpty() ? "" : "?who=" + java.net.URLEncoder.encode(owner, "UTF-8");
        HttpURLConnection c = (HttpURLConnection) new URL("http://" + address + "/api/alerts" + query).openConnection();
        c.setConnectTimeout(4000);
        c.setReadTimeout(8000);
        c.setRequestProperty("X-Pin", pin);
        int code = c.getResponseCode();
        if (code != 200) {
            c.disconnect();
            return code;
        }
        InputStream in = c.getInputStream();
        ByteArrayOutputStream body = new ByteArrayOutputStream();
        byte[] buf = new byte[4096];
        for (int n; (n = in.read(buf)) > 0; ) body.write(buf, 0, n);
        c.disconnect();
        JSONArray alerts = new JSONObject(body.toString("UTF-8")).getJSONArray("alerts");
        Set<String> now = new HashSet<>();
        for (int i = 0; i < alerts.length(); i++) {
            JSONObject a = alerts.getJSONObject(i);
            String key = a.optString("key") + "@" + a.optString("since");
            now.add(key);
            if (!shown.containsKey(key)) {
                int id = nextId++;
                shown.put(key, id);
                manager().notify(id, alarmNotification(a.optString("title") + " " + a.optString("where"), a.optString("advice")));
            }
        }
        for (String key : new HashSet<>(shown.keySet())) {
            if (!now.contains(key)) manager().cancel(shown.remove(key));  // the sensor went quiet
        }
        JSONArray rang = new JSONObject(body.toString("UTF-8")).optJSONArray("reminders");
        if (rang != null) {
            // each ring once, even across a restart of the service: the last one shown is remembered
            SharedPreferences p = prefs(this);
            long seen = p.getLong("ring_seen", 0);
            for (int i = 0; i < rang.length(); i++) {
                JSONObject r = rang.getJSONObject(i);
                long id = r.optLong("id");
                if (id <= seen) continue;
                manager().notify(nextId++, reminderNotification("timer".equals(r.optString("kind")) ? "Таймер" : "Напоминание", r.optString("text")));
                seen = id;
            }
            p.edit().putLong("ring_seen", seen).apply();
        }
        return code;
    }

    // ------------------------------------------------------------ notifications

    private NotificationManager manager() {
        return (NotificationManager) getSystemService(NOTIFICATION_SERVICE);
    }

    private void channels() {
        if (Build.VERSION.SDK_INT < 26) return;
        NotificationChannel watch = new NotificationChannel(WATCH_CHANNEL, "Наблюдение за домом", NotificationManager.IMPORTANCE_MIN);
        watch.setShowBadge(false);
        NotificationChannel alarms = new NotificationChannel(ALARM_CHANNEL, "Тревоги", NotificationManager.IMPORTANCE_HIGH);
        alarms.enableVibration(true);
        alarms.setVibrationPattern(new long[]{0, 600, 300, 600, 300, 600});
        alarms.setBypassDnd(true);
        NotificationChannel reminders = new NotificationChannel(REMINDER_CHANNEL, "Напоминания и таймеры", NotificationManager.IMPORTANCE_HIGH);
        reminders.enableVibration(true);
        manager().createNotificationChannel(watch);
        manager().createNotificationChannel(alarms);
        manager().createNotificationChannel(reminders);
    }

    private PendingIntent openApp() {
        Intent intent = new Intent(this, MainActivity.class).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_CLEAR_TOP);
        return PendingIntent.getActivity(this, 0, intent, PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
    }

    @SuppressWarnings("deprecation")
    private Notification.Builder builder(String channel) {
        return Build.VERSION.SDK_INT >= 26 ? new Notification.Builder(this, channel) : new Notification.Builder(this);
    }

    private Notification watchNotification(String text) {
        return builder(WATCH_CHANNEL)
                .setSmallIcon(android.R.drawable.ic_lock_idle_lock)
                .setContentTitle("Jarvis")
                .setContentText(text)
                .setOngoing(true)
                .setContentIntent(openApp())
                .build();
    }

    @SuppressWarnings("deprecation")
    private Notification reminderNotification(String title, String text) {
        Notification.Builder b = builder(REMINDER_CHANNEL)
                .setSmallIcon(android.R.drawable.ic_popup_reminder)
                .setContentTitle(title)
                .setContentText(text)
                .setStyle(new Notification.BigTextStyle().bigText(text))
                .setCategory(Notification.CATEGORY_REMINDER)
                .setAutoCancel(true)
                .setContentIntent(openApp());
        if (Build.VERSION.SDK_INT < 26) b.setPriority(Notification.PRIORITY_HIGH).setDefaults(Notification.DEFAULT_ALL);
        return b.build();
    }

    @SuppressWarnings("deprecation")
    private Notification alarmNotification(String title, String text) {
        Notification.Builder b = builder(ALARM_CHANNEL)
                .setSmallIcon(android.R.drawable.stat_sys_warning)
                .setContentTitle("Тревога: " + title)
                .setContentText(text)
                .setStyle(new Notification.BigTextStyle().bigText(text))
                .setCategory(Notification.CATEGORY_ALARM)
                .setAutoCancel(true)
                .setContentIntent(openApp());
        if (Build.VERSION.SDK_INT < 26) b.setPriority(Notification.PRIORITY_MAX).setDefaults(Notification.DEFAULT_ALL);
        return b.build();
    }
}
