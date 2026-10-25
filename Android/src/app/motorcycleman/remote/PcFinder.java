package app.motorcycleman.remote;

import java.io.BufferedReader;
import java.io.IOException;
import java.io.InputStreamReader;
import java.io.UnsupportedEncodingException;
import java.net.DatagramPacket;
import java.net.DatagramSocket;
import java.net.HttpURLConnection;
import java.net.Inet4Address;
import java.net.InetAddress;
import java.net.NetworkInterface;
import java.net.SocketException;
import java.net.URL;
import java.net.URLEncoder;
import java.util.ArrayList;
import java.util.Collections;
import java.util.HashSet;
import java.util.List;
import java.util.Set;
import java.util.concurrent.Callable;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

// Finds Motorcycle Man on the Wi-Fi without a QR code. It asks every address on the phone's own network (and
// an emulator's host computer) "are you Motorcycle Man?" on Motorcycle Man's port; Motorcycle Man answers with its PC's name and
// nothing secret. To connect, the phone then asks that PC for its link, which Motorcycle Man hands over only
// after the user taps Allow on the PC, where it shows the same code as the phone, or types on the
// phone the six digits the PC shows. An emulator on that PC itself is let in at once.
// Plain Java with no Android parts, so it can be tried on a PC too.
final class PcFinder {
    static final int PORT = 8080;

    static final class Pc {
        final String address;
        final String name;
        final int version;  // 2 and newer take the PC's code typed on the phone

        Pc(String address, String name, int version) {
            this.address = address;
            this.name = name;
            this.version = version;
        }
    }

    private PcFinder() {
    }

    // The networks this device is on, as address beginnings like "192.168.1.".
    static List<String> localNetworks() {
        List<String> networks = new ArrayList<>();
        try {
            for (NetworkInterface face : Collections.list(NetworkInterface.getNetworkInterfaces())) {
                if (!face.isUp() || face.isLoopback()) {
                    continue;
                }
                for (InetAddress address : Collections.list(face.getInetAddresses())) {
                    if (address instanceof Inet4Address && address.isSiteLocalAddress()) {
                        String ip = address.getHostAddress();
                        String network = ip.substring(0, ip.lastIndexOf('.') + 1);
                        if (!networks.contains(network)) {
                            networks.add(network);
                        }
                    }
                }
            }
        } catch (SocketException ignored) {
            // No network information; only the emulator's host is tried.
        }
        return networks;
    }

    // Every Motorcycle Man that answers, asking many addresses at once with a short wait each. The emulator's
    // host (10.0.2.2, as in BlueStacks) is asked too.
    static List<Pc> find(List<String> networks) {
        List<String> addresses = new ArrayList<>();
        addresses.add("10.0.2.2");
        for (String network : networks) {
            for (int i = 1; i <= 254; i++) {
                addresses.add(network + i);
            }
        }
        ExecutorService pool = Executors.newFixedThreadPool(48);
        List<Future<Pc>> answers = new ArrayList<>();
        for (final String address : addresses) {
            answers.add(pool.submit(new Callable<Pc>() {
                @Override
                public Pc call() {
                    return hello(address);
                }
            }));
        }
        List<Pc> found = new ArrayList<>();
        Set<String> names = new HashSet<>();
        for (Future<Pc> answer : answers) {
            try {
                Pc pc = answer.get();
                if (pc != null && names.add(pc.name)) {  // one PC can answer on two addresses
                    found.add(pc);
                }
            } catch (Exception ignored) {
                // That address didn't answer.
            }
        }
        pool.shutdownNow();
        return found;
    }

    static Pc hello(String address) {
        String reply = get("http://" + address + ":" + PORT + "/hello", 700, 1200);
        if (reply == null || !"motorcycle-man".equals(field(reply, "app"))) {
            return null;
        }
        String name = field(reply, "name");
        return new Pc(address, name == null || name.isEmpty() ? address : name, number(reply, "version"));
    }

    // Asks the PC to let this phone in. The PC shows the code and waits up to a minute for Allow.
    // Returns Motorcycle Man's phone link, or null when it wasn't allowed or the PC didn't answer.
    static String pair(Pc pc, String device, String code) {
        String reply = get("http://" + pc.address + ":" + PORT + "/pair?device=" + encode(device) + "&code=" + code,
                3000, 75000);
        String token = reply == null ? null : field(reply, "token");
        return token == null ? null : link(pc.address, token);
    }

