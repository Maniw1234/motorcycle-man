package app.motorcycleman.remote;

import android.Manifest;
import android.content.Context;
import android.content.pm.PackageManager;
import android.media.AudioFormat;
import android.media.AudioRecord;
import android.media.MediaRecorder;
import android.media.audiofx.AcousticEchoCanceler;
import android.media.audiofx.AudioEffect;
import android.media.audiofx.AutomaticGainControl;
import android.media.audiofx.NoiseSuppressor;
import android.os.ParcelFileDescriptor;
import android.os.Process;
import android.os.SystemClock;
import android.util.Log;

import java.io.Closeable;
import java.io.IOException;
import java.io.OutputStream;

// Holding the helmet with the app's own recording (Android 13 and newer, behind a hidden switch). The app
// records the microphone itself, with the phone's echo canceller on, and hands the sound to the speech service
// through a pipe (RecognizerIntent.EXTRA_AUDIO_SOURCE). The echo canceller takes the phone's own sound (Motorcycle
// Man's voice, music) out of what the service hears, and recording starts the moment the press does, so the first
// words wait in the pipe while the service gets ready. 16 kHz, mono, 16-bit: what speech services take.
final class OwnMic {
    static final int RATE = 16000;
    static final int CHANNELS = 1;
    static final int ENCODING = AudioFormat.ENCODING_PCM_16BIT;
    private static final String TAG = "MMSpeech";
    private static final int CHUNK_BYTES = RATE / 50 * 2;  // 20 ms of sound

    private final AudioRecord record;
    private final AudioEffect[] effects;
    private final ParcelFileDescriptor source;  // the pipe's read end, for the speech service
    private final OutputStream sink;  // the pipe's write end
    private final Thread pump;
    private volatile boolean recording = true;
    private boolean released;  // guarded by this

    private OwnMic(AudioRecord record, AudioEffect[] effects, ParcelFileDescriptor source, ParcelFileDescriptor sink) {
        this.record = record;
        this.effects = effects;
        this.source = source;
        this.sink = new ParcelFileDescriptor.AutoCloseOutputStream(sink);
        pump = new Thread(new Runnable() {
            @Override
            public void run() {
                pumpSound();
            }
        }, "MM own mic");
    }

    // A started recording, or null when this phone can't record this way (the press then listens the usual way).
    // cleanNoise: the phone's noise suppressor as well; evenLoudness: its automatic gain control as well. Both are off
    // unless asked: speech services do their own, and doing it twice can make the words harder to hear.
    static OwnMic open(Context context, boolean cleanNoise, boolean evenLoudness) {
        if (context.checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
            return null;
        }
        int least = AudioRecord.getMinBufferSize(RATE, AudioFormat.CHANNEL_IN_MONO, ENCODING);
        if (least <= 0) {
            return null;  // no 16 kHz mono recording on this phone
        }
        AudioRecord record = null;
        AudioEffect[] effects = new AudioEffect[3];
        ParcelFileDescriptor[] pipe = null;
        try {
            record = new AudioRecord(MediaRecorder.AudioSource.VOICE_RECOGNITION, RATE, AudioFormat.CHANNEL_IN_MONO,
                    ENCODING, Math.max(least, RATE * 4));  // room for 2 seconds
            if (record.getState() != AudioRecord.STATE_INITIALIZED) {
                throw new IllegalStateException("no recorder");
            }
            int session = record.getAudioSessionId();
            if (AcousticEchoCanceler.isAvailable()) {
                effects[0] = AcousticEchoCanceler.create(session);
            }
            if (cleanNoise && NoiseSuppressor.isAvailable()) {
                effects[1] = NoiseSuppressor.create(session);
            }
            if (evenLoudness && AutomaticGainControl.isAvailable()) {
                effects[2] = AutomaticGainControl.create(session);
            }
            for (AudioEffect effect : effects) {
                if (effect != null) {
                    effect.setEnabled(true);
                }
            }
            pipe = ParcelFileDescriptor.createPipe();
            record.startRecording();
            if (record.getRecordingState() != AudioRecord.RECORDSTATE_RECORDING) {
                throw new IllegalStateException("microphone busy");  // another app has it
            }
            OwnMic mic = new OwnMic(record, effects, pipe[0], pipe[1]);
            mic.pump.start();
            Log.i(TAG, "own microphone on, echo canceller " + (effects[0] != null ? "on" : "missing")
                    + (effects[1] != null ? ", noise suppressor on" : "") + (effects[2] != null ? ", gain control on" : ""));
            return mic;
        } catch (IOException | RuntimeException e) {
            Log.i(TAG, "own microphone unavailable: " + e.getClass().getSimpleName());
            releaseAll(record, effects);
            if (pipe != null) {
                closeQuietly(pipe[0]);
                closeQuietly(pipe[1]);
            }
            return null;
        }
    }

    // Where the speech service reads the sound (EXTRA_AUDIO_SOURCE).
    ParcelFileDescriptor source() {
        return source;
    }

    // Let go: recording stops and the sound ends, so the service gives its last results.
    void finish() {
        recording = false;
        stopRecording();
    }

    // The press is over. The pump thread releases the recorder once it is out of its read, so the recorder is never
    // released in the middle of one. Closing this end of the pipe means that, once the service closes its own,
    // a pump stuck writing to a full pipe gets an error and ends.
    void close() {
        finish();
        closeQuietly(source);
    }

    private void pumpSound() {
        Process.setThreadPriority(Process.THREAD_PRIORITY_URGENT_AUDIO);
        byte[] chunk = new byte[CHUNK_BYTES];
        long started = SystemClock.uptimeMillis();
        try {
            while (recording) {
                int got = record.read(chunk, 0, chunk.length);
                if (got > 0) {
                    sink.write(chunk, 0, got);
                } else if (got < 0) {
                    Log.i(TAG, "own microphone read error " + got);
                    break;
                } else {
                    SystemClock.sleep(10);  // nothing yet
                }
            }
        } catch (IOException e) {
            Log.i(TAG, "own microphone pipe closed");  // the service is done with the sound
        } finally {
            closeQuietly(sink);  // the end of the sound
            synchronized (this) {
                released = true;
                releaseAll(record, effects);
            }
            Log.i(TAG, "own microphone off after " + (SystemClock.uptimeMillis() - started) + " ms");
        }
    }

    private synchronized void stopRecording() {
        if (released) {
            return;
        }
        try {
            record.stop();  // the pump's read returns
        } catch (IllegalStateException e) {
            // already stopped
        }
    }

    private static void releaseAll(AudioRecord record, AudioEffect[] effects) {
        for (AudioEffect effect : effects) {
            if (effect != null) {
                effect.release();
            }
        }
        if (record != null) {
            record.release();
        }
    }

    private static void closeQuietly(Closeable closeable) {
        try {
            if (closeable != null) {
                closeable.close();
            }
        } catch (IOException e) {
            // closing anyway
        }
    }
}
