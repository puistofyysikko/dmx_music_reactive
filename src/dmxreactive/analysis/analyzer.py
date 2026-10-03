"""Real-time music analyser: audio blocks in, `Features` out."""

from __future__ import annotations

from collections import deque

import numpy as np

from dmxreactive.analysis.dsp import AdaptiveNorm, OnsetDetector, PeakNorm
from dmxreactive.analysis.features import BAND_NAMES, Features
from dmxreactive.analysis.tempo import BeatState, TempoTracker
from dmxreactive.audio.level import rms_dbfs

FFT_SIZE = 2048
BAND_EDGES_HZ = ((20, 150), (150, 500), (500, 2000), (2000, 8000), (8000, 16000))
SILENCE_DB = -65.0
LOCK_WINDOW_MS = 60.0  # a kick within this (or 15 % of the beat period, if shorter) of a
#                         predicted beat counts as on the grid; chance level is then ~0.2-0.25
LOCK_ALPHA = 0.12  # coherence smoothing per kick (~ last 8 kicks)
LOCK_ON, LOCK_OFF = 0.5, 0.3
BASS_HOLD_S = 0.25  # bass_energy peak-hold, so it reads the last kick between kicks
FLUX_WEIGHTS = (1.5, 1.0, 0.7, 0.5, 0.0)  # how much each band feeds the tempo envelope