    // Answers the PC's question with the six digits it shows under Allow, typed on this phone (code is
    // the phone's own four). Returns "ok", "wrong code" or "no request", or null when the PC didn't
    // answer; on "ok" the link arrives through pair().
    static String answer(Pc pc, String code, String typed) {
        String reply = get("http://" + pc.address + ":" + PORT + "/pair_code?match=" + code + "&code=" + encode(typed),
                3000, 5000);
        if (reply == null) {
            return null;
        }
        return Pattern.compile("\"ok\"\\s*:\\s*true").matcher(reply).find() ? "ok" : field(reply, "error");
    }

    static String link(String address, String token) {
        return "http://" + address + ":" + PORT + "/?token=" + encode(token);
    }

    // Whether Motorcycle Man at this address takes the link's secret, as after the PC got a new address.
    static boolean accepts(String address, String token) {
        return get("http://" + address + ":" + PORT + "/status?token=" + encode(token), 1500, 3000) != null;
    }

    // Whether Motorcycle Man at a link's own address takes its secret: "http://192.168.1.20:8080" on the
    // Wi-Fi, or "https://<words>.trycloudflare.com" through the internet link (a little slower).
    static boolean answers(String base, String token) {
        return get(base + "/status?token=" + encode(token), 3000, 6000) != null;
    }

    // Wake-on-LAN: the "magic packet" (six 0xFF bytes, then the PC's network card address sixteen times) to everyone
    // on the Wi-Fi, a few times over. A PC whose card may wake it (Windows' Device Manager says so) wakes up.
    static boolean wake(String mac, String broadcast) {
        String hex = mac == null ? "" : mac.replaceAll("[^0-9A-Fa-f]", "");
        if (hex.length() != 12) {
            return false;
        }
        byte[] packet = new byte[102];
        for (int i = 0; i < packet.length; i++) {
            packet[i] = i < 6 ? (byte) 0xFF : (byte) Integer.parseInt(hex.substring(i % 6 * 2, i % 6 * 2 + 2), 16);
        }
        boolean sent = false;
        try (DatagramSocket socket = new DatagramSocket()) {
            socket.setBroadcast(true);
            for (int round = 0; round < 3; round++) {
                for (String to : new String[] {broadcast, "255.255.255.255"}) {
                    for (int port : new int[] {9, 7}) {
                        try {
                            if (to != null && !to.isEmpty()) {
                                socket.send(new DatagramPacket(packet, packet.length, InetAddress.getByName(to), port));
                                sent = true;
                            }
                        } catch (IOException | RuntimeException ignored) {
                            // not reachable from this network
                        }
                    }
                }
            }
        } catch (IOException e) {
            return sent;
        }
        return sent;
    }

    static String get(String url, int connectMs, int readMs) {
        HttpURLConnection connection = null;
        try {
            connection = (HttpURLConnection) new URL(url).openConnection();
            connection.setConnectTimeout(connectMs);
            connection.setReadTimeout(readMs);
            if (connection.getResponseCode() != 200) {
                return null;
            }
            StringBuilder text = new StringBuilder();
            try (BufferedReader in = new BufferedReader(new InputStreamReader(connection.getInputStream(), "UTF-8"))) {
                String line;
                while ((line = in.readLine()) != null && text.length() < 8192) {
                    text.append(line);
                }
            }
            return text.toString();
        } catch (IOException | RuntimeException e) {
            return null;
        } finally {
            if (connection != null) {
                connection.disconnect();
            }
        }
    }

    // A text value from Motorcycle Man's small JSON answers, like "name" in {"app": "motorcycle-man", "name": "LAPTOP"}.
    static String field(String json, String name) {
        Matcher m = Pattern.compile("\"" + Pattern.quote(name) + "\"\\s*:\\s*\"((?:[^\"\\\\]|\\\\.)*)\"").matcher(json);
        return m.find() ? unescape(m.group(1)) : null;
    }

    static int number(String json, String name) {
        Matcher m = Pattern.compile("\"" + Pattern.quote(name) + "\"\\s*:\\s*(\\d{1,6})").matcher(json);
        return m.find() ? Integer.parseInt(m.group(1)) : 0;
    }

    private static String unescape(String text) {
        StringBuilder out = new StringBuilder();
        for (int i = 0; i < text.length(); i++) {
            char c = text.charAt(i);
            if (c != '\\' || i + 1 >= text.length()) {
                out.append(c);
                continue;
            }
            char next = text.charAt(++i);
            if (next == 'u' && i + 4 < text.length()) {
                out.append((char) Integer.parseInt(text.substring(i + 1, i + 5), 16));
                i += 4;
            } else if (next == 'n') {
                out.append('\n');
            } else {
                out.append(next);
            }
        }
        return out.toString();
    }

    static String encode(String text) {
        try {
            return URLEncoder.encode(text, "UTF-8");
        } catch (UnsupportedEncodingException e) {
            return "";
        }
    }
}
