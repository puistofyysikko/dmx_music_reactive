"""Camera audio/video sync calibration: the laptop flashes its screen white and plays a click
at the same instant (once every 1.2 s). Film the screen with the camera while the click comes
out of the speaker nearby, then run

    python tools/latency_analyze.py clip.MP4 --lead 0 --skip 3 --min-lag -0.6 --max-lag 0.6

The measured "flash minus click" is the camera's audio/video offset plus the laptop's own
display delay (~15-30 ms) minus its audio output delay (wired: ~15-45 ms): a few tens of ms
at most, so anything near +250 ms is the camera's (or the video decoder's) alignment.

    python tools/av_sync_flash.py [--seconds 20] [--window]   (--window: small test window)
"""

import argparse
import subprocess
import threading
import time
import tkinter as tk

import numpy as np

SR = 48000
CHUNK = 0.005  # s: audio is written in 5 ms pieces so a click starts within 5 ms of the flash


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=20.0)
    ap.add_argument("--period", type=float, default=1.2)
    ap.add_argument("--window", action="store_true", help="small window instead of full screen")
    ap.add_argument("--sink", help="output sink (default: the default sink)")
    args = ap.parse_args()

    cmd = ["paplay", "--raw", "--format=s16le", f"--rate={SR}", "--channels=1",
           "--latency-msec=10"]
    if args.sink:
        cmd.append(f"--device={args.sink}")
    play = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    click = (int(0.8 * 32767) * np.sin(2 * np.pi * 1000 * np.arange(int(0.010 * SR)) / SR)
             ).astype("<i2").tobytes()
    silence = bytes(2 * int(SR * CHUNK))
    want_click = threading.Event()
    stop = threading.Event()
    log: list[float] = []

    def writer() -> None:
        t_next = time.monotonic()
        while not stop.is_set():
            now = time.monotonic()
            if now < t_next:
                time.sleep(t_next - now)
                continue
            t_next += CHUNK
            try:
                if want_click.is_set():
                    want_click.clear()
                    play.stdin.write(click)
                    t_next += 0.010 - CHUNK
                else:
                    play.stdin.write(silence)
                play.stdin.flush()
            except (BrokenPipeError, ValueError):
                return

    root = tk.Tk()
    root.configure(bg="black")
    if args.window:
        root.geometry("400x300+100+100")
    else:
        root.attributes("-fullscreen", True)
    root.title("A/V sync flash")
    t_end = time.monotonic() + args.seconds + 2.0

    def flash() -> None:
        if time.monotonic() > t_end:
            root.destroy()
            return
        root.configure(bg="white")
        root.update()
        want_click.set()  # the click starts within ~5 ms of the screen update
        log.append(time.monotonic())
        root.after(120, lambda: (root.configure(bg="black"), root.update()))
        root.after(int(args.period * 1000), flash)

    threading.Thread(target=writer, daemon=True).start()
    root.after(2000, flash)  # 2 s of black first so the camera can settle
    root.mainloop()
    stop.set()
    time.sleep(0.1)
    play.terminate()
    print(f"{len(log)} flash+click pairs sent")


if __name__ == "__main__":
    main()