class Analyzer:
    def __init__(self, sample_rate: int = 48000, block_size: int = 1024) -> None:
        self.sample_rate = sample_rate
        self.block_size = block_size
        self.fr = sample_rate / block_size  # analysis frames per second
        self._window = np.hanning(FFT_SIZE).astype(np.float32)
        self._ring = np.zeros(FFT_SIZE, dtype=np.float32)
        freqs = np.fft.rfftfreq(FFT_SIZE, 1.0 / sample_rate)
        self._band_bins = [np.flatnonzero((freqs >= lo) & (freqs < hi)) for lo, hi in BAND_EDGES_HZ]
        self._freqs = freqs
        self._log_freqs = np.log(np.maximum(freqs, 1.0))
        self._prev_logmag: np.ndarray | None = None
        self._level_norm = AdaptiveNorm(self.fr, range_db=24.0, decay_db_per_s=1.0)
        self._band_norms = [AdaptiveNorm(self.fr, range_db=30.0, min_ceiling_db=-90.0)
                             for _ in BAND_NAMES]
        self._flux_norms = [PeakNorm(self.fr) for _ in BAND_NAMES]
        self._kick = OnsetDetector(self.fr, min_level=0.3)
        self._hat = OnsetDetector(self.fr, min_level=0.3, refractory_s=0.08)
        self._tempo = TempoTracker(self.fr)
        self._env_hist = np.zeros(max(4, round(0.4 * self.fr)))
        self._t = 0.0
        self._kick_count = self._hat_count = self._beat_count = 0
        self._silent_frames = 0
        self._level_fast = self._level_slow = 0.0
        # slow ceiling (-0.25 dB/s): a 10 s breakdown must stay 'quiet', not re-normalise to loud
        self._bass_slow = AdaptiveNorm(self.fr, range_db=24.0, decay_db_per_s=0.25,
                                       min_ceiling_db=-90.0)
        self._bass_hold = 0.0
        self._bass_db_hist: deque[float] = deque(maxlen=max(4, round(0.4 * self.fr)))
        self._last_kick_rise = 0.0
        self._last_kick_strength = 0.0
        self._coherence = 0.0  # share of recent kicks that fell on the beat grid
        self._locked = False

    def process(self, block: np.ndarray) -> Features:
        n = len(block)
        self._ring = np.roll(self._ring, -n)
        self._ring[-n:] = block
        self._t += n / self.sample_rate

        level_db = rms_dbfs(block)
        silent_now = level_db < SILENCE_DB
        self._silent_frames = self._silent_frames + 1 if silent_now else 0
        silent = self._silent_frames > self.fr  # >1 s of near-silence

        mag = np.abs(np.fft.rfft(self._ring * self._window)) / (FFT_SIZE / 4)
        logmag = np.log1p(mag * 50.0)
        if self._prev_logmag is None:
            self._prev_logmag = logmag
        diff = np.maximum(logmag - self._prev_logmag, 0.0)
        self._prev_logmag = logmag

        bands, band_db, fluxes = [], [], []
        for i, bins in enumerate(self._band_bins):
            power = float(np.mean(mag[bins] ** 2)) + 1e-12
            band_db.append(10 * np.log10(power))
            bands.append(self._band_norms[i](band_db[-1]))
            fluxes.append(self._flux_norms[i](float(diff[bins].sum())))
        fluxes_arr = np.array(fluxes)

        # tempo envelope: weighted band flux, minus a short local mean to emphasise peaks
        raw = float(np.dot(fluxes_arr, FLUX_WEIGHTS) / sum(FLUX_WEIGHTS))
        self._env_hist = np.roll(self._env_hist, -1)
        self._env_hist[-1] = raw
        env = max(0.0, raw - float(self._env_hist.mean()))
        if silent:
            self._tempo.reset()
            beat_state = BeatState(0.0, 0.0, False, 0.0)
        else:
            beat_state = self._tempo.update(0.0 if silent_now else env)

        # Always feed the detectors (their thresholds adapt to the quiet gaps too); only the
        # result is gated by silence.
        kick = self._kick(float(fluxes_arr[0])) and not silent_now
        hat = self._hat(float(fluxes_arr[3])) and not silent_now
        # Beat-lock: do kicks land on the predicted grid? (a ballad or free rhythm: no)
        if kick and beat_state.bpm > 0:
            period_ms = 60000.0 / beat_state.bpm
            miss_ms = min(beat_state.phase, 1.0 - beat_state.phase) * period_ms
            window_ms = min(LOCK_WINDOW_MS, 0.15 * period_ms)
            self._coherence += LOCK_ALPHA * ((miss_ms < window_ms) - self._coherence)
        if beat_state.bpm == 0:
            self._coherence, self._locked = 0.0, False
        elif self._coherence >= LOCK_ON:
            self._locked = True
        elif self._coherence < LOCK_OFF:
            self._locked = False
        self._bass_hold = max(self._bass_slow(band_db[0]),
                              self._bass_hold * float(np.exp(-1.0 / (BASS_HOLD_S * self.fr))))
        if kick:
            self._last_kick_strength = float(fluxes_arr[0])
            self._last_kick_rise = band_db[0] - min(self._bass_db_hist, default=band_db[0])
        self._bass_db_hist.append(band_db[0])
        self._kick_count += kick
        self._hat_count += hat
        self._beat_count += beat_state.beat

        level = self._level_norm(level_db)
        self._level_fast += (level - self._level_fast) * (1 - np.exp(-1 / (2.0 * self.fr)))
        self._level_slow += (level - self._level_slow) * (1 - np.exp(-1 / (10.0 * self.fr)))
        total = float(mag.sum()) + 1e-12
        centroid = float(np.dot(mag, self._freqs)) / total
        centroid_n = float(np.clip(np.log(max(centroid, 100.0) / 100.0) / np.log(100.0), 0, 1))

        return Features(
            t=self._t,
            silent=silent,
            level_db=level_db,
            level=level,
            bands=tuple(bands),
            band_db=tuple(band_db),
            centroid=centroid_n,
            flux=min(1.0, raw),
            kick=bool(kick),
            kick_strength=float(fluxes_arr[0]) if kick else 0.0,
            kick_count=self._kick_count,
            last_kick_strength=self._last_kick_strength,
            last_kick_rise_db=self._last_kick_rise,
            bass_energy=self._bass_hold,
            hat=bool(hat),
            hat_count=self._hat_count,
            bpm=beat_state.bpm,
            beat_phase=beat_state.phase,
            beat=beat_state.beat,
            beat_count=self._beat_count,
            beat_confidence=beat_state.confidence,
            beat_locked=self._locked,
            energy_trend=float(np.clip((self._level_fast - self._level_slow) * 4.0, -1.0, 1.0)),
        )
