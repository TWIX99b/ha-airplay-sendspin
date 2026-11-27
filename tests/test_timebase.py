"""Time base against a simulated shairport-sync (stdout backend).

Run: python tests/test_timebase.py (from the add-on directory or the repo).
"""

import random
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "airplay2_sendspin_bridge" / "app"))

from timebase import TimeBase  # noqa: E402

CHUNK = 20_000  # µs
PACKET = 352 / 44_100 * 1e6  # AirPlay packet, µs


def simulate(seed: int, seconds: float = 40.0, drift_ppm: float = 80.0,
             burst_s: float = 1.0, stalls: bool = True, flush_at: float | None = None):
    """Arrival times (µs) of 20 ms chunks plus the true release time of each.

    The true release time of chunk i is when its last frame is due; the
    stdout backend releases a packet 0–10 ms after it is due.
    """
    rng = random.Random(seed)
    t_session = 1_000_000 + rng.uniform(0, 50_000)
    first_due = t_session + rng.uniform(5_000, 300_000)  # first real packet
    n_burst = int(burst_s * 1e6 / CHUNK)
    rate = 1 + drift_ppm / 1e6
    arrivals, truth = [], []
    stall_until = 0.0
    line_start = first_due - n_burst * CHUNK  # chunk 0 of this line
    i = 0
    t_flush_done = None
    while True:
        due = line_start + (i + 1) * CHUNK * rate
        if due - first_due > seconds * 1e6:
            break
        if flush_at is not None and t_flush_done is None and due - first_due > flush_at * 1e6:
            # flush: 0.6 s nothing, then a new lead-in burst and a new line
            t_flush_done = due + 600_000
            first_due = t_flush_done + rng.uniform(5_000, 300_000)
            line_start = first_due - n_burst * CHUNK
            for k in range(n_burst):
                arrivals.append(t_flush_done + k * 30)
                truth.append(line_start + (k + 1) * CHUNK * rate)
            i = n_burst
            continue
        if i < n_burst and t_flush_done is None:
            arr = t_session + i * 30  # lead-in: all at once
        else:
            # the packet completing this chunk is released 0–10 ms late
            last_packet_due = (int((due - line_start) / PACKET) + 1) * PACKET + line_start
            arr = last_packet_due + rng.uniform(0, 10_000)
        if stalls and rng.random() < 0.004:
            stall_until = arr + rng.uniform(20_000, 80_000)
        if arr < stall_until:
            arr = stall_until + rng.uniform(0, 200)
        if arrivals and arr < arrivals[-1]:
            arr = arrivals[-1] + 10
        arrivals.append(arr)
        truth.append(due)
        i += 1
    return arrivals, truth


def run(arrivals, truth):
    tb = TimeBase(CHUNK)
    lines = []  # per line: list of (index, truth)
    for arr, tru in zip(arrivals, truth):
        idx, new = tb.add(int(arr))
        if new:
            lines.append([])
        sent = tb.time_of(idx) if tb.settled else None  # as used when sending
        lines[-1].append((idx, tru, sent))
    return tb, lines


def errors_after_settle(tb, lines, skip_s=3.0):
    """time_of(i) − truth for the last line, after it has settled."""
    errs = []
    line = lines[-1]
    for idx, tru, sent in line:
        if sent is not None and idx * CHUNK > skip_s * 1e6 + line[0][0] * CHUNK:
            errs.append(sent - tru)
    return errs


def test_sessions_agree():
    """The point of it all: every session lands on the same timing (±2 ms)."""
    offsets = []
    for seed in range(30):
        arr, tru = simulate(seed)
        tb, lines = run(arr, tru)
        assert len(lines) == 1, f"seed {seed}: unexpected restart"
        assert tb.settled and tb.locked
        # only the last 10 s: drift is followed
        errs = errors_after_settle(tb, lines, skip_s=30)
        offsets.append(statistics.median(errs))
        assert max(errs) - min(errs) < 3_000, f"seed {seed}: wobbles {max(errs) - min(errs):.0f} µs"
    spread = max(offsets) - min(offsets)
    print(f"  session offsets: {min(offsets) / 1000:+.2f} … {max(offsets) / 1000:+.2f} ms (spread {spread / 1000:.2f} ms)")
    assert spread < 2_000, spread


def test_old_method_was_random():
    """For comparison: anchoring on the first chunk differs per session."""
    offsets = []
    for seed in range(30):
        arr, tru = simulate(seed)
        offsets.append(arr[0] - tru[0])
    assert max(offsets) - min(offsets) > 50_000


def test_settles_quickly():
    arr, tru = simulate(1)
    tb = TimeBase(CHUNK)
    for k, a in enumerate(arr):
        tb.add(int(a))
        if tb.settled:
            break
    burst = 50
    assert k < burst + TimeBase.MIN_SAMPLES + 10, k


def test_flush_starts_over_and_settles_again():
    arr, tru = simulate(7, seconds=50, flush_at=20)
    tb, lines = run(arr, tru)
    assert len(lines) == 2, len(lines)
    assert tb.jumps == 1
    errs = errors_after_settle(tb, lines, skip_s=5)
    assert max(abs(e - statistics.median(errs)) for e in errs) < 3_000
    ref = statistics.median(errors_after_settle(*run(*simulate(8)), skip_s=30))
    assert abs(statistics.median(errs) - ref) < 2_000


def test_drift_followed():
    for ppm in (-300, 300):
        arr, tru = simulate(3, seconds=60, drift_ppm=ppm)
        tb, lines = run(arr, tru)
        errs = errors_after_settle(tb, lines, skip_s=10)
        assert max(errs) - min(errs) < 3_500, (ppm, max(errs) - min(errs))


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
    print("ALL OK")
