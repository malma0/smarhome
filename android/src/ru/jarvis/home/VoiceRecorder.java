package ru.jarvis.home;

import android.media.AudioFormat;
import android.media.AudioRecord;
import android.media.MediaRecorder;

import java.io.ByteArrayOutputStream;

/**
 * The phone's microphone for the chat: 16 kHz mono 16-bit, what Jarvis's speech
 * recognition takes from the laptop's microphone too. start() .. stop() gives a WAV.
 */
class VoiceRecorder {
    static final int RATE = 16000;
    static final int MAX_SECONDS = 60;

    private AudioRecord record;
    private Thread reader;
    private final ByteArrayOutputStream pcm = new ByteArrayOutputStream();
    private volatile boolean running;

    /** False when the microphone can't be opened (taken by a call, no permission). */
    boolean start() {
        int min = AudioRecord.getMinBufferSize(RATE, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT);
        try {
            record = new AudioRecord(MediaRecorder.AudioSource.VOICE_RECOGNITION, RATE, AudioFormat.CHANNEL_IN_MONO,
                    AudioFormat.ENCODING_PCM_16BIT, Math.max(min, RATE));
            if (record.getState() != AudioRecord.STATE_INITIALIZED) {
                record.release();
                record = null;
                return false;
            }
            pcm.reset();
            record.startRecording();
        } catch (SecurityException | IllegalStateException e) {
            return false;
        }
        running = true;
        reader = new Thread(() -> {
            byte[] buf = new byte[RATE / 5];
            while (running && pcm.size() < RATE * 2 * MAX_SECONDS) {
                int n = record.read(buf, 0, buf.length);
                if (n > 0) pcm.write(buf, 0, n);
            }
        });
        reader.start();
        return true;
    }

    boolean recording() {
        return running;
    }

    /** Stops and returns what was said as a WAV file (null if nothing was recording). */
    byte[] stop() {
        if (!running) return null;
        running = false;
        try {
            reader.join(1000);
        } catch (InterruptedException ignored) {
        }
        try {
            record.stop();
        } catch (IllegalStateException ignored) {
        }
        record.release();
        record = null;
        return wav(pcm.toByteArray());
    }

    static byte[] wav(byte[] data) {
        ByteArrayOutputStream out = new ByteArrayOutputStream(44 + data.length);
        writeAscii(out, "RIFF");
        writeInt(out, 36 + data.length);
        writeAscii(out, "WAVE");
        writeAscii(out, "fmt ");
        writeInt(out, 16);
        writeShort(out, 1);  // PCM
        writeShort(out, 1);  // mono
        writeInt(out, RATE);
        writeInt(out, RATE * 2);  // bytes a second
        writeShort(out, 2);  // bytes a sample
        writeShort(out, 16);
        writeAscii(out, "data");
        writeInt(out, data.length);
        out.write(data, 0, data.length);
        return out.toByteArray();
    }

    private static void writeAscii(ByteArrayOutputStream out, String s) {
        for (int i = 0; i < s.length(); i++) out.write(s.charAt(i));
    }

    private static void writeInt(ByteArrayOutputStream out, int v) {
        out.write(v);
        out.write(v >> 8);
        out.write(v >> 16);
        out.write(v >> 24);
    }

    private static void writeShort(ByteArrayOutputStream out, int v) {
        out.write(v);
        out.write(v >> 8);
    }
}
