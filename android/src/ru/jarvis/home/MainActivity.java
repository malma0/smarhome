package ru.jarvis.home;

import android.Manifest;
import android.app.Activity;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.content.res.Configuration;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.os.PowerManager;
import android.provider.Settings;
import android.net.Uri;
import android.util.Base64;
import android.view.View;
import android.view.Window;
import android.webkit.JavascriptInterface;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;

import org.json.JSONObject;

import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.Inet4Address;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.net.InterfaceAddress;
import java.net.NetworkInterface;
import java.net.Socket;
import java.net.URL;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;

/**
 * The control panel Jarvis serves (http://computer:8765/panel/) in a full-screen
 * WebView. The start page (assets/start.html) finds Jarvis in the home network:
 * the address that worked last time, else every host of the phone's Wi-Fi network
 * asked on the panel's port, else typed by hand.
 */
public class MainActivity extends Activity {
    static final int PORT = 8765;
    static final String START = "file:///android_asset/start.html";
    static final int MIC_PERMISSION = 1;
    static final int NOTIFY_PERMISSION = 2;

    WebView web;
    SharedPreferences prefs;
    final Handler main = new Handler(Looper.getMainLooper());
    volatile boolean scanning;
    final VoiceRecorder voice = new VoiceRecorder();

    @Override
    protected void onCreate(Bundle saved) {
        super.onCreate(saved);
        prefs = getSharedPreferences("jarvis", MODE_PRIVATE);
        paintBars();
        web = new WebView(this);
        web.setBackgroundColor(night() ? 0xFF07090D : 0xFFF3F5F8);
        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);  // the panel keeps its PIN in localStorage
        s.setTextZoom(100);  // the panel scales itself to the screen - the system font size would break the plan
        web.addJavascriptInterface(new Bridge(), "JarvisApp");
        web.setWebViewClient(new WebViewClient() {
            @Override
            public void onReceivedError(WebView view, WebResourceRequest request, WebResourceError error) {
                if (request.isForMainFrame()) showStart("offline");
            }

            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                Uri url = request.getUrl();
                if ("tel".equals(url.getScheme())) {  // the alarm's "Позвонить 112": the dialer, the user presses call
                    startActivity(new Intent(Intent.ACTION_DIAL, url));
                    return true;
                }
                return false;
            }
        });
        setContentView(web);
        showStart("");
    }

    void showStart(String mode) {
        web.loadUrl(mode.isEmpty() ? START : START + "?mode=" + mode);
    }

    void open(String address) {
        prefs.edit().putString("address", address).apply();
        web.loadUrl("http://" + address + "/panel/");
    }

    void js(String code) {
        main.post(() -> web.evaluateJavascript(code, null));
    }

    // ------------------------------------------------------------ the bars around the page

    boolean night() {
        return (getResources().getConfiguration().uiMode & Configuration.UI_MODE_NIGHT_MASK) == Configuration.UI_MODE_NIGHT_YES;
    }

    void paintBars() {
        Window w = getWindow();
        boolean dark = night();
        w.setStatusBarColor(dark ? 0xFF07090D : 0xFFF3F5F8);
        w.setNavigationBarColor(dark ? 0xFF0A0D12 : 0xFFFFFFFF);
        int flags = 0;
        if (!dark) {
            flags |= View.SYSTEM_UI_FLAG_LIGHT_STATUS_BAR;
            if (Build.VERSION.SDK_INT >= 26) flags |= View.SYSTEM_UI_FLAG_LIGHT_NAVIGATION_BAR;
        }
        w.getDecorView().setSystemUiVisibility(flags);
    }

    @Override
    public void onConfigurationChanged(Configuration config) {
        super.onConfigurationChanged(config);
        paintBars();
    }

    // ------------------------------------------------------------ the microphone, for the chat

    void listen() {
        if (Build.VERSION.SDK_INT >= 23 && checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(new String[]{Manifest.permission.RECORD_AUDIO}, MIC_PERMISSION);  // then onRequestPermissionsResult
            return;
        }
        if (voice.start()) js("window.onListening && onListening()");
        else js("window.onVoiceError && onVoiceError('Микрофон занят или недоступен')");
    }

    // ------------------------------------------------------------ watching the house while the app is closed

    void watch() {
        if (Build.VERSION.SDK_INT >= 33 && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(new String[]{Manifest.permission.POST_NOTIFICATIONS}, NOTIFY_PERMISSION);  // then here again
            return;
        }
        WatchService.start(this);
        askToStayAwake();
    }

    /** Once: let the watcher reach the house while the phone sleeps (else Android cuts its network). */
    void askToStayAwake() {
        if (Build.VERSION.SDK_INT < 23 || prefs.getBoolean("asked_battery", false)) return;
        prefs.edit().putBoolean("asked_battery", true).apply();
        PowerManager power = (PowerManager) getSystemService(POWER_SERVICE);
        if (power.isIgnoringBatteryOptimizations(getPackageName())) return;
        try {
            startActivity(new Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS, Uri.parse("package:" + getPackageName())));
        } catch (Exception ignored) {
            // a phone without that screen: the user can find it in the app's battery settings
        }
    }

    @Override
    public void onRequestPermissionsResult(int code, String[] permissions, int[] results) {
        if (code == NOTIFY_PERMISSION) {
            boolean ok = results.length > 0 && results[0] == PackageManager.PERMISSION_GRANTED;
            WatchService.start(this);  // watches either way; without the permission the alarms can't show
            if (!ok) js("window.JV && JV.toast('Уведомления запрещены — тревоги не будут приходить. Разреши их в настройках телефона', true)");
            askToStayAwake();
            return;
        }
        if (code != MIC_PERMISSION) return;
        if (results.length > 0 && results[0] == PackageManager.PERMISSION_GRANTED) listen();
        else js("window.onVoiceError && onVoiceError('Нет доступа к микрофону - разреши его в настройках телефона')");
    }

    /** The recording, as base64 WAV, to the page - it sends it to Jarvis with its PIN. */
    void sendRecording() {
        new Thread(() -> {
            byte[] wav = voice.stop();
            if (wav == null) {  // stopped already - the app went to the background mid-phrase
                js("window.onVoiceError && onVoiceError('Запись прервалась - скажи ещё раз')");
                return;
            }
            js("window.onVoice && onVoice(" + JSONObject.quote(Base64.encodeToString(wav, Base64.NO_WRAP)) + ")");
        }).start();
    }

    // ------------------------------------------------------------ the phone's back button

    @Override
    public void onBackPressed() {
        String url = web.getUrl();
        if (url == null || url.startsWith("file:")) {
            leave();
            return;
        }
        // the panel closes an open room or dialog first and answers true
        web.evaluateJavascript("window.jarvisBack ? jarvisBack() : false", result -> {
            if (!"true".equals(result)) leave();
        });
    }

    void leave() {
        super.onBackPressed();
    }

    @Override
    protected void onPause() {
        super.onPause();
        if (voice.recording()) voice.stop();  // no listening behind the user's back
        web.onPause();
        web.pauseTimers();  // the page's refresh stops while it's not seen - the watcher service keeps the alarms
    }

    @Override
    protected void onResume() {
        super.onResume();
        web.resumeTimers();
        web.onResume();
    }

    // ------------------------------------------------------------ finding Jarvis

    /** "192.168.0.5", "http://192.168.0.5:8765/panel/" -> "192.168.0.5:8765". */
    static String normalize(String typed) {
        String a = typed == null ? "" : typed.trim().replaceFirst("^[a-zA-Z]+://", "");
        int slash = a.indexOf('/');
        if (slash >= 0) a = a.substring(0, slash);
        if (a.isEmpty()) return null;
        return a.contains(":") ? a : a + ":" + PORT;
    }

    /** Asks the address the question only Jarvis's panel answers this way. */
    static boolean isJarvis(String address) {
        HttpURLConnection c = null;
        try {
            c = (HttpURLConnection) new URL("http://" + address + "/api/hello").openConnection();
            c.setConnectTimeout(800);
            c.setReadTimeout(1500);
            if (c.getResponseCode() != 200) return false;
            byte[] buf = new byte[256];
            InputStream in = c.getInputStream();
            int n = in.read(buf);
            return n > 0 && new String(buf, 0, n, "UTF-8").contains("\"jarvis\"");
        } catch (Exception e) {
            return false;
        } finally {
            if (c != null) c.disconnect();
        }
    }

    /** The phone's own addresses in home networks (192.168.x, 10.x) - the networks to look in. */
    static List<Inet4Address> ownAddresses() {
        List<Inet4Address> found = new ArrayList<>();
        try {
            for (NetworkInterface nic : Collections.list(NetworkInterface.getNetworkInterfaces())) {
                if (!nic.isUp() || nic.isLoopback()) continue;
                for (InterfaceAddress ia : nic.getInterfaceAddresses()) {
                    InetAddress a = ia.getAddress();
                    if (a instanceof Inet4Address && a.isSiteLocalAddress()) found.add((Inet4Address) a);
                }
            }
        } catch (Exception ignored) {
        }
        return found;
    }

    /** Every host of the phone's /24 network, asked on the panel's port at once. */
    void scan() {
        if (scanning) return;
        scanning = true;
        new Thread(() -> {
            AtomicInteger hits = new AtomicInteger();
            ExecutorService pool = Executors.newFixedThreadPool(48);
            for (Inet4Address own : ownAddresses()) {
                byte[] b = own.getAddress();
                String prefix = (b[0] & 255) + "." + (b[1] & 255) + "." + (b[2] & 255) + ".";
                for (int host = 1; host < 255; host++) {
                    if (host == (b[3] & 255)) continue;
                    final String ip = prefix + host;
                    pool.execute(() -> {
                        try (Socket socket = new Socket()) {
                            socket.connect(new InetSocketAddress(ip, PORT), 400);
                        } catch (Exception e) {
                            return;
                        }
                        String address = ip + ":" + PORT;
                        if (isJarvis(address)) {
                            hits.incrementAndGet();
                            js("window.onFound && onFound(" + JSONObject.quote(address) + ")");
                        }
                    });
                }
            }
            pool.shutdown();
            try {
                pool.awaitTermination(30, TimeUnit.SECONDS);
            } catch (InterruptedException ignored) {
            }
            scanning = false;
            js("window.onScanDone && onScanDone(" + hits.get() + ")");
        }).start();
    }

    /** What the start page may ask of the app. */
    class Bridge {
        @JavascriptInterface
        public String saved() {
            return prefs.getString("address", "");
        }

        @JavascriptInterface
        public void connect(String typed) {
            new Thread(() -> {
                String address = normalize(typed);
                boolean ok = address != null && isJarvis(address);
                main.post(() -> {
                    if (ok) open(address);
                    else js("window.onFailed && onFailed(" + JSONObject.quote(typed == null ? "" : typed) + ")");
                });
            }).start();
        }

        @JavascriptInterface
        public void scan() {
            MainActivity.this.scan();
        }

        @JavascriptInterface
        public void startListening() {
            main.post(MainActivity.this::listen);
        }

        @JavascriptInterface
        public void stopListening() {
            sendRecording();
        }

        @JavascriptInterface
        public void cancelListening() {
            new Thread(voice::stop).start();
        }

        /** The panel's PIN, for the watcher's own questions to the house; on = watch while closed. */
        @JavascriptInterface
        public void watch(String pin) {
            prefs.edit().putString("pin", pin).putBoolean("watch", true).apply();
            main.post(MainActivity.this::watch);
        }

        @JavascriptInterface
        public void unwatch() {
            prefs.edit().putBoolean("watch", false).apply();
            WatchService.stop(MainActivity.this);
        }

        @JavascriptInterface
        public boolean watching() {
            return prefs.getBoolean("watch", false);
        }

        @JavascriptInterface
        public void forget() {
            prefs.edit().remove("address").apply();
            main.post(() -> showStart(""));
        }
    }
}
