import shutil
import subprocess
import sys
import time

import numpy as np
import pytest

from dmxreactive.audio.array_source import ArraySource, FileSource, click_track, sine
from dmxreactive.audio.level import FLOOR_DB, peak_dbfs, rms_dbfs
from dmxreactive.audio.pulse import PulseMonitorSource

BLOCK = 256


def fake_capture(blocks: int, value: float = 0.5, then_sleep: float = 0.0) -> list[str]:
    """A stand-in for parec: writes `blocks` blocks of constant samples to stdout."""
    code = (
        "import sys,time,numpy as np;"
        f"b=np.full({BLOCK},{value},dtype=np.float32).tobytes();"
        f"[sys.stdout.buffer.write(b) for _ in range({blocks})];sys.stdout.buffer.flush();"
        f"time.sleep({then_sleep})"
    )
    return [sys.executable, "-c", code]


def test_levels():
    assert rms_dbfs(np.full(100, 0.5, dtype=np.float32)) == pytest.approx(-6.02, abs=0.01)
    assert peak_dbfs(np.array([0.1, -0.5, 0.2], dtype=np.float32)) == pytest.approx(-6.02, abs=0.01)
    assert rms_dbfs(np.zeros(10, dtype=np.float32)) == FLOOR_DB
    sine_block = sine(1000, 0.1)
    assert rms_dbfs(sine_block) == pytest.approx(-9.03, abs=0.05)  # 0.5 amplitude sine


def test_array_source_blocks_and_eof():
    src = ArraySource(np.arange(1000, dtype=np.float32), 48000, 300, realtime=False)
    src.start()
    blocks = [src.read() for _ in range(3)]
    assert [len(b) for b in blocks] == [300, 300, 300]
    assert blocks[1][0] == 300
    with pytest.raises(EOFError):
        src.read()  # 100 samples left is less than a full block


def test_array_source_realtime_pacing():
    src = ArraySource(np.zeros(48000, dtype=np.float32), 48000, 4800, realtime=True)
    src.start()
    t0 = time.perf_counter()
    for _ in range(3):
        src.read()
    assert 0.28 < time.perf_counter() - t0 < 0.4  # three 100 ms blocks


def test_array_source_timeout_returns_none():
    src = ArraySource(np.zeros(48000 * 10, dtype=np.float32), 48000, 48000 * 5, realtime=True)
    src.start()
    assert src.read(timeout=0.05) is None


def test_click_track_has_one_click_per_beat():
    sr, bpm = 48000, 120
    x = click_track(bpm, 4.0, sr)
    env = np.abs(x)
    onsets = np.flatnonzero((env[1:] > 0.5) & (env[:-1] <= 0.5))
    assert abs(len(onsets) - 8) <= 1  # 4 s at 120 BPM = 8 beats
    gaps = np.diff(onsets) / sr
    assert np.allclose(gaps, 0.5, atol=0.01)


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg missing")
def test_file_source_decodes_and_resamples(tmp_path):
    wav = tmp_path / "t.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
                    "-ar", "44100", "-ac", "2", str(wav)], check=True)
    src = FileSource(str(wav), sample_rate=48000, block_size=1024, realtime=False)
    src.start()
    n = 0
    while True:
        try:
            block = src.read()
        except EOFError:
            break
        n += 1
        assert block.dtype == np.float32 and len(block) == 1024
    assert 45 <= n <= 47  # 1 s at 48 kHz / 1024


def test_pulse_source_reads_exact_blocks_from_fake_capture():
    src = PulseMonitorSource(block_size=BLOCK, command=fake_capture(5, 0.5, then_sleep=5))
    src.start()
    try:
        blocks = [src.read(3) for _ in range(5)]
    finally:
        src.stop()
    assert all(b is not None and b.shape == (BLOCK,) and b.dtype == np.float32 for b in blocks)
    assert np.allclose(blocks[0], 0.5)
    assert src.stats.blocks >= 5 and src.stats.dropped == 0


def test_pulse_source_restarts_when_capture_dies():
    src = PulseMonitorSource(block_size=BLOCK, command=fake_capture(2, 0.25, then_sleep=0))
    src.start()
    try:
        got = [src.read(3) for _ in range(6)]  # needs at least two process generations
    finally:
        src.stop()
    assert all(b is not None for b in got)
    assert src.stats.restarts >= 1


def test_pulse_source_drops_oldest_when_consumer_is_slow():
    src = PulseMonitorSource(block_size=BLOCK, queue_blocks=2,
                             command=fake_capture(20, 0.1, then_sleep=5))
    src.start()
    time.sleep(0.5)
    try:
        assert src.stats.dropped >= 10
        assert src._queue.qsize() <= 2
    finally:
        src.stop()


def test_pulse_source_survives_missing_capture_binary():
    src = PulseMonitorSource(block_size=BLOCK, command=["/nonexistent/parec"])
    src.start()
    try:
        assert src.read(0.3) is None  # no crash, just no audio
    finally:
        src.stop()


@pytest.mark.skipif(shutil.which("pactl") is None or shutil.which("parec") is None
                    or shutil.which("paplay") is None, reason="PulseAudio/PipeWire tools missing")
def test_live_capture_from_virtual_sink(tmp_path):
    """Silent end-to-end: play a tone into a null sink and capture its monitor."""
    try:
        mod = subprocess.run(
            ["pactl", "load-module", "module-null-sink", "sink_name=dmxreactive_test"],
            capture_output=True, text=True, check=True).stdout.strip()
    except (subprocess.CalledProcessError, OSError):
        pytest.skip("cannot create a virtual sink (no sound server?)")
    try:
        wav = tmp_path / "tone.wav"
        subprocess.run(  # ffmpeg's sine is 0.125 amplitude by default; volume=2 makes it 0.25
            ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
             "-af", "volume=2", str(wav)], check=True)
        src = PulseMonitorSource("dmxreactive_test.monitor", 48000, 1024)
        src.start()
        player = subprocess.Popen(["paplay", "--device=dmxreactive_test", str(wav)])
        try:
            levels = [rms_dbfs(b) for b in (src.read(2) for _ in range(60)) if b is not None]
        finally:
            player.wait(timeout=10)
            src.stop()
        assert len(levels) > 40
        # 0.25-amplitude sine -> RMS -15 dBFS; the loud middle of the capture must show it
        assert max(levels) == pytest.approx(-15.05, abs=1.0)
        assert min(levels) < -60  # silence before/after playback
    finally:
        subprocess.run(["pactl", "unload-module", mod], check=False)
