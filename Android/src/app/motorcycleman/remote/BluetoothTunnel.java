package app.motorcycleman.remote;

import android.annotation.SuppressLint;
import android.bluetooth.BluetoothDevice;
import android.bluetooth.BluetoothSocket;

import java.io.DataInputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.InetAddress;
import java.net.ServerSocket;
import java.net.Socket;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;

// The phone page over Bluetooth, when the phone and the PC share no Wi-Fi or internet: a little web server on
// this phone (127.0.0.1) whose every connection is carried over one Bluetooth link to Motorcycle Man on the PC,
// which passes it to its own web server (see the PC's bluetooth_bridge.py, which has the same frames:
// kind, stream, length, then the bytes). The page loads from it as if it came from the PC.
final class BluetoothTunnel {
    static final UUID SERVICE = UUID.fromString("7b4d2f18-3a54-4b9e-9c1f-6d0e5a8b2c31");
    private static final int OPEN = 1, DATA = 2, CLOSE = 3, CHUNK = 32 * 1024, FRAME_LIMIT = 1 << 20;
    private final BluetoothSocket link;
    private final ServerSocket local;
    private final OutputStream out;
    private final Map<Integer, Socket> streams = new ConcurrentHashMap<>();
    private int nextStream = 1;
    private volatile boolean closed;

    private BluetoothTunnel(BluetoothSocket link, ServerSocket local) throws IOException {
        this.link = link;
        this.local = local;
        this.out = link.getOutputStream();
    }

    // Connects to Motorcycle Man on a PC this phone is paired with. Needs Android's Bluetooth permission, which the
    // caller (MainActivity.connectBluetooth) checks first, also catching the SecurityException of a refusal.
    @SuppressLint("MissingPermission")
    static BluetoothTunnel open(BluetoothDevice pc) throws IOException {
        BluetoothSocket link = pc.createRfcommSocketToServiceRecord(SERVICE);
        link.connect();
        ServerSocket local = new ServerSocket(0, 50, InetAddress.getByName("127.0.0.1"));
        final BluetoothTunnel tunnel = new BluetoothTunnel(link, local);
        new Thread(new Runnable() {
            @Override
            public void run() {
                tunnel.acceptLoop();
            }
        }).start();
        new Thread(new Runnable() {
            @Override
            public void run() {
                tunnel.readLoop();
            }
        }).start();
        return tunnel;
    }

    int port() {
        return local.getLocalPort();
    }

    boolean isOpen() {
        return !closed;
    }

    private synchronized void send(int kind, int stream, byte[] data, int length) throws IOException {
        byte[] header = {(byte) kind, (byte) (stream >> 8), (byte) stream,
                (byte) (length >> 24), (byte) (length >> 16), (byte) (length >> 8), (byte) length};
        out.write(header);
        if (length > 0) {
            out.write(data, 0, length);
        }
        out.flush();
    }

    private void acceptLoop() {
        try {
            while (!closed) {
                final Socket page = local.accept();
                final int stream;
                synchronized (this) {
                    stream = nextStream;
                    nextStream = nextStream % 65535 + 1;
                }
                streams.put(stream, page);
                send(OPEN, stream, null, 0);
                new Thread(new Runnable() {
                    @Override
                    public void run() {
                        pump(stream, page);
                    }
                }).start();
            }
        } catch (IOException ignored) {
            // closed
        }
        close();
    }

    // What the page sends, over Bluetooth to the PC.
    private void pump(int stream, Socket page) {
        byte[] buffer = new byte[CHUNK];
        try {
            InputStream in = page.getInputStream();
            int count;
            while ((count = in.read(buffer)) > 0) {
                send(DATA, stream, buffer, count);
            }
        } catch (IOException ignored) {
            // the page or the link went away
        }
        if (streams.remove(stream) != null) {
            try {
                send(CLOSE, stream, null, 0);
            } catch (IOException ignored) {
                // the link is gone
            }
            quietly(page);
        }
    }

    // What the PC answers, back to each page connection.
    private void readLoop() {
        try {
            DataInputStream in = new DataInputStream(link.getInputStream());
            while (!closed) {
                int kind = in.readUnsignedByte(), stream = in.readUnsignedShort(), length = in.readInt();
                if (length < 0 || length > FRAME_LIMIT) {
                    break;
                }
                byte[] data = new byte[length];
                in.readFully(data);
                Socket page = streams.get(stream);
                if (page == null) {
                    continue;
                }
                if (kind == DATA) {
                    try {
                        page.getOutputStream().write(data);
                    } catch (IOException gone) {
                        streams.remove(stream);
                        quietly(page);
                        send(CLOSE, stream, null, 0);
                    }
                } else if (kind == CLOSE) {
                    streams.remove(stream);
                    quietly(page);
                }
            }
        } catch (IOException ignored) {
            // the PC went away
        }
        close();
    }

    void close() {
        closed = true;
        for (Socket page : streams.values()) {
            quietly(page);
        }
        streams.clear();
        try {
            local.close();
        } catch (IOException ignored) {
            // already closed
        }
        try {
            link.close();
        } catch (IOException ignored) {
            // already closed
        }
    }

    private static void quietly(Socket socket) {
        try {
            socket.close();
        } catch (IOException ignored) {
            // already closed
        }
    }
}
