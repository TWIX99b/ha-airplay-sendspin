"""Check of the calibration analysis (Python port of the JS in app/static/index.html).

Builds synthetic microphone recordings (click track, room reflections, reverb,
noise, jitter, muted phase) with known offsets and checks that the analysis
finds them again.

    python3 tests/test_calibration.py      # needs numpy
"""

import numpy as np, random, sys
import pathlib

sys.path.insert(
    0,
    str(
        pathlib.Path(__file__).resolve().parents[1] / "airplay2_sendspin_bridge" / "app"
    ),
)
import types

_a = types.ModuleType("aiohttp")
_a.web = types.ModuleType("aiohttp.web")
_a.web.middleware = lambda f: f
sys.modules["aiohttp"] = _a
sys.modules["aiohttp.web"] = _a.web
import web  # uses build_click_wav for the real click waveform
import io, wave

BINS = 1000
GUARD_S = 2.5
PHASE_S = 9.0


def envelope(x, sr):
    d = np.abs(np.diff(x, prepend=0.0))
    w = max(1, round(sr * 0.0005))
    c = np.cumsum(d)
    out = c.copy()
    out[w:] = c[w:] - c[:-w]
    return out / w


def fold(env, sr, a, b):
    idx = np.arange(a, b)
    k = np.floor(((idx % sr) / sr) * BINS).astype(int)
    p = np.bincount(k, weights=env[a:b], minlength=BINS)
    c = np.bincount(k, minlength=BINS)
    p = np.where(c > 0, p / np.maximum(c, 1), 0)
    return sum(np.roll(p, j) for j in range(-2, 3)) / 5


ONSET_WIN = 40  # ms before the maximum: direct sound comes before reflections
ONSET_REL = 0.3


def onset(P):
    m = int(np.argmax(P))
    base = float(np.median(P))
    thr = base + ONSET_REL * (P[m] - base)
    for j in range(m - ONSET_WIN, m + 1):
        if P[j % BINS] >= thr:
            return j % BINS
    return m


def analyse(x, sr, markA, markB, end):
    env = envelope(x, sr)
    A = fold(env, sr, markA + round(GUARD_S * sr), markB)
    B = fold(env, sr, markB + round(GUARD_S * sr), end)
    D = B - A
    s, e = onset(A), onset(D)
    qa = A.max() / (np.median(A) + 1e-12)
    qd = D.max() / (np.median(np.abs(D)) + 1e-12)
    d = (e - s) * 1000 / BINS
    if d > 500:
        d -= 1000
    if d <= -500:
        d += 1000
    return round(d), qa, qd


# real click waveform from the add-on
with wave.open(io.BytesIO(web.build_click_wav())) as w:
    src = np.frombuffer(w.readframes(48000), dtype="<i2").astype(float) / 32768
click = src[: int(48000 * 0.004)]


def room(click, sr, rng):
    """Click + early reflections + reverb tail."""
    ir = np.zeros(int(sr * 0.25))
    ir[0] = 1
    for _ in range(8):
        ir[rng.integers(int(sr * 0.002), int(sr * 0.03))] += rng.uniform(
            0.2, 0.5
        ) * rng.choice([-1, 1])
    tail = rng.normal(0, 1, len(ir)) * np.exp(-np.arange(len(ir)) / (sr * 0.06)) * 0.05
    return np.convolve(click, ir + tail)


def simulate(
    delay_ms,
    sr=48000,
    echo_gain=1.0,
    ref_gain=1.0,
    noise=0.01,
    bridge_lag_s=1.6,
    seed=0,
):
    rng = np.random.default_rng(seed)
    total = int(sr * (2 * PHASE_S + 0.5))
    x = rng.normal(0, noise, total)
    ref_click = room(click, sr, rng) * ref_gain
    echo_click = room(click, sr, rng) * echo_gain
    phase0 = 0.3137  # arbitrary click phase within the recording
    markA = int(sr * 0.2)
    markB = markA + int(sr * PHASE_S)
    end = markB + int(sr * PHASE_S)
    mute_from = markA + int(bridge_lag_s * sr)
    mute_to = markB + int(bridge_lag_s * sr)
    n = 0
    while True:
        t = phase0 + n
        i_ref = int(t * sr + rng.normal(0, sr * 0.0003))  # ±0.3 ms jitter
        if i_ref + len(ref_click) >= total:
            break
        x[i_ref : i_ref + len(ref_click)] += ref_click
        i_echo = int((t + delay_ms / 1000) * sr + rng.normal(0, sr * 0.0005))
        if 0 <= i_echo < total - len(echo_click) and not (
            mute_from <= i_echo < mute_to
        ):
            x[i_echo : i_echo + len(echo_click)] += echo_click
        n += 1
    return x, markA, markB, end


ok = True
cases = [
    (-120, 1, 1),
    (37, 1, 1),
    (-5, 1, 1),
    (0, 1, 1),
    (250, 0.5, 1),
    (-300, 1, 0.4),
    (80, 0.3, 1),
    (12, 1, 1),
]
for delay, eg, rg in cases:
    for sr in (48000, 44100):
        x, a, b, e = simulate(delay, sr=sr, echo_gain=eg, ref_gain=rg, seed=delay + sr)
        d, qa, qd = analyse(x, sr, a, b, e)
        good = abs(d - delay) <= 2 or (abs(delay) <= 3 and abs(d) <= 3)
        ok &= good
        print(
            f"true {delay:+5d} ms  sr {sr}  echo×{eg} ref×{rg} → measured {d:+5d} ms  qa {qa:6.1f} qd {qd:6.1f}  {'OK' if good else 'FAIL'}"
        )
# noisy case → quality must be low or still right
x, a, b, e = simulate(60, noise=0.2, seed=5)
d, qa, qd = analyse(x, 48000, a, b, e)
print(f"heavy noise: measured {d:+d} ms qa {qa:.1f} qd {qd:.1f}")
print("ALL OK" if ok else "FAILURES")

# random robustness test
rng = random.Random(42)
fails = 0
worst = 0
for i in range(60):
    delay = rng.randint(-450, 450)
    eg = rng.uniform(0.3, 1.2)
    rg = rng.uniform(0.3, 1.2)
    sr = rng.choice([44100, 48000])
    lag = rng.uniform(1.0, 2.3)
    x, a, b, e = simulate(
        delay,
        sr=sr,
        echo_gain=eg,
        ref_gain=rg,
        bridge_lag_s=lag,
        noise=rng.uniform(0.003, 0.03),
        seed=i,
    )
    d, qa, qd = analyse(x, sr, a, b, e)
    err = abs(d - delay)
    worst = max(worst, err)
    trusted = qa > 4 and qd > 8
    if err > 3 and trusted:
        fails += 1
        print("WRONG BUT TRUSTED", delay, d, qa, qd)
print(f"random: worst error {worst} ms, trusted-but-wrong {fails}/60")
sys.exit(0 if ok and fails == 0 else 1)
