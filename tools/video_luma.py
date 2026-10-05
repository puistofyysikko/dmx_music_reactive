"""Per-frame mean brightness of a video, decoded with PyAV (handles HEVC from GoPro/phones,
which this machine's ffmpeg cannot). Results are cached next to /tmp-style scratch if requested.

    from video_luma import luma_series
    t, y = luma_series("clip.mp4", roi=None)   # roi = (w, h, x, y) in source pixels
"""

from __future__ import annotations

import numpy as np


def luma_series(path: str, roi: tuple[int, int, int, int] | None = None,
                small: int = 160) -> tuple[np.ndarray, np.ndarray]:
    import av

    container = av.open(path)
    stream = container.streams.video[0]
    stream.thread_type = "AUTO"
    times, values = [], []
    for frame in container.decode(stream):
        if roi:
            gray = frame.to_ndarray(format="gray")
            w, h, x, y = roi
            values.append(float(gray[y:y + h, x:x + w].mean()))
        else:
            small_frame = frame.reformat(width=small, height=small * 9 // 16, format="gray")
            values.append(float(small_frame.to_ndarray().mean()))
        times.append(float(frame.pts * stream.time_base))
    container.close()
    return np.array(times), np.array(values)
