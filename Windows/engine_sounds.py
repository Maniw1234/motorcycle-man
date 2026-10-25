# The buddy's engine sounds, made from scratch (no recordings, so nothing to license): a small inline-four
# like a ZX-6R's, firing twice a turn, as WAV files. Each sound is a curve of engine speed and throttle
# over time; the sound is the engine's firing tone at that speed, with intake roar while the throttle is
# open and crackles from the exhaust when it closes.
import array, io, math, random, wave

RATE = 22050
IDLE = 1300  # rpm


def _ramp(points):
    # A curve through (time, value) points, eased between them.
    def value(t):
        if t <= points[0][0]:
            return points[0][1]
        for (t0, v0), (t1, v1) in zip(points, points[1:]):
            if t <= t1:
                x = (t - t0) / max(1e-6, t1 - t0)
                return v0 + (v1 - v0) * (x * x * (3 - 2 * x))
        return points[-1][1]
    return value


SOUNDS = {
    # name: (seconds, rpm points, throttle points, misfire chance a firing, crackle chance a second off throttle)
    "start": (1.7, [(0, 260), (0.5, 300), (0.56, 3400), (0.9, 2000), (1.7, IDLE)],
              [(0, 0.2), (0.5, 0.2), (0.56, 0.9), (0.8, 0.1), (1.7, 0.05)], 0.0, 4),
    "rev": (1.15, [(0, IDLE), (0.26, 9800), (0.42, 10200), (1.05, 1700), (1.15, IDLE)],
            [(0, 1), (0.36, 1), (0.42, 0), (1.15, 0)], 0.0, 26),
    "wheelie": (1.6, [(0, IDLE), (0.18, 7000), (0.32, 6500), (0.62, 12800), (0.9, 13000), (1.5, 1900), (1.6, IDLE)],
                [(0, 1), (0.84, 1), (0.9, 0), (1.6, 0)], 0.0, 30),
    "blip": (0.95, [(0, IDLE), (0.12, 6200), (0.34, IDLE), (0.48, IDLE), (0.6, 7400), (0.88, IDLE), (0.95, IDLE)],
             [(0, 1), (0.1, 1), (0.14, 0), (0.46, 0), (0.48, 1), (0.58, 1), (0.62, 0), (0.95, 0)], 0.0, 18),
    "sputter": (1.1, [(0, IDLE), (0.5, 1000), (1.0, 560), (1.1, 400)],
                [(0, 0.15), (1.1, 0)], 0.35, 0),
}


NAMES = list(SOUNDS) + ["listen"]  # and a little chirp when Motorcycle Man starts listening


def chirp(volume: float = 0.3, seconds: float = 0.14) -> array.array:
    count = int(seconds * RATE)
    out, phase = array.array("h", bytes(2 * count)), 0.0
    for i in range(count):
        t = i / RATE
        phase += (900 + 700 * t / seconds) / RATE  # rising, like a question
        fade = min(1.0, t / 0.01, (seconds - t) / 0.05)
        out[i] = int(math.sin(2 * math.pi * phase) * fade * volume * 32767)
    return out


def render(name: str, volume: float = 0.42, seed: int = 6) -> array.array:
    # The sound as 16-bit samples.
    if name == "listen":
        return chirp()
    seconds, rpm_points, throttle_points, misfire, crackle = SOUNDS[name]
    rpm_at, throttle_at = _ramp(rpm_points), _ramp(throttle_points)
    rnd = random.Random(f"{name}{seed}")
    count = int(seconds * RATE)
    out = array.array("h", bytes(2 * count))
    phase = cycle = 0.0
    firing, roar, soft, pops = 1.0, 0.0, 0.0, []
    tau = 2 * math.pi
    for i in range(count):
        t = i / RATE
        rpm, throttle = rpm_at(t), throttle_at(t)
        step = rpm / 30 / RATE * (1 + 0.006 * (rnd.random() - 0.5))  # four cylinders: two firings a turn
        phase += step
        if phase - cycle >= 1:  # the next firing: sometimes a misfire
            cycle = math.floor(phase)
            firing = 0.15 if rnd.random() < misfire else 1.0
        p = phase % 1.0
        brightness = 1.35 - 0.55 * throttle
        tone = (math.sin(tau * p) + math.sin(tau * 2 * p + 0.6) / 2 ** brightness
                + math.sin(tau * 3 * p + 1.1) / 3 ** brightness + math.sin(tau * 4 * p + 0.3) / 4 ** brightness
                + math.sin(tau * 6 * p + 2.0) / 6 ** brightness)
        tone += 0.3 * math.sin(math.pi * phase)  # half order: no two cylinders are quite the same
        pulse = math.exp(-5.5 * p)  # each firing is a kick that dies away
        roar += 0.12 * (rnd.uniform(-1, 1) - roar)
        x = firing * (tone * (0.45 + 0.55 * pulse) + (0.25 + 0.9 * throttle) * 1.6 * roar * pulse)
        if rpm < 420:  # the starter's whine under the cranking
            x = 0.5 * x + 0.25 * math.sin(tau * 95 * t) * (0.6 + 0.4 * math.sin(tau * 9 * t))
        if crackle and throttle < 0.2 and rpm > 1800 and rnd.random() < crackle / RATE:
            pops.append([rnd.uniform(0.6, 1.2), 0])  # a pop from the exhaust as the throttle shuts
        for pop in pops:
            x += pop[0] * math.exp(-pop[1] / (0.006 * RATE)) * rnd.uniform(-1, 1)
            pop[1] += 1
        pops = [pop for pop in pops if pop[1] < 0.03 * RATE]
        x = math.tanh(x * (0.9 + 0.8 * throttle))
        soft += 0.45 * (x - soft)  # a little softer, like through a can
        fade = min(1.0, t / 0.012, (seconds - t) / 0.09)
        out[i] = int(max(-1.0, min(1.0, soft * fade * volume)) * 32767)
    return out


def wav_bytes(name: str, volume: float = 0.42) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(render(name, volume).tobytes())
    return buf.getvalue()
