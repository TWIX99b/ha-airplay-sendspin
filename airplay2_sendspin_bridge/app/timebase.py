"""Stable time base for the audio coming out of shairport-sync.

shairport-sync (stdout backend) releases each AirPlay packet once its time
has come, but only checks every few milliseconds: a packet arrives 0–10 ms
late, never early. At the start of a session (and after a pause or a skip)
it writes the silent lead-in all at once.

The old approach took the arrival of the very first chunk as the anchor and
only corrected deviations above 60 ms – every session ended up with its own
random error of a few tens of milliseconds. That made calibration useless.

This time base instead follows the *earliest* arrivals: for chunk i the
residual r_i = arrival_i - i * CHUNK is the release time of the stream
plus the (always positive) delay. A low percentile of the residuals over a
sliding window is the release time without the delay. Chunks that arrive
back to back (lead-in burst, backlog) are not used for that.

- Until enough steady chunks have arrived the time base is not *settled*;
  the caller holds the audio back until then (it is the silent lead-in).
- Once settled it follows slow drift by at most DRIFT_US per chunk.
- A jump of more than JUMP_US (pause, skip, flush) starts it over.
"""

from __future__ import annotations

from collections import deque

CHUNK_US_DEFAULT = 20_000


class TimeBase:
    WINDOW = 150  # chunks (3 s) for the percentile
    MIN_SAMPLES = 25  # steady chunks (0.5 s) before the time base is settled
    PERCENTILE = 0.05
    BURST_US = 4_000  # arriving closer than this after the previous chunk
    JUMP_US = 250_000  # pause / skip / flush → start over
    RESETTLE_US = 30_000  # settled but this far off → jump to the estimate
    DEADBAND_US = 500  # follow only deviations beyond this
    DRIFT_US = 20  # max correction per chunk (= 1 ms per second)
    LOCK_US = 2_000  # within this the time base counts as locked

    def __init__(self, chunk_us: int = CHUNK_US_DEFAULT) -> None:
        self.chunk_us = chunk_us
        self.jumps = 0
        self.reset()

    def reset(self) -> None:
        self.base: float | None = None  # baseline arrival of chunk 0 (µs)
        self.index = 0  # index of the next chunk
        self.samples: deque[float] = deque(maxlen=self.WINDOW)
        self.last_arrival: int | None = None
        self.settled = False
        self.error_us = 0.0  # estimate − base (after the last correction)
        self._since_estimate = 0

    @property
    def locked(self) -> bool:
        return self.settled and abs(self.error_us) <= self.LOCK_US

    def _estimate(self) -> float:
        s = sorted(self.samples)
        return s[int(self.PERCENTILE * (len(s) - 1))]

    def time_of(self, index: int) -> float:
        """Baseline arrival (µs, monotonic) of chunk *index* of this line."""
        assert self.base is not None
        return self.base + index * self.chunk_us

    def add(self, arrival_us: int) -> tuple[int, bool]:
        """Feed the arrival of the next chunk.

        Returns (index of this chunk, started_over). started_over is True
        if this chunk begins a new time line (first chunk, or after a
        jump); indices of the old line are no longer valid. Ask time_of()
        for the play reference – it moves while the time base settles.
        """
        started_over = False
        if self.base is not None and self.settled:
            r = arrival_us - self.index * self.chunk_us
            if abs(r - self.base) > self.JUMP_US:
                self.jumps += 1
                self.reset()
        if self.base is None:
            self.base = float(arrival_us)
            started_over = True
        i = self.index
        r = arrival_us - i * self.chunk_us
        steady = (
            self.last_arrival is not None
            and arrival_us - self.last_arrival >= self.BURST_US
        )
        self.last_arrival = arrival_us
        if steady:
            self.samples.append(r)
        if len(self.samples) >= self.MIN_SAMPLES:
            self._since_estimate += 1
            if not self.settled or self._since_estimate >= 5:
                self._since_estimate = 0
                err = self._estimate() - self.base
                if not self.settled or abs(err) > self.RESETTLE_US:
                    self.base += err
                    self.settled = True
                    err = 0.0
                elif abs(err) > self.DEADBAND_US:
                    step = max(-self.DRIFT_US * 5, min(self.DRIFT_US * 5, err))
                    self.base += step
                    err -= step
                self.error_us = err
        self.index += 1
        return i, started_over
