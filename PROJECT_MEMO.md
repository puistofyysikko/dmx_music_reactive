# DMX Music-Reactive Lighting — Project Memo (v2)

Status: **v0.1.0 merged into `main`; hardware-verified; waiting for the wall lights** · Last updated 2026-10-05 · Supersedes: `dmx_music_reactive_project_plan.md` (kept as the original draft)

---

## 1. Goal and success criteria

A PC listens to whatever audio it is playing, analyses it in real time, and drives DMX fixtures through an Art-Net node so the lights follow the music. Targeted at small events (e.g. a Christmas party).

Measurable "done" criteria:

| # | Criterion | Target |
|---|-----------|--------|
| 1 | Audio → light latency (glass-to-glass, measured with phone slow-mo) | < 100 ms, stretch < 60 ms |
| 2 | Beat hit-rate on 4/4 dance/pop tracks (strobe fires within ±50 ms of the perceived beat) | > 90 % |
| 3 | Art-Net output | steady 30 Hz (jitter < 5 ms), keep-alive when idle |
| 4 | Runs on Fedora Linux (PipeWire) | Linux-only scope; audio backend still behind an `AudioSource` interface |
| 5 | Survives 3 h unattended | no crashes, no memory growth, recovers if audio device or network drops |
| 6 | Can be developed and tested with **no hardware** | virtual fixtures + WAV-file input |
| 7 | Safe | strobe rate capped, blackout hotkey, safe defaults on start/stop/crash |

Non-goals for v1: DJ-software integration, video/pixel mapping, multi-universe, GUI beyond a minimal CLI/TUI.

---

## 2. Corrections and decisions vs. the original plan

These are the points where the first draft needs changing before we write code.

| Topic | Original plan | Decision | Why |
|-------|---------------|----------|-----|
| Beat detection | `librosa` | **Custom real-time onset/beat tracker** (spectral flux on bass band + adaptive threshold + tempo-phase tracker). Optional: `aubio` as a comparison baseline. | `librosa.beat` is an offline/batch algorithm; it needs seconds of look-ahead and is slow for streaming. Use librosa only in offline tests/tuning. |
| Audio capture | `sounddevice` + WASAPI/PulseAudio | **Linux only.** A `parec` subprocess reading the PipeWire/PulseAudio *monitor* source of the default sink (no PortAudio / Python audio wheel needed), behind an `AudioSource` interface with file and synthetic sources. | Windows dropped. `sounddevice` would need the x86_64 PortAudio system library; `parec` is already installed, follows the sound server's downmix/resample, and is trivial to restart. Decision changed in Phase 3 (2026-10-02). |
| Linux audio server | PulseAudio | Support **PulseAudio and PipeWire** (pipewire-pulse exposes the same `*.monitor` sources). | Current distros (incl. this dev machine, Fedora) ship PipeWire. |
| RMS | `numpy.linalg.norm` | `sqrt(mean(x**2))` on float32 frames, converted to dBFS, then normalised with an adaptive (AGC-style) range. | Raw norm scales with frame size; fixed thresholds break with volume changes. Lights must react to *relative* dynamics, not master volume. |
| DMX rate | 20–30 Hz | **30 Hz default, configurable up to 40 Hz**; always re-send at least 1 Hz as keep-alive. | DMX512 max ≈ 44 Hz for a full universe; many nodes/fixtures time out without refresh. Analysis runs faster than output (see §4). |
| Art-Net lib | custom UDP / python-artnet / OLA | **Custom minimal ArtDmx sender** (~40 lines, `socket`), unicast to node IP. | Zero dependencies, identical on both OSes, trivially portable to C++ later. OLA is Linux-centric; python-artnet is poorly maintained. |
| Fixture "wiring" | moving head first in chain | Order in the chain is irrelevant for DMX addressing; only **start addresses** and the **terminator on the last device** matter. | Avoid confusion when re-patching. |
| Cabling | any "DMX cable" | Use real **110 Ω DMX cable**, not microphone XLR. Check **3-pin vs 5-pin** connectors on node and fixtures (adapters if mixed). | Mic cable causes reflections/flicker at length; connector mismatch is the #1 day-of-event surprise. |
| C++ | optional DSP module | **Defer.** Python + numpy easily handles 44.1 kHz FFT at ~100 Hz. Revisit only if profiling shows >30 % of one core or missed deadlines. | Avoid premature complexity. Keep the Art-Net packet format and module boundaries clean so porting is possible. |
| Safety | not covered | Add strobe-rate limiter, blackout, safe-state handling (§9). | Photosensitivity risk with guests; also avoids stuck-on fixtures after a crash. |

---

## 3. Hardware

**Confirmed scope (2026-10-02): Linux only (Fedora KDE Plasma). Windows is dropped.**

### 3.1 Bill of materials (v1)

| Item | Model | Qty | Notes |
|------|-------|-----|-------|
| Control PC | HP EliteBook x360 830 G8, Fedora KDE (PipeWire) | 1 | No RJ45 port → USB-Ethernet dongle (also the music player; see §3.5) |
| Art-Net node | Eurolite **DXT PoE Art-Net Node I** (1 universe, 512 ch) | 1 | Powered by **PoE (802.3af, 48 V) or 5 V USB-C** – see §3.3 |
| Moving head | Fun Generation **PicoSpot 20 LED** (12 W white LED, 540° pan / 230° tilt, 35 W, 3-pin XLR) | 1 | 5/9/11-ch modes; 8 colours + 8 gobos |
| Strobe | Fun Generation **LED Pot Strobe 100** (126 white SMD LEDs, 38 W, 3-pin XLR) | 1 | 1/3-ch modes |
| Wall wash | Eurolite **LED PIX-72 RGB Bar** (72 SMD 5050 RGB, 4 segments of 18 LEDs, 15 W, 3-pin XLR, 120°) | 1 | Added 2026-10-14. Display mode `12C2`: 4 independent RGB segments, no dimmer/strobe channels (we dim in software) |
| Beam effect | Varytec **LED Derby ST** (4 × 3 W RGBW + 16 white LEDs, rotating beams, 3-pin XLR) | 1 | Added 2026-10-14. Display mode `04Ch`; **no dimmer**: colour slots only, on/off |
| Fog machine | Stairville **AF-40** | 1 | Standalone, manual operation, **not DMX-controlled** (documentation only, see the event checklist) |
| DMX cable 10 m + 5 m | 110 Ω DMX cable, 3-pin XLR | 1+1 | Not microphone cable |
| 120 Ω terminator | XLR 3-pin | 1 | On the last fixture's DMX OUT |
| Ethernet cable | Cat5e+ | 1 | Dongle ↔ node (direct, or via PoE injector/switch) |
| Spare | 3↔5-pin adapters, gender changers, gaffer tape, switched power strip | — | Event-day insurance |

Sources for the specs above: the Thomann/Fun Generation manuals for both fixtures (read directly from the PDFs) and public listings of the Eurolite node; the node's own manual still has to be checked on the device (§3.6).

### 3.2 Wiring

```
Laptop (USB-C/USB-A) ─► USB-Ethernet dongle ─► [PoE injector/switch, optional] ─► Eurolite DXT PoE Node I
                                                                                      │ DMX OUT (XLR)
                                                  10 m DMX cable ─► PicoSpot 20 (DMX IN → OUT)
                                                  5 m DMX cable  ─► LED Pot Strobe 100 (DMX IN) ─ DMX OUT ─► 120 Ω terminator
```

Chain order is irrelevant to addressing; only start addresses matter. Terminator goes on the **last** device. Both fixtures have 3-pin XLR in/out. The node's DMX connector is **3-pin** (confirmed by user), so no adapters are needed.

### 3.3 Power and network setup

**PoE:** The node needs power from one of:
1. a 802.3af PoE injector or PoE switch (48 V), or
2. the **5 V USB-C port** on the node – the laptop's USB-A port or any phone charger can power it, **no injector needed**. This is the simplest option for the hobby setup.
A normal USB-Ethernet dongle does **not** supply PoE. Do not connect a 48 V injector to a laptop port.

**IP configuration:**
- Node factory IP is **`192.168.1.20`** (confirmed from the sticker on the node's underside; subnet mask assumed `255.255.255.0`). Configuration is via a built-in web page at the node's IP, which also sets IP, Art-Net Net/SubNet/Universe, names and DMX refresh rate 20/30/40 Hz).
- On Fedora/NetworkManager: create a **dedicated static profile for the dongle**, e.g. `192.168.1.10/24`, no gateway, "never use this connection for default route", so Wi-Fi/internet keeps working:
  `nmcli con add type ethernet ifname <dongle-if> con-name dmx ip4 192.168.1.10/24 ipv4.never-default yes`
- Send Art-Net by **unicast** to the node IP, UDP 6454. No firewall change is needed for sending; if we add discovery (ArtPoll replies come back to UDP 6454), open `6454/udp` in `firewalld` for that zone only.
- The dongle interface name can change per port; use a udev/NetworkManager match on the dongle's MAC.
- **Switching dongle use (internet vs DMX):** `scripts/net_profile.sh dmx|internet|status`. Two stored profiles on the same dongle; `internet` (DHCP) autoconnects by default on plug-in, `dmx` (static, no autoconnect) is switched on by hand before preflight/show, and back with `internet` afterwards. Only one is active at a time.

### 3.4 Fixture facts that shape the design (from the manuals)

- **PicoSpot 20** has a settable "DMX break" behaviour (what it does if DMX stops: hold / auto-run / sound / home). Set it to **Hold** or **home**, never auto-run, so a crash doesn't leave surprise behaviour; we will additionally send our own safe frame.
- Max start address: 504 (5-ch), 508 (9-ch), 502 (11-ch). **LED Pot Strobe 100**: 512 (1-ch), 510 (3-ch).
- PicoSpot colour wheel has **8 colours + half-way "split" values + rotation**; hard-switching colours is mechanical, so limit changes (≤ ~1–2 Hz).
- Its built-in strobe is **1–25 Hz**; the Pot Strobe's frequency range is given only as a percentage (2–255) – real Hz must be measured (§6).
- The PicoSpot is a **single white LED**: colour comes from the wheel, not RGB mixing. Colour effects are therefore discrete and slow; do not design "smooth RGB fades" for it. (A future LED PAR would provide that.)
- The Pot Strobe is **white only**: only dimmer and flash rate. Its channel 2 (programme speed / sound sensitivity) must stay at 0 so it never leaves DMX control.

### 3.5 Audio setup (laptop as music source)

- Music (YouTube in browser, karaoke player, local files) plays on the laptop → output to the speakers/mixer (3.5 mm jack, or USB-C audio).
- We capture the **PipeWire/PulseAudio monitor of the output sink** (everything the laptop plays), so any app works with no per-app setup, and it is **independent of the physical output** (speaker, headphones, USB DAC, mixer).
- Consequence: **microphones plugged into the mixer are not seen by the laptop**. For karaoke the lights follow the backing track only (this is good – singing doesn't trigger strobes). If we ever want the mix, we'd need a line-out of the mixer into a USB audio interface (optional later).
- Volume knobs on the laptop do not change the monitor level when using PipeWire monitor (it taps pre-volume or post-volume depending on the sink; check in Phase 3). The adaptive gain in §4.4 makes this irrelevant either way.
- Karaoke player: you couldn't recall the name; likely candidates are Performous, UltraStar Deluxe, or Karaoke Mugen (all on Linux). Nothing in our design depends on it, because we only listen to the system output.
- Latency note: Bluetooth speakers add 100–250 ms of audio delay, making lights early. Prefer wired output; add a configurable `audio_delay_ms` for the light output to compensate if needed.

### 3.6 Open hardware questions

1. ~~Node connector / default IP~~ — confirmed: 3-pin, `192.168.1.20`. Still to check on the web page: Art-Net Net/SubNet/Universe = 0/0/0 and refresh rate (set 30 Hz).
2. ~~Node power~~ — decided: phone charger via USB-C (no PoE injector).
3. Ethernet-to-USB dongle: probably USB-A; user will confirm when picking it up. Linux supports most (r8152, ax88179) out of the box.
4. Pictures of fixture back panels (DMX connectors, DIP/menu) would help only if the display menus differ from the manuals; otherwise not needed.

---

## 4. Software architecture

### 4.1 Pipeline

```
 ┌────────────┐   blocks    ┌────────────┐  features   ┌──────────────┐ intents ┌───────────┐ channels ┌──────────┐ UDP ┌──────┐
 │ AudioSource│ ──────────► │  Analyzer  │ ──────────► │ Mood/Effect  │ ──────► │ Fixture   │ ───────► │ ArtNet   │ ──► │ Node │
 │ (loopback/ │  (callback  │ FFT, bands,│  (100 Hz)   │ Engine       │ (30 Hz) │ Layer     │  512 B   │ Sender   │     │      │
 │  file/mic) │   thread)   │ onset,beat │             │ scenes       │         │ profiles  │          │          │     │      │
 └────────────┘             └────────────┘             └──────────────┘         └───────────┘          └──────────┘     └──────┘
                                   ▲                          ▲
                               config.yaml              control (CLI/hotkeys: scene, blackout, tap-tempo)
```

### 4.2 Threading and timing model

- **Audio callback thread** (owned by the audio library): only copies the block into a lock-free ring buffer. No analysis here.
- **Analysis thread**: pulls 1024-sample hops @ 44.1/48 kHz (~21–23 ms) with 2048 window → features at ~45–100 Hz. Publishes the **latest `Features` snapshot** (immutable dataclass) via atomic reference swap.
- **Render/output loop** (30 Hz, drift-free using absolute deadlines `next += dt`, not `sleep(dt)`): reads latest features, steps the engine, writes the DMX frame, sends Art-Net.
- Plain `threading` + `queue`/ring buffer is sufficient (numpy releases the GIL). No `asyncio` needed.
- Optional smoothing between the 30 Hz output frames for faders (attack/release envelopes), so motion looks continuous.

### 4.3 Audio layer (`AudioSource` interface)

```python
class AudioSource(Protocol):
    sample_rate: int
    def start(self) -> None: ...
    def read(self, n: int) -> np.ndarray: ...   # mono float32, blocking w/ timeout
    def stop(self) -> None: ...
```

Implementations: `LoopbackSource` (platform-specific), `FileSource` (WAV/MP3 → real-time paced, for tests/demo), `InputSource` (line/mic fallback), `SyntheticSource` (click-track/sine sweeps for unit tests).

Details: mono downmix, float32, 44.1 or 48 kHz as provided by the device (no resampling unless needed), device selection by name substring in config with a `--list-devices` CLI command.

### 4.4 Analysis layer

Per hop produce a `Features` object:

| Feature | How | Used for |
|---------|-----|----------|
| `level` | RMS → dBFS → adaptive 0..1 (running min/max with slow decay) | intensity, mood |
| `bands` | FFT magnitude (Hann window), grouped: sub/bass 20–150 Hz, low-mid 150–500, mid 500–2k, high 2k–8k, air 8k+ ; each normalised adaptively | colours, strobe, haze |
| `onset` | spectral flux (half-wave rectified), per band; adaptive median threshold + refractory period (~100 ms) | kick/snare hits |
| `beat` | tempo tracker: autocorrelation/comb filter over onset envelope (60–180 BPM), phase lock, confidence 0..1 | beat-synced effects |
| `bpm`, `beat_phase` | from tracker; `beat_phase` in [0,1) so effects can run on the grid even if a hit is missed | sweeps, chases |
| `energy_trend` | 4–8 s moving average slope | mood engine, build-ups/drops |
| `centroid` | spectral centroid, smoothed | hue modulation |

Fallbacks: if beat confidence is low, effects fall back to raw onsets, then to level-follow. Manual **tap-tempo** override available.

Offline harness: run the same analyzer on WAV files, plot features, and compare against `librosa` beats and hand-labelled beats to tune parameters (this is where librosa is useful).

### 4.5 Fixture layer

Fixture profiles are JSON/YAML files describing channel functions, ranges and defaults; the engine speaks **abstract intents** (`dimmer`, `rgb`, `strobe_rate`, `pan`, `tilt`, `gobo`, …) and the profile converts them to DMX values.

```json
{
  "id": "generic-moving-head-11ch",
  "manufacturer": "TODO",
  "model": "TODO",
  "mode": "11ch",
  "channels": [
    { "offset": 0, "name": "pan",    "type": "pan",    "range": [0, 255] },
    { "offset": 1, "name": "pan_fine", "type": "pan_fine" },
    { "offset": 2, "name": "tilt",   "type": "tilt" },
    { "offset": 4, "name": "color",  "type": "color_wheel",
      "slots": [ {"name":"white","dmx":0}, {"name":"red","dmx":10}, {"name":"blue","dmx":20} ] },
    { "offset": 6, "name": "shutter","type": "shutter",
      "open": 255, "closed": 0, "strobe": [16, 131] },
    { "offset": 7, "name": "dimmer", "type": "dimmer" }
  ],
  "defaults": { "shutter": 255, "dimmer": 0 }
}
```

Patch (in `config.yaml`): `fixtures: [{ id, profile, universe, address, group, position }]`. Includes overlap detection at load time and a "home position" for blackout/idle.

Notes for moving heads: pan/tilt are slow – rate-limit movement and use smooth paths (sine/lissajous) rather than snapping per beat; colour wheels should only change on beat boundaries and not faster than ~1–2 Hz to avoid mechanical wear/noise.

### 4.6 Art-Net sender

ArtDmx packet (UDP 6454):

```
"Art-Net\0" | OpCode 0x5000 (LE) | ProtVer 0x000E (BE) | Sequence | Physical | SubUni | Net | Length (BE, even, 2..512) | data[length]
```

- Universe 0, sequence 1–255 (0 disables ordering), full 512-byte frames (or even-rounded to the highest used channel — keep 512 for simplicity).
- `Net/SubUni` configurable. Unicast IP configurable, optional broadcast.
- Optional ArtPoll discovery (nice-to-have, Phase 4b) to find the node IP.
- Socket errors (network down) are logged and retried with back-off; the render loop never blocks on I/O.

### 4.7 Effect/mood engine

- **Effects** are small classes: `update(features, t, dt) -> intents` — e.g. `StrobeOnKick`, `ColorWash`, `BeatChase`, `PanTiltSweep`, `Pulse`, `Blackout`.
- **Scenes** combine effects per fixture group and carry a mood tag: `ambient`, `groove`, `energetic`, `drop`.
- **Mood engine**: picks/blends scenes from `level` + `energy_trend` + BPM with hysteresis (min dwell ~8–16 s, crossfade 1–2 s) so lights don't flicker between states. Includes a build-up → drop detector (energy ramp then sudden bass return) for strobe/white flash moments.
- **Manual overrides** always win: scene lock, blackout, strobe-off.

### 4.8 Control and observability

- CLI: `run`, `list-devices`, `test-fixtures` (walk through channels), `replay <file.wav>`, `sim` (virtual fixtures).
- Live keys (terminal): `b` blackout, `1–9` scene, `space` tap-tempo, `s` strobe on/off, `q` quit with safe state.
- Optional minimal TUI (rich/textual) showing level bars, BPM, beat LED, current scene, DMX output preview. Not a v1 blocker.
- Logging to file with rotation; periodic stats (loop jitter, dropped audio blocks, send errors).

---

## 5. Tech stack

- Python ≥ 3.11, `numpy`, `scipy` (optional, for filters), `PyYAML`, `sounddevice` (or `soundcard`) for PipeWire/Pulse monitor capture, `rich` (optional TUI), `pytest`, `ruff`, `mypy` (light).
- Packaging: `pyproject.toml`, a venv, `uv` or `pip`. Launch script: `scripts/run.sh` (optional KDE desktop launcher).
- `librosa`, `matplotlib`: **dev/offline tools only**, in an optional extra (`pip install .[dev]`).

---

## 6. Channel maps (from the manuals)

### 6.1 Fun Generation PicoSpot 20 LED – **chosen mode: 9-channel (16-bit pan/tilt), start address 1** (occupies 1–9)

Why 9-ch: fine pan/tilt for smooth motion, plus shutter/strobe and gobo; the 11-ch adds only built-in programmes/speed that we don't want. (5-ch = no strobe/gobo/fine.)

| Ch | Function | DMX values |
|----|----------|-----------|
| 1 | Pan (coarse), 0–540° | 0–255 |
| 2 | Tilt (coarse), 0–230° | 0–255 |
| 3 | Pan fine | 0–255 |
| 4 | Tilt fine | 0–255 |
| 5 | Pan/tilt motor speed | manual says 0 slow … 255 fast, **but measured on the real unit (2026-10-02): 0 = fast, 255 = very slow/near-frozen** → profile uses `dmx_range [255, 0]` so norm 1.0 = fast |
| 6 | Colour wheel | 0–10 white · 11–21 red · 22–32 orange · 33–43 yellow · 44–54 green · 55–65 blue · 66–76 cyan · 77–87 purple · 88–175 "colour selection 6+" (half-way/split colours) · 176–255 wheel rotation |
| 7 | Gobo | 0–124 gobo select · 125–249 gobo shake · 250–255 wheel select |
| 8 | Master dimmer | 0–255 = 0–100 % |
| 9 | Shutter/strobe | 0–9 open (no strobe) · 10–255 strobe ≈ 1–25 Hz |

Other: 3-pin XLR; 35 W; display menu: `Addr`, `ChS` (channels), `brk` (behaviour on DMX loss); 13° beam, 12 W white LED.
Notes: some listings say 200° tilt but the manual's channel table says 230° – use 230° and verify physically. Gobo slot names and the 88–175 colour behaviour must be checked on the real unit. Patch decision: *keep channel 5 (speed) at a fixed high value* when tracking beats, lower for ambient.

### 6.2 Fun Generation LED Pot Strobe 100 – **chosen mode: 3-channel, start address 10** (occupies 10–12; leaves 1–9 for the PicoSpot)

| Ch | Function | DMX values |
|----|----------|-----------|
| 1 (abs. 10) | Dimmer | 0–255 = 0–100 % |
| 2 (abs. 11) | Operating mode | 0 = no function (**keep 0 → pure DMX control**) · 1–127 programme speed · 128–255 sound sensitivity |
| 3 (abs. 12) | Strobe | 0–1 no function · 2–255 strobe, **period linear in DMX value, measured ≈ 0.7–19 Hz** (see below) |

1-ch mode = dimmer only (no strobe) → not usable for beat strobing. 3-pin XLR, 38 W, 126 white SMD LEDs, 45° beam. Display: `d.xxx` for address, `1-ch`/`3-ch` for mode.
Manual warning: strobe may trigger photosensitive epilepsy.

**Measured 2026-10-02 (a6000, 1080/60p, shutter 1/250, `tools/strobe_analyze.py`):**

| Fixture | DMX value | Flash period | Rate |
|---------|-----------|--------------|------|
| PicoSpot ch9 | 10 / 60 / 102 | 1001 / 167 / 100 ms | **1.00 / 6.0 / 10.0 Hz** (manual's linear 1–25 Hz mapping confirmed; 120 and 200 were capped to 102 → 10.2 Hz) |
| Pot Strobe ch3 | 2 / 30 / 60 / 90 / 103 | 1451 / 1301 / 1135 / 968 / 884 ms | 0.69 / 0.77 / 0.88 / 1.03 / 1.13 Hz |

The Pot Strobe's **period is linear in the DMX value** (frequency is hyperbolic), confirmed by a second sweep with the cap lifted:

| Pot Strobe DMX | 103 | 150 | 190 | 220 | 235 | 245 |
|----------------|-----|-----|-----|-----|-----|-----|
| Period (ms) | 884 | 634 | 417 | 250 | 167 | 100 |
| Rate (Hz) | 1.13 | 1.58 | 2.40 | 4.00 | 5.99 | 9.99 |

Least-squares fit over all 10 points: `period_ms = 1464.1 − 5.535·DMX` (residuals ≤ 10 ms, i.e. within one 60 fps frame), giving 0.69 Hz at DMX 2 and ≈19 Hz at DMX 255. The profile stores it as `period_ms: [1453.1, 52.6]` (period at DMX 2 and 255). A 60 fps camera cannot measure above ~10 Hz, so the range 10–19 Hz is extrapolated and stays above the cap.

**Safety caps are now expressed in Hz** (`safe_max_hz: 10` in both profiles, rounded *down* to a DMX value): PicoSpot cap = DMX 101 (≈ 9.97 Hz), Pot Strobe cap = DMX 246 (≈ 9.9 Hz). The default 10 Hz limit can be changed per profile.

**Strobe behaviour assumption for the code (superseded by the measurement above for the Pot Strobe):** treat channel 3 as linear in frequency; the safety limiter (§9) converts the allowed max Hz to a DMX cap after we measure it (phone slow-mo / counting flashes at 3–4 values). Alternatively, **drive flashes ourselves** with short dimmer pulses on ch 1 at a controlled rate (exact timing locked to detected beats), which avoids relying on the fixture's own strobe oscillator at all. Plan to support both; decide after a hardware test.

### 6.4 Eurolite LED PIX-72 RGB Bar – **chosen mode: `12C2` (12-2-channel), start address 100** (occupies 100–111)

From the user's copy of the manual (`support/User_Manual_45263_1.pdf`, 28 pages, p. 25-26; not committed). Modes: 3, 5, 6, `12C1`, `12C2`; the display menu `Chnd` selects the mode, then `Addr` the start address. The bar has 4 segments (1–4, left to right in the manual's picture), each 18 LEDs.

| Mode | Channels |
|------|----------|
| 3 | R, G, B (whole bar) |
| 5 | R, G, B, master dimmer, ch5: 1–5 sound mode, 11–255 strobe |
| 6 | master dimmer, strobe, R, G, B, auto programs (16–255) |
| `12C1` | two halves (segments 1+2, 3+4): dimmer, strobe, R, G, B, auto programs each |
| **`12C2`** | **R, G, B for segment 1, 2, 3, 4 (ch 1–12), nothing else** |

Why `12C2`: independent segments for chases; no hardware dimmer/strobe to fight with (brightness is scaled in software, the PWM frequency is 13 kHz so there is no visible flicker); no strobe channel to misuse (flash safety stays in one place).

### 6.5 Varytec LED Derby ST – **chosen mode: 4-channel (`04Ch`), start address 200** (occupies 200–203)

From the Thomann manual (390843, section 7.5). The menu item `dMH` selects `01Ch`/`04Ch` and the address (`dxxx`).

| Ch | Function |
|----|----------|
| 1 | Derby colour combination by range: 6–20 red, 21–35 green, 36–50 blue, 51–65 white, 66–80 R+G, 81–95 R+B, 96–110 R+W, 111–125 G+B, 126–140 G+W, 141–155 B+W, 156–170 R+G+B, 171–185 R+G+W, 186–200 G+B+W, 201–215 all four, 216–230 colour change 1, 231–255 colour change 2; 0–5 off |
| 2 | 0–5 none, 6–255 strobe of the coloured LEDs, increasing speed (**never used: fixed 0 in the profile**) |
| 3 | beam motor: 0 none, 1–127 fixed position (indexing), 128–255 rotation (speed mapping unmeasured: profile slots hold 1 / slow 140 / medium 190 / fast 255 are guesses to be checked) |
| 4 | white-LED strobe patterns 1–17 (10–179, **unused**), 0–9 none, **180–255 white at full brightness without strobe** (`full` = 200) |

There is no dimmer: the Derby is on/off and colour-slot only.

### 6.6 Patch summary

| Universe 0 | Fixture | Mode | Channels |
|-----------|---------|------|----------|
| 1–9 | PicoSpot 20 LED | 9-ch | pan, tilt, pan-fine, tilt-fine, speed, colour, gobo, dimmer, strobe |
| 10–12 | LED Pot Strobe 100 | 3-ch | dimmer, mode (=0), strobe |
| 13–99 | free (movers block) | | |
| 100–111 | Eurolite LED PIX-72 RGB Bar (`wash1`, group `washes`) | 12C2 | s1_r, s1_g, s1_b … s4_r, s4_g, s4_b (4 segments) |
| 112–199 | free (washes/PARs block) | | |
| 200–203 | Varytec LED Derby ST (`derby1`, group `effects`) | 4-ch | colour, colour strobe (fixed 0), motor, white |
| 204–299 | free (effects block) | | |
| 300+ | reserved: atmosphere (haze) | | |

(Address blocks from the original plan are kept as the *expansion convention*; the two current fixtures sit at the start for simplicity.)

---

## 7. Repository layout

```
dmx_music_reactive/
├── PROJECT_MEMO.md              ← this file
├── dmx_music_reactive_project_plan.md   (original draft)
├── pyproject.toml
├── config/
│   ├── config.example.yaml      # network, audio, patch, scenes
│   └── fixtures/                # *.json profiles
├── src/dmxreactive/
│   ├── audio/        base.py, loopback_win.py, loopback_linux.py, file_source.py, synthetic.py
│   ├── analysis/     fft.py, bands.py, onset.py, tempo.py, agc.py, features.py
│   ├── dmx/          artnet.py, universe.py, fixtures.py, profiles.py
│   ├── engine/       effects.py, scenes.py, mood.py, renderer.py
│   ├── safety.py     # strobe limiter, blackout, safe-state
│   ├── control/      cli.py, keys.py, tui.py
│   └── app.py        # wiring / main loop
├── tools/            # offline analysis, plots, latency test, channel walker
├── tests/            # unit + replay tests, fake Art-Net receiver
├── scripts/          run.sh, setup_fedora.sh (dnf deps, NetworkManager dongle profile)
└── docs/             wiring photos, fixture manuals, event checklist
```

---

## 8. Development phases (revised, each with acceptance criteria)

Order change: build the **DMX output and simulator first**, so everything downstream can be tested without audio hardware or the real rig.

### Phase 0 — Repo bootstrap  ✅ done 2026-10-02
- `pyproject.toml`, lint/test config, layout from §7, `config.example.yaml`, CI-free local `pytest`.
- **Done when:** `pytest` runs green on an empty smoke test; `python -m dmxreactive --help` works on Linux.

### Phase 1 — Art-Net output + virtual rig (no hardware needed)  ✅ done 2026-10-02 (hardware check pending)
- `ArtNetSender`, `Universe` (512-byte buffer with clamp/set helpers), fixture profile loader + patch validator.
- Virtual fixture receiver: a small Art-Net listener that prints/plots channel values (or a basic window), plus a packet-format unit test.
- **Done when:** a test script sets channels by *fixture intent* and a receiver verifies exact packet bytes; 30 Hz sender holds jitter < 5 ms for 10 min.

### Phase 2 — Real rig bring-up
- Wire the chain, terminator, set node IP/universe, set fixture start addresses/modes.
- Verify with QLC+ first (known-good baseline), then with our own sender + `test-fixtures` channel walker.
- Fill in §6 and the JSON profiles from real manuals.
- **Done when:** our sender alone drives every channel of both fixtures correctly; blackout and home position work.

### Phase 3 — Audio capture  ✅ core done 2026-10-02 (live YouTube/speaker check + 30 min soak pending)
- `AudioSource` implementations for PipeWire/Pulse monitor, file, synthetic; device listing CLI; level meter script.
- Spike first: confirm monitor-source capture of YouTube/karaoke audio on the laptop with `sounddevice`, and that it is independent of the output device (speaker/headphones/mixer).
- **Done when:** meter reacts to system playback with no dropouts for 30 min; callback never blocks.

### Phase 4 — Analysis  ✅ core done 2026-10-02 (live listening check pending)
- Windowed FFT, bands, adaptive normalisation, onset detector, tempo/phase tracker.
- Offline harness vs. labelled tracks (build a small test set: 5–8 tracks across genres + click tracks).
- **Done when:** onset F-measure and beat hit-rate meet §1 targets on the test set; analysis uses < 15 % of a core; unit tests on synthetic click tracks pass (BPM ±1).

### Phase 5 — Effects + integration  ✅ software done 2026-10-02 (real-rig run pending)
- First effect: strobe on kick. Then colour wash from centroid/bands, moving-head sweeps tied to `beat_phase`.
- Wire everything in `app.py`, safety layer in place from day one.
- Latency measurement tool (see §10).
- **Done when:** full chain works live with music on the real rig; measured latency < 100 ms; no crashes in a 1 h soak.

### Phase 6 — Mood engine, scenes, polish
- Scenes (beat-strobe, colour sweep, slow pan/tilt ambience, drop flash), mood selection with hysteresis, manual overrides, tap-tempo, TUI.
- Launch scripts + setup docs for Fedora; event checklist (§11).
- **Done when:** a 3 h unattended run in a real room is clean and a second person can start/stop it from the README alone.

### Phase 7 — Expansion (post-event, optional)
PARs, derby, haze (see original plan §8). Architecture already supports them through profiles/groups; the main addition is channel-block allocation and haze logic with a **hard max-on-time and duty cycle**.

---

## 9. Safety and failure handling

- **Strobe limiter:** hard cap ≤ 10 Hz by default (configurable, never > ~15 Hz); no sustained full-white strobe > a few seconds; strobe can be disabled by one key. Add a notice for guests with photosensitive epilepsy at the event.
- **Safe state:** on start, exit, exception and signal (SIGINT/SIGTERM/console close) send a blackout/home frame several times. Fixtures hold the last DMX frame (or go to their own fail mode) if the sender dies — so a watchdog/`atexit` blackout matters.
- **Dead-man behaviour:** if no audio arrives for N seconds → fade to ambient scene; if audio device disappears → retry and keep lights in ambient.
- **Network loss:** log, keep trying; never crash the render loop.
- **Moving heads:** no sudden full-speed pan/tilt; clamp ranges so heads don't point into guests' eyes (set a tilt floor in the profile/patch).
- **Haze/fog (later):** duty-cycle limits and smoke-alarm warning; never auto-run unattended.
- **Electrical:** one power strip with a switch; check combined fixture wattage against the circuit.

---

## 10. Testing strategy

- **Unit:** ArtDmx byte layout, universe clamping, profile → DMX conversion, patch overlap detection, strobe limiter, adaptive normaliser, onset detector on synthetic signals.
- **Replay tests:** run a WAV through the analyzer + engine with a fake clock; assert on the DMX frames produced (golden tests for scenes).
- **Fake receiver:** local UDP listener standing in for the node; used in CI-like local runs.
- **Latency:** play a click track, record screen/lights with phone at 240 fps, or loop the audio output into a second capture and timestamp flash vs click. Report median and p95.
- **Soak:** 3 h run logging loop jitter, memory (RSS), dropped blocks, send errors.
- **Hardware-in-loop:** a bring-up checklist (channel walker, strobe Hz measurement, latency) on the real rig for each release tag.

---

## 11. Event-day checklist (draft)

1. Fixtures positioned, powered, DMX chain connected, **terminator on the last fixture**.
2. Node powered; PC static IP set; `ping <node>` OK.
3. `python -m dmxreactive test-fixtures` — every fixture responds, blackout works.
4. Select the right audio loopback device; play a track; level meter moves.
5. Run `scripts/run.sh` for 5 min with music; check beat LED and strobe cap.
6. Disable OS notifications/sounds, sleep/screen lock, auto-updates; set power plan to high performance; close browsers' extra tabs.
7. Spare: second DMX cable, adapters, laptop charger, a backup `QLC+` project with a static scene in case of software failure.

---

## 12. Risks and mitigations (updated)

| Risk | Impact | Mitigation |
|------|--------|-----------|
| Monitor capture picks wrong device or breaks when output changes (speaker ↔ mixer) | no audio | select by sink name/`@DEFAULT_MONITOR@`; re-open on device change; `--list-devices`; fallback USB line-in |
| Beat tracker unreliable on some genres (e.g. Christmas songs, soft/swing) | lights feel random | fall back to onset/level-follow; tap-tempo; confidence-gated effects; tune on a Christmas-heavy test set |
| Python jitter / GIL stalls | visible stutter | absolute-deadline scheduler, light render loop, `numpy` in analysis thread, profile before any C++ work |
| Audio device sample-rate/format mismatches | crash or noise | query device defaults, no hard-coded 44.1 kHz |
| Wrong DMX modes/addresses | fixtures behave oddly | channel walker tool, profiles from manuals, QLC+ cross-check |
| Network misconfig (subnet, firewall) | no output | documented setup, `ArtPoll` discovery, startup self-test that warns when no `ArtPollReply`/send errors |
| Cable/termination issues | flicker, ghost fixtures | proper 110 Ω cable, terminator, short test runs before event |
| Photosensitivity / safety | harm to guests | §9 limits, blackout key, notice |
| Scope creep (haze, PARs, derby, C++) | project never ships | gated behind Phase 7; v1 = strobe + moving head only |

---

## 13. Decisions (updated 2026-10-02)

| # | Question | Answer |
|---|----------|--------|
| 1 | Hardware | Eurolite DXT PoE Art-Net Node I; Fun Generation PicoSpot 20 LED; Fun Generation LED Pot Strobe 100 (manuals read, see §6) |
| 2 | OS | **Linux only**: Fedora KDE Plasma on HP EliteBook x360 830 G8 (PipeWire, USB-Ethernet dongle; no RJ45 port). **Windows dropped.** |
| 3 | Music source | Laptop itself plays YouTube/karaoke/etc. → speakers (via mixer for karaoke). Capture system output monitor. Mixer microphones are not captured. |
| 4 | Language / UI | Python for v1 and probably final (C++ only if profiling demands). CLI/TUI first; light browser GUI later (kept out of the core: the engine exposes a control API so a web UI can be added without redesign). |

Still open:
- Tilt range of the PicoSpot (200° vs 230°): check later on the real unit.
- Dongle model (USB-A expected; user to confirm).
- Real strobe Hz vs DMX value for the Pot Strobe, and PicoSpot gobo list (measure/visual test in Phase 2).
- Do we want an optional line-in of the mixer's output later (see §3.5)?

---

## 13a. Node settings (verified 2026-10-02, firmware V2.2, screenshot kept locally in `support/node_config_defaults.png`, not committed (shows the node's MAC))

Factory defaults kept: Static IP 192.168.1.20 / 255.255.255.0; Art-Net Net 0, SubNet 0, **Universe 1** (so our config uses `universe: 1`, port-address 1); Port 1 type **DMX Out 40 Hz** (node refreshes at 40 Hz; we send 30 Hz and it repeats the last frame in between). The node also has an sACN universe (= Art-Net address + 1) and a Light'J port, both unused.

## 13c. Phase 3 results (audio capture)

- `src/dmxreactive/audio/`: `PulseMonitorSource` (parec subprocess, bounded queue that drops the oldest block if the consumer lags, auto-restart with back-off, re-opens when the default output changes), `ArraySource`/`FileSource` (ffmpeg decode, real-time pacing), `click_track`/`sine` test signals, `level.py`, `devices.py`, `factory.py`.
- CLI: `dmxreactive list-devices`, `dmxreactive meter [--file F] [--device D] [--seconds N]`. Config section `audio:` (device, sample_rate 48000, block_size 1024 = 21 ms, latency_ms 20).
- Verified silently with virtual sinks (`pactl load-module module-null-sink`): tone level read back exactly (−15.03 dBFS for a 0.25-amplitude sine), no dropped blocks, ~46 blocks/s; after switching the default output, the capture reopens on the new sink's monitor within ~1 s and does not leak the old sink's audio.
- Mixer microphones are not captured (they never touch the laptop); only what the laptop plays.
- Live check done by user 2026-10-02: 30 s of real playback → 46.7 blocks/s (expected 46.9), 0 dropped, 0 restarts, signal around −23 dBFS RMS.
- Still to do: measure capture latency vs. real sound (loopback click test), 30 min soak, behaviour with a USB DAC/mixer as default output.

## 13c-2. Phase 4 results (analysis)

Code: `src/dmxreactive/analysis/` — `analyzer.py` (2048-pt Hann FFT every 1024-sample block = 46.9 frames/s), `dsp.py` (AdaptiveNorm, PeakNorm, OnsetDetector), `tempo.py` (comb-filter tempo/phase tracker with tempo prior and hysteresis), `features.py` (`Features`: level, 5 bands, centroid, flux, kick/hat events, bpm, beat_phase, beat, confidence, energy_trend; events carry monotonic counters so the 30 Hz render loop never misses one).
Tools: `dmxreactive analyze [--file F]` (live terminal view), `tools/analyze_track.py FILE --plot out.png --reference` (offline report, librosa comparison), `tools/click_eval.py` (synthetic ground truth).

Design decisions:
- **Beats are predicted from a free-running phase** locked to the onset envelope every ~0.13 s (best tempo/phase over the last 8 s), so a weak or missing kick doesn't drop a beat and breakdowns keep the grid. Tempo prior: log-normal, centre 140 BPM, σ 0.7 octave, range 70–200 BPM. Once a tempo has been confident for ~2 s, switching needs ~1.8 s of consistent evidence and 30 % better score (prevents breakdown flips).
- **Volume independence:** bands/level normalise against a decaying per-band peak (level: 24 dB range, bands: 30 dB, floors −60/−90 dB), flux against a decaying peak. Silence (< −65 dBFS for > 1 s) resets the tempo and gates all events.
- Kick = adaptive-threshold onset on the bass-band flux (refractory 120 ms); hat = same on the high band.

Measured (click tracks, ground truth): BPM exact from 90 to 190 (≤ 1 BPM error); grid beats land 0–21 ms *after* the true click (one analysis frame of quantisation, never early); kick events 7–28 ms after the click (median ≈ 17 ms). Add `beat_lead_ms` in the effect engine to compensate pipeline latency (the phase is predictive, so beat-synced effects can fire early).

Measured on the user's tracks (`support/tracks/`, git-ignored):

| Track | Found | librosa | Notes |
|-------|-------|---------|-------|
| Wildstylez – Timeless | 150.0 BPM, stable from ~5 s | 152 | beat F1 0.97 vs librosa; slow intro locks too |
| KBS – Huokausten silta (karaoke) | 134.0 BPM | 136 | F1 0.99 |
| Poison (Tune Up Remix) | 140.0 BPM | 143.6 | F1 0.90 (librosa's tempo grid is coarse) |
| Technoboy & Isaac – Digital Playground | 150.0 BPM; holds through breakdowns; 0 BPM at the silent tail | 152 | F1 0.59 overall because librosa locks to the off-beat (+176 ms) for ~40 s; ours stays on the kicks (88 % within 50 ms) |

**Live recordings (2026-10-02, `dmxreactive record` of YouTube playback, analysed offline):**

| Clip | Result |
|------|--------|
| Falco – Out Of The Dark (Daniel Cain hardstyle remix) | 155 BPM, stable, beat F1 0.99 vs librosa, 77 % of beats within 60 ms of a kick, beat-locked 98 % of the time |
| Umberto Tozzi & Monica Bellucci – Ti Amo | **no steady tempo**: tempo hops 90 → 120 → 180 BPM; autocorrelation shows the strongest periodicity at **60 BPM** (1 s) plus weaker 90 and ~178 — a slow ballad with triplet (12/8) feel and no steady kick; only 24 % of beats near a kick; beat-locked 38 % (mostly only in the last 20 s) |

Conclusion: widening the tempo range (min 50–55 BPM, other priors) did **not** fix Ti Amo and broke 174/190 BPM click tracks, so the 70–200 BPM range / centre 140 prior stays. The fix is semantic: a new **`beat_locked`** feature (kicks must fall on the predicted grid: share of the last ~8 kicks within min(60 ms, 15 % of the period), lock at ≥ 0.5, unlock < 0.3; chance level ≈ 0.2–0.25). Locked fractions after 12 s: karaoke 94 %, Poison 94 %, Falco 98 %, Digital Playground 66 %, Timeless 62 % (unlocks in breakdowns/outro), Ti Amo 38 %. **Effect-engine rule (Phase 5/6): beat-synced effects (strobe on beat, chases) only when `beat_locked`; otherwise fall back to level/band-following and slow ambient motion.** `beat_confidence` alone is not a reliable trust signal (Falco 0.54 vs Ti Amo 0.43).

CPU: ≈ 3.1 ms per block (≈ 15 % of one core; budget 21 ms). Known weak spots: the first ~5–15 s of a track (lock-in; confidence is low until then), kick events fire slightly more often than beats in rolling-bass sections, no downbeat/bar detection yet (Phase 6 candidate).

## 13c-3. Phase 5 results (show engine + live runtime)

Code: `engine/show.py` (Show: scenes, mood switching, safety, hotkeys; pure logic, no I/O), `engine/primitives.py`, `engine/sim.py` (deterministic offline simulation), `live.py` (analysis thread + 30 Hz render loop + keys), `control/keys.py`; CLI `dmxreactive run [--file F] [--target host:port] [--scene auto|beat|ambient] [--seconds N]`; config section `show:`; tools `tools/simulate_show.py FILE --plot out.png`.

Behaviour:
- **Scenes:** `ambient` (default, no reliable beat): mover breathes with loudness, slow pan/tilt wander (45 s / 31 s periods), slow colour drift (20 s), strobe light dark. `beat` (when `beat_locked`): Pot Strobe flashes on every predicted grid beat (dimmer pulse, 70 ms decay, fired `beat_lead_ms`=30 ms early using the phase extrapolated to "now"); mover pulses on the beat, pan/tilt paced by beats (16/11-beat periods), colour changes every 8 beats. Auto mood switch: ambient→beat after lock ≥ 2 s and ≥ 6 s dwell; beat→ambient after unlock ≥ 4 s; 1.5 s crossfade.
- **Safety:** flash rate limiter (`max_flash_hz` ≤ 10, default 8) applied to every flash; built-in strobe channels are never used (forced off); pan/tilt limited to `pan_limits`/`tilt_limits` with slew limiting (0.8 /s); colour wheel changes ≥ 1.5 s apart; `master` ceiling; silence or lost audio fades all dimmers to 0 (2 s); blackout key immediate; every exit path sends blackout frames.
- **Hotkeys:** `b` blackout toggle, `s` strobe light on/off, `a` auto, `1` force beat, `2` force ambient, `3` force drive, `+`/`-` master, Ctrl-C quit.
- Analysis exceptions are caught per frame (lights stay alive); render loop never blocks on audio.

Verification (no hardware): 13 show tests (scene switching, flash timing vs true clicks within ~1 frame, rate cap, no flashing without lock, silence/no-audio dark, blackout/strobe keys, limits, colour rate limit, crossfade, <1 ms per tick); simulation on user tracks — Falco 87 % beat scene, 2.7 Hz max flash rate; Timeless 72 %; karaoke 81 %; Ti Amo 27 %; real-time end-to-end run against the fake node: 30.2 packets/s, 0 sequence gaps, final frame blackout, ~25 % of one core for the whole pipeline.

Not yet verified on the real rig: end-to-end latency (audio → light; `beat_lead_ms` is a guess), how the movement/colour pacing looks, flash brightness at `master` 0.8.

## 13c-4. Day 2 (2026-10-03): first live feedback and fixes

User's first real-rig run: ambient mode looked good; (1) the spot sometimes "does not know where to move" and makes small movements for ~1 s; (2) strobe hits the rhythm but flashes in low-energy moments and misses high-energy ones. Diagnosed on the user's tracks in simulation (`diag` of flashes vs bass energy and pan direction reversals), then fixed:

| Problem | Cause | Fix | Result (simulation, 6 clips) |
|---------|-------|-----|------------------------------|
| Flashes in quiet moments | the predicted grid keeps running through breakdowns | new `bass_energy` feature (bass band vs a *slow* ceiling, −0.25 dB/s, peak-held 250 ms) gates grid flashes (`flash_gate` 0.35) and scales flash/accent intensity | flashes in low-bass moments 12–28 % → 0–6 % |
| Drops missed | beat scene was entered slowly (2 s lock + 6 s dwell) and left after 4 s | enter after 1.5 s lock / 3 s dwell; leave only after 10 s unlocked (the grid survives breakdowns); strong off-grid bass hits flash too (`kick_flash_strength` 0.6, limiter still caps rate); ambient scene flashes only on very strong bass hits (≥ 0.75, rise ≥ 6 dB, ≤ 3 Hz) | isolated strong kicks without a flash (±150 ms): Falco 3/102, Timeless 30/166 (was 120/166 at ±80 ms), DP 15/96, Poison 18/99; karaoke 77/209 (syncopated bass hits), Ti Amo 40/50 (ballad: by design) |
| Spot hesitation / small movements | beat position was recomputed from the phase, so every grid re-sync made pan/tilt targets jump (≈ 1.2 direction reversals per second) | beat position integrates smoothly with BPM; a fired beat only nudges it 25 % toward the nearest whole beat | pan reversals per 115 s: Timeless 139 → 33, Digital Playground 67 → 29, karaoke 53 → 30 |

Findings: the kick "rise" (bass jump) is *not* a reliable transient test (hardstyle median rise 4–6 dB vs noise 8 dB), so it is only used for the ambient-scene flash; beat-scene flashes rely on the verified grid + bass energy. New config keys (`show:`): `flash_gate`, `kick_flash_strength`, `ambient_flash_energy`, `ambient_flash_rise_db`, `ambient_flash_hz`. New Features: `bass_energy`, `last_kick_strength`, `last_kick_rise_db`. 76 tests pass.

Still to verify on the rig: whether the hesitation is gone in ambient mode too (ambient motor speed 0.6 may still be too fast for the slow wander; slower speed + fewer target updates is the next lever); whether flash gating feels right on live material.

## 13c-5. Day 2 (continued): frenchcore "LSD Problem" and the drive scene

User feedback: Timeless/Digital Playground work well; no movement hesitation/jitter observed any more; **Sefa & Mr. Ivex – LSD Problem** (frenchcore) barely flashed although it should "blink a lot" (baseline in simulation: beat scene 6 %, **2 flashes in 227 s**).

Diagnosis (`tools/analyze_track.py`, autocorrelation, kick IOI histograms, comb scores):
- True tempo ≈ **207 BPM** (autocorrelation 0.87 at 206.7, also 105 and 70); the tempo search stopped at 200 BPM, so the tracker sat at 105 (half-time) or 140 (2:3) and `beat_locked` was true only 5 % of the time. Kick IOIs are ~290 ms in some sections and ~170 ms (rolling) in others, i.e. no steady grid.
- The distorted kick keeps much of its punch above 150 Hz, so the 20–150 Hz `bass_energy` reads 0.2–0.5 and the energy gate suppressed most flashes.

Fixes:
1. `BPM_MAX` 200 → **240**; **half-time preference**: if 2× the winning tempo has a comb score ≥ 85 % of the winner's, take the faster pulse (a prior-overruling switch is judged on raw comb support so hysteresis cannot block it). Ordinary 90/105/120 BPM click tracks are not doubled (tested); 207 and 215 BPM click tracks lock; Digital Playground locks better (80 % vs 66 %). Limit: a 225 BPM click does not double because clicks that fall between analysis frames lose half their amplitude in the comb interpolation; real kicks smear over 2+ frames.
2. New **`drive` scene** (third scene next to `beat` and `ambient`): when kicks are dense (≥ 2.0 kicks/s smoothed), bass energy ≥ 0.2 and some periodicity (confidence ≥ 0.15) but no verified beat lock, **each detected kick flashes** (gate `drive_flash_gate` 0.25 bass energy; same 8 Hz hard cap) and pulses the mover; motion is paced by time (pseudo 120 BPM), colour changes every 4 s. Mood rules: `beat` (verified grid) has priority; ambient→drive after 1 s of energetic; drive→ambient after 4 s without; beat→drive/ambient only after 10 s unlocked. Key `3` forces drive; `scene: drive` in config.

Result (simulation, 100–120 s): LSD Problem **2 → ~170 flashes** (drive scene ≈ 85 % of the time, median flash interval 300 ms), real-time end-to-end run (30 s, fake node): 81 flashes, 30.1 packets/s, no gaps, final blackout, 34 % of one core. Other tracks unchanged or better; flashes in low-bass moments: Poison 1 %, Timeless 2 %, Falco ~13 %, Digital Playground/LSD ≈ 10–18 % (gate sweep: gate 0.1/0.2/0.3 → LSD 194/169/146 flashes and 26/18/10 % low-bass; chosen 0.25 as the compromise; tune `drive_flash_gate`). 83 tests pass.

Known trade-off: loud broadband noise also reads as kick-dense (≈ 3 kicks/s, confidence 0.16) and enters drive, flashing at ≤ 3/s under the 8 Hz cap; a real discriminator (kick-interval structure) would be needed if that ever matters.

## 13c-6. Day 2 (continued): moving-head flashes, ambient breathing, drops

User tested with many songs: "all looks awesome"; asked for (a) the moving head to flash sometimes and (b) its brightness to "breathe" in ambient mode (neither was planned beyond Phase 6 ideas).

Implemented (`engine/show.py`, `engine/primitives.py`):
- **Mover flash:** every Nth flash event (default N = 4, `mover_flash_every`, 0 = off) the moving head flashes along with the strobe (dimmer to full master for ~90 ms, full level on the first frame via `Decay(full_first_frame=True)`). Ordinary beat pulses were lowered (peak 0.7, base 0.12 + 0.35·level) so flashes stand out. Mover flashes obey the same flash limiter and the `s` key (**`s` now means "all flashing off"**: strobe light and mover flashes; pulses and motion continue).
- **Ambient breathing:** the mover's brightness follows the music's loudness and breathes slowly on top (`breath_period_s` 7 s, `breath_depth` 0.6 → dips to 40 % of its level; 0 disables; per-mover phase offset 0.12).
- **Drop detection (new, also Phase 6 groundwork):** `Ctx.drop` fires once when `bass_energy` returns to ≥ 0.6 after ≥ 1.5 s below 0.3. In every scene a drop flashes the strobe at full level and the mover together, and it has priority over an ordinary beat on the same tick.
- Simulation on the user's tracks (100 s each): mover flashes ≈ 1/3 of strobe flashes (LSD 57 vs 162, Timeless 53 vs 182, Falco 35 vs 127); ambient mover dimmer swings 0.02–0.45 (silence fades included). 87 tests pass (breathing period ≈ 7 s via FFT, flash every 4th event, disable paths, drop flashes both).

Open question for the user: master brightness feels right? Ambient peak brightness is modest (≈ 0.35–0.45 of full at master 0.8) — tune `master`/base level if the ambient look should be brighter.

## 13c-7. Day 3 (2026-10-03): drop blast, hardware limits of the moving head's dimmer

User test: ambient breathing looks nice; could not see the moving head's flashes (even with master 20 %, and the moving beam makes it harder to judge) and never saw a drop flash; asked whether the hardware can show such short pulses and whether the drop effect could last 0.5–2 s.

Findings and changes:
- **Drops were almost never detected** on the user's tracks (Timeless 0, Digital Playground 0, Falco 1, Poison 1 with the old "bass energy < 0.3 for 1.5 s" rule): their breakdowns keep a good deal of bass. New detector (`Show._detect_drop`): reference = 85th percentile of the smoothed (0.6 s) bass energy over the last 30 s (a first version with a decaying-max reference got stuck at the start-up transient); breakdown = energy ≥ `drop_dip` (0.25) below the reference for ≥ 1 s; drop = sustained return to within 0.12 of it for 0.4 s; at most one per `drop_min_gap_s` (10 s); only in music with groove (a locked beat or kick-dense in the last 30 s) so ballads/pads/speech do not blast. Simulated drops: Timeless 39.5 (the main drop), 52.6, 78.2, 91.4, 103.8 s; Digital Playground 11.8, 24.0, 65.8, 78.3, 114.6; Falco 19.5, 50.9; LSD ~8 per 230 s; KBS and Poison none; Ti Amo 2.
- **Drop = blast of `drop_duration_s` (default 1.5 s, allowed 0.2–4 s):** the strobe light stutters at 6 Hz (under the flash cap) for the first 40 % and then fades linearly; the moving head jumps to full brightness, holds through the stutter part and fades with it, and its colour wheel is forced to white (bypassing the colour hold). `s` (flashing off) silences the strobe part only; the mover swell still happens. Overlays any scene.
- **Mover flash now holds** `mover_flash_hold_ms` (200 ms) before decaying (`HoldDecay` envelope) — the PicoSpot's dimmer is a slow, smoothed LED driver (it is a stage-effect head, not a strobe); whether even 200 ms is visible is **to be measured**: new command `dmxreactive pulse-test mover1 [--mode dimmer|strobe] [--widths 33,67,133,200,333,500]` pulses the dimmer (or bursts the head's built-in strobe, which the earlier camera test showed produces clean 1–10 Hz flashes) so the real response can be seen or filmed. Fallback if dimmer pulses stay invisible: let the mover "flash" by bursting its built-in strobe channel (profile cap 10 Hz) for ~300 ms.
- Tests: 25 show tests (blast length/white/flash cap/strobe key, drop only after groove, HoldDecay).

## 13c-8. Day 3: GoPro measurements (240 fps, shutter 1/480) and what they changed

User confirmed the drop blast now works. The GoPro clips are HEVC, which this machine's ffmpeg cannot decode (no hevc decoder; no libx264 either), so `tools/video_luma.py` decodes with **PyAV** (`av` in the dev extras; ~125 fps at 1080p) and `tools/latency_analyze.py` uses it.

| Clip | Test | Result |
|------|------|--------|
| GX014448 | Pot Strobe dimmer pulses (33/67/133 ms) | **crisp**: rise/fall 4 ms (one frame); widths come out 29–154 ms because our 30 Hz frames are resampled by the node's 40 Hz output (→ run at 40 Hz) |
| GX014446 | PicoSpot **dimmer** pulses 33–500 ms | slew-limited: ≈ **170 ms** to swing full scale up and ≈ 170 ms down. Peak reached: 33–67 ms → ~25 % of the swing, 133 ms → 40–60 %, 200 ms → 65–85 %, ≥ 333 ms → full. Explains why the old 90 ms mover flash was invisible |
| GX014447 | PicoSpot built-in **strobe** bursts at 6 Hz | crisp: ~17–20 ms full-brightness flashes, 4 ms edges, dark between (the head is lit only during the flash; steady when strobe = 0) |
| GX014449 | click track → show (kick path) → light | flash is **+98 ms late** vs the click sound (59 clicks after lock: p10 +84, p90 +124) |

Software pipeline alone (new `tools/pipeline_latency.py`, real-time file source → analysis → show → packets on a fake node): kick path ≈ +34…+51 ms after the click; predicted-grid path with the old 30 ms lead ≈ +1 ms. So ≈ 64 ms of the 98 ms lies outside the software (audio capture buffering, Art-Net → node 40 Hz output, fixture, camera/speaker offsets). Note: in the click-track clip the flashes were kick-triggered (the synthetic click's bass energy decays between clicks, so the energy gate blocked grid flashes); the kick path cannot be led, the grid path can.

Changes made:
- `beat_lead_ms` 30 → **95** (software grid latency 30 + the ~64 ms outside it; software-only check now shows grid flashes −74 ms early, i.e. ≈ 0 on the real chain — to be confirmed by a re-film). Dataclass default stays 30 (neutral for simulation); `config.yaml` sets 95.
- `output.rate_hz` 30 → **40** (matches the node's 40 Hz output, less width jitter); audio capture `latency_ms` 20 → **10** with `PIPEWIRE_LATENCY` requested for the parec process (tone integrity checked, 0 drops).
- **Mover flash now uses the head's built-in strobe** (`mover_flash_mode: strobe`, default): every 4th flash event a ~250 ms burst at 8 Hz (profile cap 10 Hz applies, DMX ≤ 101), dimmer lifted to 0.9 so the flashes have light; `dimmer` mode (HoldDecay) remains available. Off with `s`, with `mover_flash_every: 0`, and in blackout.
- `dmxreactive run --flash-path grid|kick` (latency-test aid) isolates one strobe path; `tools/pipeline_latency.py [--path grid|kick]`.
- Remaining latency of the kick path (drive scene / off-grid flashes) ≈ 100+ ms cannot be led away; options if it bothers: smaller `audio.block_size` (512), a delay on the audio output, or a predictive drive scene for regular kicks.

To verify next: re-film `dmxreactive run --flash-path grid --scene beat` with the click track and run `latency_analyze.py clip.mp4 --lead 95` (expect a median near 0 ms), and look at the new mover strobe bursts live.

## 13c-9. Day 3: latency re-test with the new settings (GX014450, GoPro 240 fps)

`run --flash-path grid --scene beat` + 150 BPM click track, `tools/latency_analyze.py ... --lead 95`: 70/70 clicks (after lock) paired with a flash; **flash minus click = median −20 ms** (p10 −33, p90 −10, spread 23 ms ≈ render tick 25 ms + node output 25 ms quantisation). Before the changes the strobe was +98 ms late (kick path), so predicted-beat flashes are now ~20 ms early. `beat_lead_ms` set to **85** (not the tool's suggested 75): light slightly early is better tolerated than light late (audio-visual sync detection thresholds are asymmetric: ≈ 45 ms for sound ahead of picture vs ≈ 125 ms for sound behind it). Kick-triggered flashes (drive scene, off-grid hits) remain ≈ 100 ms late; user is fine with that; a C++ DSP rewrite would save only ≈ 5–10 ms (software is ≈ 35–50 ms of which analysis is ≈ 3 ms per 21 ms block), better levers if ever needed: `audio.block_size` 512, delaying the audio output, predictive drive scene.

## 13c-10. Day 3: launcher, pre-flight, status panel, Bluetooth delay

User's order: launcher/checklist and status display before the soak run; mixer + wires come tomorrow; **music currently plays through a Bluetooth party speaker** (pre-flight detected `bluez_output…` as the default sink). A Bluetooth speaker delays the sound ~150–250 ms while the lights follow the PC audio before that delay, so the lights run ahead of the sound; the 85 ms `beat_lead_ms` was tuned for wired/laptop output.

Done:
- **`show.light_delay_ms`** (0 = wired; try ~150–250 for Bluetooth): the features (hence beat grid, kicks, drops) are held back by that long (`FeatureHolder` delay line; the beat phase is extrapolated to "now − delay"), which, unlike a lead, also delays kick-triggered flashes. `tools/latency_analyze.py` now prints the matching `light_delay_ms` when its suggested lead goes negative. To measure for Bluetooth: re-run the click-track GoPro test (`run --flash-path grid --scene beat`, `paplay support/click150.wav`) with the Bluetooth speaker as the output and set `light_delay_ms` ≈ 85 − suggested lead … (the tool prints it).
- **Status panel** (`control/tui.py`, rich; `dmxreactive run` shows it on a terminal, `--plain` for text): scene (and forced/blackout/flashing-off flags), BPM / LOCKED / confidence / level trend, band sparklines, bass energy, BEAT/KICK/FLASH lamps, flash and drop counters, per-fixture state (dimmer bars, pan/tilt, colour swatch/name, strobe), audio health (blocks, dropped, restarts), analysis errors, packets/s, send errors, late ticks, master/lead/delay, last 5 events, hotkeys. Drawn on its own thread at ~8 Hz so it cannot disturb the 40 Hz render loop. Verified in a real pseudo-terminal.
- **`dmxreactive preflight [--walk] [--no-audio]`**: config, network (is an interface on the node's subnet?), **node ArtPoll** (parses the ArtPollReply: name, IP, net/subnet/universe → catches the universe mix-up of day 1), audio output/monitor/short capture test (silent → warning, no blocks → error), and a **Bluetooth-output warning**; hints for every failure; exit code for scripting; `--walk` runs the fixture channel walk after the checks. Run against the real rig: node `PoE_Node_I_4c6ca7` at 192.168.1.20 answered, universe matches, capture works.
- **`scripts/start_show.sh`** (pre-flight, asks before starting if something failed, then `dmxreactive run "$@"`), **`scripts/install_desktop_entry.sh`** (writes `~/.local/share/applications/dmxreactive.desktop`, opens konsole; **not installed yet — run it to get the launcher**), **`docs/EVENT_CHECKLIST.md`** (setup, checks, during the party, troubleshooting, packing list; derived from §11).
- ArtPoll support in `dmx/artnet.py` (`build_artpoll`, `parse_artpollreply`, `discover`), `FakeNode` can answer polls; 106 tests pass.

Still open: USB DAC/mixer as default output and unplug/replug (needs the wires, tomorrow), Bluetooth `light_delay_ms` measurement, whether the launcher feels right.

## 13c-11. Day 3: soak test

`tools/soak.py` (hardware-free, silent): a looping playlist of `support/tracks/` plays into a virtual null sink (`dmxsoak`), the real capture path (parec on its monitor) feeds the analysis, the full show runs at the configured rate and sends Art-Net to a fake node on loopback; a metrics line is logged every minute (RSS, CPU, packets/s, sequence gaps, late ticks, p95/max loop error, audio drops/restarts, analysis/send errors, flashes/drops, threads, open fds) and PASS/FAIL criteria are applied at the end (memory growth ≤ 15 MB after 5 min warm-up, constant threads/fds, no sequence gaps/bad packets/errors, late ticks ≤ 0.1 %, no capture restarts, dropped audio blocks ≤ 0.1 %, packet rate ≥ 97 % of nominal). The sink is removed on exit.
Found while preparing it: `LoopStats.errors` grew without bound (~15 MB per 3 h) → now a bounded deque (+ running maximum); `FakeNode(record_times=False)` for long runs. 2.5-minute dry run: PASS (41.5 → 41.8 MB RSS, 40.0 pkt/s, 0 gaps, 0 late ticks, 30–36 % of one core).
**3 h run started 2026-10-03 18:54 (detached, with a sleep inhibitor), log `support/soak_20261003_1854.log`, expected end ≈ 21:54.** Heavy work on the laptop (e.g. the full test suite) would distort its timing numbers.

### Soak result (3 h, 2026-10-03 18:54–21:54): **PASS**
179 one-minute samples, 83 tracks played (looping the 7-track playlist, including the live recordings): RSS 41.6 → 41.9 MB (**+0.10 MB over 174 min after warm-up**, i.e. no leak), threads constant at 5, open fds constant at 9; CPU 37–38 % of one core (analysis + show + output + capture, steady); **40.0 packets/s in every sample**, 0 sequence gaps, 0 bad packets, 0 send errors, 0 analysis errors; **0 late ticks** (worst single tick 16.6 ms), 0 dropped audio blocks, 0 capture restarts. Scenes at the sample times: beat 119, drive 51, ambient 9. Activity: 24,474 flashes (≈ 8,150/h) and 299 drop blasts (≈ 100/h, i.e. about one per 36 s on this dance-heavy playlist; `drop_min_gap_s` is the knob if that is too frequent). In parallel the user ran the real rig from YouTube for the same period without issues.

## 13c-12. Day 4 (2026-10-04): wired audio, unplug test, hardware inventory

**Hardware inventory (as used):** HP EliteBook x360 830 G8 (Fedora KDE, PipeWire); Eurolite DXT PoE Art-Net Node I (USB-C powered, 192.168.1.20, universe 1); Fun Generation PicoSpot 20 LED (9-ch, address 1) and LED Pot Strobe 100 (3-ch, address 10); **USB-Ethernet dongle: TP-Link UE200(UN) ver. 2.0 (USB-A, Fast Ethernet)**; **Bluetooth speaker: Music Sound BTSPKMSBOOMBOXX, model FB-2808-C**; **mixer: K BOX Sunfly SFA-108 (karaoke mic mixer)**; Sony a6000 and GoPro Hero 7 for measurements.
**Audio chains:** (a) Bluetooth: PC → BT speaker (default sink `bluez_output…`, sound ~150–250 ms late); (b) wired: PC headphone jack (3.5 mm, sink `alsa_output…HiFi__Headphones__sink`) → 3.5 mm-to-RCA cable → mixer's two RCA inputs → mixer's two RCA outputs → cable to the speaker's 3.5 mm AUX input. Microphones plugged into the mixer are not heard by the PC (the lights follow the backing track only).

Results today:
- Pre-flight is clean on the wired chain (audio output = Headphones sink, capture level up to −12 dBFS) and shows the Bluetooth warning with the BT speaker as default output.
- **Unplug/replug (output removed while a song plays):** verified with virtual sinks that the capture follows automatically (reopened within ~0.5 s on the new default, restarts 1 then 2, level restored); on the real laptop YouTube itself stops when its output vanishes, the lights fade to dark (no audio) and everything runs again once playback resumes — intended behaviour.
- **Important correction:** **all earlier latency measurements (GoPro click tests, §13c-8/9, `beat_lead_ms` 85) were made with the Bluetooth speaker**, i.e. against sound delayed by the BT link. The wired AUX chain has the sound earlier, so the lead/light delay values must be re-measured for each chain. Subjectively the timing looks good on the wired chain.
- Plan: two GoPro click-track clips (wired and Bluetooth) → the difference of the two medians is the Bluetooth latency; wired result sets `beat_lead_ms`; Bluetooth gets `show.light_delay_ms`.

### Wired vs Bluetooth latency clips (GX014451 wired, GX014452 Bluetooth, GoPro 240 fps, `--lead 85`, `run --flash-path grid --scene beat`)
- Bluetooth: flash − click = **median −24 ms** (p10 −34, p90 −15, 70/71 clicks) — consistent with the earlier BT test (−20 ms at lead 95).
- Wired: the flashes sit at a stable phase of **250 ms mod 400 ms** against the click grid (spread ±15 ms; 71/72 clicks paired; the analyser's pairing window wraps, hence its p10 −145 / median +238 / p90 +250). Only one alias is physical: the wired sound must arrive *earlier* than the Bluetooth sound, so **wired = +238 ms (lights late)** and the Bluetooth link delays the sound by **≈ 262 ms** relative to the wired chain (the other alias would make Bluetooth 138 ms *faster* than a cable).
- The **difference (262 ms) does not depend on the GoPro's audio/video alignment** → `config/config_bt.yaml` (local, git-ignored; `DMXREACTIVE_CONFIG=config/config_bt.yaml scripts/start_show.sh` or `dmxreactive --config config/config_bt.yaml run`) = wired config + `show.light_delay_ms: 260`.
- **Open contradiction:** the *absolute* wired value (+238 ms late) disagrees with (a) the user's impression that wired timing looks good and (b) the software/capture estimates (software ≈ −64 ms with lead 85, capture path measured 58–90 ms write→monitor, node ≤ 25 ms ⇒ expected ≈ 0 ms). Suspect an uncalibrated constant in the GoPro measurement (audio/video alignment of the camera; container start times are both 0). If the camera's A/V offset is ≈ +240 ms, the true wired offset is ≈ 0 (lead 85 is right) and Bluetooth without delay would run ≈ 260 ms early. **To settle it: a GoPro clip of hand claps (hands and sound together, 1 m away) measures the camera's own A/V offset**; until then `beat_lead_ms` stays 85 (do not re-tune it from the wired clip).
- Capture path measured directly (very quiet clicks written to the default sink, real-time paced): write → monitor capture **58–90 ms** (BT sink). An attempt to hear the speaker with the laptop microphone gave no detections (too quiet/far); the headphone jack was unplugged at the time, so the wired sink could not be measured.

### Camera A/V calibration attempts (2026-10-04)
- **Hand-clap clip GX014453:** unusable — the camera looked at the lower body in dim light, the instant the hands meet is not visible (7 audio claps found at 1.80, 2.85, 3.96, 5.05, 6.11, 7.18, 8.82 s; a frame contact sheet shows hands only as dark blobs by the waist; automatic motion analysis was inconsistent).
- **Mic cross-check on the wired chain** (laptop mic next to the speaker, clicks written to the Headphones sink): the microphone hears each click **9–19 ms after the monitor capture sees it**, i.e. the wired sound path after the capture tap is only ~10–20 ms; the write→capture time was unreliable (392 vs 97 ms, headphone sink waking from standby).
- Together with the software estimate (−64 ms at lead 85 + capture 20–30 + node ≤ 25 + sound path 15 ⇒ lights ≈ 40 ms *early* on wired) the +238 ms wired GoPro reading implies a constant camera/decoder offset of roughly +250–280 ms (the same offset also explains the −24 ms Bluetooth reading, which would otherwise be ≈ −300).
- New objective calibration, **`tools/av_sync_flash.py`**: the laptop flashes its whole screen white and plays a click at the same instant (every 1.2 s, ~0.8 Hz); film the screen with the speaker audible and analyse with `latency_analyze.py clip --lead 0 --skip 3 --min-lag -0.6 --max-lag 0.6`: the result is the camera's A/V offset (± the laptop's display/audio delay difference, a few tens of ms). Pending: user's clip.

### Camera A/V calibration result (GX014454, screen flash + click, laptop speakers)
Screen flash (fades up over ~20 ms, 120 ms on) vs click: **flash 101 ms before the click** (median −101, p10 −108, p90 −80, 14 pairs; onset = 30 % of span). Independently expected: write → sound ≈ 58–97 ms (write→capture) + 9–19 ms (monitor→mic) ≈ 70–115 ms ⇒ the **GoPro/decoder A/V offset ≈ 0 ± 30 ms**; the earlier hypothesis of a +250 ms camera bias is rejected (the 60 Hz laptop screen is accurate enough: ±15 ms). The 'flash minus click' numbers of the earlier clips can be trusted, modulo the click period: with 150 BPM clicks (400 ms) the wired reading (+238 ms) is ambiguous with −162 ms, the Bluetooth reading (−24) with +376/−424. Next: a wired clip with 120 BPM clicks (500 ms period; `support/click120.wav`) to separate +238 from −162.

## 13c-13. Day 4: latency model resolved (GX014455: wired, 120 BPM click track, lead 85)

`latency_analyze.py --lead 85 --skip 15 --min-lag -0.3 --max-lag 0.45`: **wired flash − click = median −181 ms** (p10 −199, p90 −164, 53/55 clicks). At 500 ms spacing this separates the two aliases of the 150 BPM wired clip (+238 ≡ −162 mod 400): the lights were **~170 ms early on the wired chain** (lead 85). Consequences:
- Camera A/V offset ≈ 0 (screen-flash calibration), so the measurements are valid.
- Bluetooth sound is ≈ **254–262 ms later** than wired ⇒ with lead 85 the BT lights were ≈ −424 ms (more than one 400 ms beat) early, i.e. they coincided with the *previous* beat's sound — which is why Bluetooth "looked in sync" in all earlier tests (a coincidence of tempo, not a calibration).
- Software model: kick-path flash = grid-path(lead 95) + ≈ 118 ms (BT clips: +98 vs −20) ⇒ relative to the wired sound: **grid(lead 95) ≈ −180 ms, kick path ≈ −62 ms** (early), so the wired sound leaves the speaker ≥ ~130 ms after the capture tap (audio driver/ALSA buffering is larger than first assumed). (The earlier laptop-microphone measurement 'monitor→mic 9–19 ms' is invalid: the mic signal was saturated.)
- New settings: **wired: `beat_lead_ms` 0, `light_delay_ms` 60** (expected: kick flashes ≈ 0 ms, predicted-beat flashes ≈ −25 ms, i.e. slightly early); **Bluetooth (`config/config_bt.yaml`): `light_delay_ms` 315** (60 + 254), lead 0. `light_delay_ms` delays all paths (also kick-triggered ones). Tool note: `latency_analyze.py`'s 'suggested beat_lead_ms' is only meaningful for the grid path and must be read modulo the click period; use two tempi (150 and 120 BPM) to dealias.
- To verify (pending, two wired clips at 120 BPM with the new config): `run --flash-path grid --scene beat` (expect ≈ −25 ms) and `run --flash-path kick --scene beat` (expect ≈ 0 ms), analysed with `--lead 0 --min-lag -0.3 --max-lag 0.45`.

### Verification clips (GX014456 grid path, GX014457 kick path; wired, 120 BPM clicks, `light_delay_ms` 60, `beat_lead_ms` 0)
- Predicted-beat flashes: **−40 ms** (p10 −54, p90 −23; 56/56 clicks); expected −25 → ok.
- Kick-triggered flashes: **+17 ms** (p10 +5, p90 +36; 48/60 clicks); expected 0 → ok.
Both within ~20 ms of the model. Trim: `beat_lead_ms` **−30** (delays only the predicted-beat flashes → ≈ −10 ms; kicks stay at +17 ms) in all three configs (wired, Bluetooth, example); `light_delay_ms` stays 60 (wired) / 315 (Bluetooth). `latency_analyze.py` now warns when a reading is larger than a quarter of the click period (alias risk).

## 13c-14. Day 4 (continued): dark drops, PicoSpot questions, downbeat idea

- User installed the desktop launcher; the browser GUI is not needed now (the terminal panel is liked) — **kept as a future idea** (a light web UI on top of the existing control API if interest grows).
- **Dark drop (user's idea, implemented):** at a detected drop the *moving head goes completely dark* for `drop_blackout_s` (1.5 s) while the strobe stutters (6 Hz, true darkness between flashes), then the head **pops back to full white** for 0.4 s and fades over 1 s (total ≈ 2.9 s). Rationed by `drop_blackout_every_s` (**90 s**; 0 = never): the first drop after the cooldown goes dark, the other drops stay ordinary white blasts. Falls back to the white blast when flashing is off (`s`). Simulation on the user's tracks: LSD 8 drops → 2 dark, Timeless 5 → 1 (the main drop at 39.5 s), Digital Playground 5 → 2, Falco 2 → 1, Ti Amo 2 → 1. 110 tests pass (dark part fully dark incl. mover bursts, true-dark stutter, white pop, ration, off-cases).
- **PicoSpot:** the tilt range question is settled by the manual (**tilt 230°**, pan 540°; the 200° came from a shop listing), no limits were hit in tests. The manual gives no gobo patterns or per-gobo DMX boundaries (only "gobo selection 0–124" for 7 static gobos + open, "shake" 125–249, "gobo wheel selection" 250–255); the manual's images are only logos. Gobos stay unused until the user checks the real wheel.
- **Downbeat accent** (explained to the user; not implemented): estimate which beat of the 4-beat bar is the "one", then align bar-level effects to it (mover flash on the one instead of "every 4th flash event", colour change on the bar, drops land on bar boundaries, a stronger accent on the one).

## 13c-15. Day 4 (continued): downbeat (bar) detection

Goal: know which beat of the 4-beat bar is the "one" and align bar-level effects to it. Built, tested, wired into the show; **by-ear verification by the user pending**.

Work and findings:
1. **Beat counting had to be fixed first.** The old tempo tracker counted beat *events*; skipped/duplicated events and phase corrections made "beat index mod 4" drift against the real bar. `TempoTracker` now keeps a continuous beat position (floor = beat index, high-water mark so a backward correction never repeats a beat) and reports `index` and `epoch` (changes whenever the grid is re-locked/reset). Grid epochs on the user's tracks are now long (Timeless 1 epoch of 277 beats, Digital Playground 361, Falco 144, Poison 136). No regression in tempo accuracy, lock statistics or tests.
2. **`analysis/bar.py` `BarTracker`** (4/4 assumed): per beat it summarises a 16-band log spectrum, bass energy, onset strength, kick strength; evidence = (a) *change peaks*: spectral change between the 2 beats after a boundary and the 2 before it, only local maxima (±2 beats) above 1.2× the recent median vote for the boundary's phase (sections/phrases change on bar boundaries); (b) a small *accent* vote (onset/bass/kick z-scores). Votes accumulate per phase (decay 0.985/beat ≈ 65 beats), the winner is switched only with hysteresis (1.25× lead for 8 beats), confidence = vote spread. Decisions are 3 beats late. New Features: `beat_index`, `grid_epoch`, `bar_beat` (0 = the one, −1 unknown), `bar_confidence`, `bar_start`.
3. **Validation without ground truth:** (i) synthetic 4/4 track (kick every beat; bass note + crash on every bar's one; chord change every 8 bars): ≥ 90 % of detected ones land within 70 ms of a real accent, beats numbered 1-2-3-4 consecutively; (ii) an offline pseudo-oracle (whole-track beat-synchronous chroma/MFCC/mel novelty with peak picking per grid epoch; clear on Timeless 17/24 peaks on one phase, Falco, Poison, one LSD stretch; ambiguous on Digital Playground and the karaoke track): the online tracker agrees with it on **86 % of beats** (Timeless 100 %, Falco 95 %, Poison 86 %; chance 25 %). The oracle is not ground truth — hence the ear test below. (A first attempt using accent votes only and drop onsets as a proxy gave 33 %: drop onsets are only good to ±0.5 beat.)
4. **Show integration** (`bar_min_confidence` 0.35): in the beat scene, with a trusted bar position the strobe flash on the one is full strength and the other beats 0.8×; the moving head's flash burst fires **on the one** (otherwise it falls back to every 4th flash event); colour changes every 2 bars aligned to bar starts (otherwise every 8 beats from an arbitrary start). Status panel shows `bar 1 2 3 4 (confidence)` with the one highlighted; `dmxreactive analyze` prints `bar N`.
5. **Ear test tool:** `tools/bar_check.py song.mp3 out.wav` renders the track with a loud beep on every detected one and a quiet tick on the other beats; four files were generated in `support/tracks/` (Timeless 66 ones, mean confidence 0.89; Digital Playground 85, 0.78; Poison 25, 0.80; Falco 31, 0.87). 115 tests pass.
Known limits: 4/4 only; ambiguous on vocal/karaoke music (confidence stays low there, so effects fall back); after a grid re-lock the bar position must be found again (several bars).
Future (not done): kill the lights on the last beat before a predicted drop using bar/phrase counting; browser GUI (low priority).

## 13c-16. Day 5 (2026-10-05): feedback, PicoSpot gobo wheel decoded from video

User feedback: bar_check tracks all sound solid, no slipping; downbeat looks right from the panel indicator and the lights, though the continuously moving head makes it hard to judge; the **dark drop will be judged in about a week** when the remaining ambient wall lights are in place; overall: "close to commercial systems", rapid kick transitions look great on hardstyle/frenchcore.

**PicoSpot gobo wheel** — from the user's phone video `VID_20261005_175302715.mp4` (1920×864, 30 fps, 54.6 s; beam patterns on the left, terminal on the right) of `gobo-walk` (DMX 0…124 step 4, 1.5 s each, 3 s lead-in): pattern segmentation by mask differencing found 8 stable patterns separated by transitions at 10.3, 16.3, 22.3, 28.3, 34.4, 40.3, 46.4 s; with the start of the beam at T0 ≈ 1.2 s and the value k applied at T0 + 3 + 1.5·k these are the commands for DMX 16, 32, 48, 64, 80, 96, 112 (the boundary lies within 4 DMX below each; each gobo spans exactly 16 values, i.e. 8 positions × 16 = 128, "shake" begins at 125 per the manual):

| DMX | Pattern | slot name (profile) |
|-----|---------|---------------------|
| 0–15 | open, plain circle | `open` (8) |
| 16–31 | ring broken into four arcs | `ring` (24) |
| 32–47 | petal starburst (elongated ellipses around a hub) | `petals` (40) |
| 48–63 | spoked star with round tips | `star` (56) |
| 64–79 | brick mosaic (scattered rectangles) | `mosaic` (72) |
| 80–95 | scattered dots/bubbles | `dots` (88) |
| 96–111 | swirl (three curved blades) | `swirl` (104) |
| 112–127 | diagonal slats | `slats` (120) |

Implementation: `gobo` is now a **select channel** in `picospot20_9ch.json` (default `open`); the show picks gobos from `show.gobo_palette` (default open, dots, star, swirl, petals, mosaic; `ring` and `slats` available), **changing on bar boundaries** — every 4 bars when the bar position is trusted (else every 32 beats; drive scene ≈ 16 s; ambient scene one change per ~45 s), never faster than `gobo_hold_s` (5 s, the wheel is mechanical), always **open during the drop effect**; `gobo_mode: off` keeps it open; the status panel shows the active non-open gobo. 4 new tests, **119 pass**.

## 13c-17. Day 6 (2026-10-14): wall wash, Derby and fog machine arrive

Hardware: Eurolite **LED PIX-72 RGB Bar**, Varytec **LED Derby ST**, Stairville **AF-40** fog machine (standalone, manual; needed because the Derby's and PicoSpot's beams are invisible without particles in the air). Channel tables: §6.4, §6.5 (PIX-72 from the user's PDF copy, Derby from the Thomann manual). Git housekeeping: the memo hand-off commit had stayed on `docs/status-handoff` (never merged), so it was cherry-picked onto `update/add_new_lights_and_features`.

**Built (software only, untested on the real rig):**
- Profiles `config/fixtures/pix72_12c2.json` (12 continuous channels `s1_r` … `s4_b`) and `derbyst_4ch.json` (select channels `color`, `motor`, `white`; `color_strobe` is a *fixed* 0 and the white strobe patterns are not reachable: the Derby can never flash by accident). Patch: `wash1` at 100 (group `washes`), `derby1` at 200 (group `effects`), following the address convention in §6.6; also in the user's local `config.yaml` / `config_bt.yaml`.
- Scene roles (`engine/show.py`): **wash**: ambient = slow colour drift (20 s per palette step, opposite the mover's colour), brightness breathing as a wave along the four segments; beat/drive = bass-following floor, a one-segment chase per flash event (so it inherits the shared flash cap), **the whole bar on the ONE**, colour per bar; **drop blast** = snaps to white; **dark drop** = dark with the rest, then the white pop. Colour changes crossfade (τ 0.25 s), brightness does not. **Derby**: on while the bass is there (hysteresis around `flash_gate`, so it goes dark in breakdowns by itself), colour per bar with `colour_hold_s`, beam motor slot per scene (`show.derby_motor_ambient: slow`, `derby_motor_beat: medium`); drop blast = white + the white LEDs at full; dark drop = off. Master brightness scales the wash only (the Derby cannot dim, it is on/off with the master/silence/blackout).
- Walker: segments one at a time (checks the physical order), whole-bar R/G/B, Derby motor slots and white LEDs.
- 9 new tests in `tests/test_new_lights.py` (profiles, chase, silence/blackout, breakdown, white blast, dark drop, flash cap, Derby strobe channel stays 0), walker test extended.

**Open (needs the real rig):** set the PIX-72 to `12C2` / `Addr 100` and the Derby to `04Ch` / `A200`; `preflight --walk`; find out what the motor values 128–255 actually do (speed direction/mapping) and fix the `slow/medium/fast` slots in the profile; judge brightness (the 4 segments on a wall: `master`), colours (RGB mix on camera vs eye), dark-drop timing with all lights.

**Fog machine AF-40:** documentation only (checklist). It is not on DMX and not under software control.

## 13c-18. Day 6 (2026-10-14): first GoPro measurements of the new lights (`camera-test`)

New command `dmxreactive camera-test derby-motor|wash-ramp|wash-pulse` (`control/camera_tests.py`): each item is preceded by a slate (wash white 0.3 s, dark 0.7 s), items are found in the clip by those flashes. Clips (GoPro 240 fps, `support/`): GX014458 derby-motor, GX014459 wash-ramp, GX014460 wash-pulse. The clips' own slate times match the script (item spacing exact), so cutting by slate works.

- **Wash pulses (GX014460):** commanded 25/50/100/200/400 ms come out as 25/50/100/200/400 ms (half-max width, ±4 ms = one camera frame); even the single 25 ms frame reaches 100 % peak; rise/fall within one camera frame. The wash is effectively an instant on/off for our flash purposes.
- **Wash ramp (GX014459):** smooth and monotone from 2 % up (white luma over dark: 10, 14, 19, 31, 47, 62, 82, 99 at 2, 5, 10, 20, 35, 50, 75, 100 %), no flicker (std < 0.6), exposure stable. Luma is gamma-encoded, so no linearity claim. **Colour balance cannot be judged from these clips: the camera's white balance was off** (the white slate shows as RGB ≈ 0/160/255, i.e. a strong blue cast), so set the GoPro white balance to ≈ 5500 K / native before any colour work, and analyse RGB instead of luma.
- **Derby motor (GX014458):** DMX 1 and 32 = stationary (frame difference = sensor noise); 64 and 96 = barely creeping (drift only visible over seconds); **from 128 the pattern really moves and speeds up monotonically to 255** (decorrelation time of the image: 0.65 s at 128, 0.33 at 144, 0.20 at 160, 0.12 at 176, ~0.07 s at 192-240, 0.05 s at 255, so ≈ 13x faster at 255 than at 128; the fast end is limited by the analysis' 17 ms sampling). Profile slots (slow 140 / medium 190 / fast 255) therefore are roughly 1 : 4 : 8 in speed; `hold` (1) truly stops it, which is what the wear-free mode needs. The absolute rotation period was not obtained (the pattern does not repeat cleanly in an 8 s window). The Derby's "white" colour looked blue only through the camera's white balance (bright spot cores read RGB ≈ 200/248/254).

## 13c-19. Day 6 (2026-10-14): pre-drop kill, first offline look (negative result)

Tools: `tools/predrop_features.py` (caches per-block features of tracks) and `tools/predrop_eval.py` (finds breakdowns with the show's drop-detector logic, measures their length in beats/bars and where the return lands relative to the bar "one"). Run on 5 tracks (Timeless, Digital Playground, LSD Problem, Poison, Falco): 28 breakdowns.

- **Counting bars from the breakdown start does not reach the needed ±1 beat:** only 4 % of breakdown lengths are within 1 beat of a multiple of 4 bars. Causes: (1) the detector's start and return times jitter by several beats (0.6 s smoothing, dip/return thresholds, 0.4 s hold; in Falco/Timeless/LSD the bass is already back 1-2 beats before the reported return); (2) tempo octave flips inside one track (LSD reads 105 / 140 / 210 BPM, so "beats" are unreliable); (3) most "breakdowns" are short bass gaps of 1-2 bars, not 8/16-bar phrases. The few clean long ones: Digital Playground 88.5-114.1 s = exactly 64 beats (16 bars), Timeless 24.8-39.1 s ≈ 9 bars (8 + detection lag).
- **Where this leaves the feature:** not built live. The sample is too small to claim a rule, and there is no ground truth. Needed: the user marks drop times (mm:ss) for ~10 tracks, then candidate predictors (phrase grid from the previous drop, riser/snare-roll signature, bar-aligned breakdown start) can be scored against them. Also worth fixing first: a tempo that stays in one octave through a track.

## 13c-20. Day 6 (2026-10-14): wear-free mode (`show.mechanics`)

Motivation: the PicoSpot (pan/tilt motors, colour and gobo wheels) and the Derby (beam motor) wear out; for near-daily use the user wants a mode without moving parts. Modes (`show.mechanics`, CLI `run --mechanics`, hotkey **m** cycles live, shown in the panel as `MECHANICS STATIC/OFF`):
- `full`: as before.
- `static`: moving head **parked** (`park_pan`/`park_tilt`, default centre, still slewed softly and clipped by the pan/tilt limits), **gobo wheel open, colour wheel fixed** (`static_colour`, default white; the wheels are set directly at the mode change, one move, not constantly), also during drop blasts (the blast's white-forcing is skipped). Derby motor on `hold` (DMX 1 = stationary, measured §13c-18). Everything electronic keeps working: head dimmer, breathing, flashes and bursts of its built-in strobe, Derby LEDs/colours/white blast, wash, Pot Strobe.
- `off`: additionally the moving head (dimmer, strobe) and the Derby stay dark; wash and Pot Strobe carry the show.
A static head is the beam of a fixed spot; since the lamp is an LED, only the motors and wheels are spared. The quiet alternative with no code, a config without `mover1`/`derby1`, also still works. 5 new tests (parking, wheels, motor hold, off incl. drop blast, key cycling live, config validation). Not yet tried on the real rig.

## 13c-19b. Backlog: pre-drop darkness

Parked by the user (2026-10-14) until there is ground truth: the user collects good sample tracks and marks the drop times; then score predictors (phrase grid from the previous drop, riser/snare signature, bar-aligned breakdown start) and fix the tempo octave flips first. See §13c-19.

## 13c-21. Day 7 (2026-10-15): Bluetooth characterisation with all lights, wash vs strobe

Setup: Bluetooth speaker, `config/config_bt.yaml` (`light_delay_ms` 315, `beat_lead_ms` −30), `run --scene beat --mechanics static`, GoPro 240 fps on the lit wall (strobe spot at the left, wash bar at the lower right, head parked; strobe light also spills over the wash area). `latency_analyze`-style pairing of click and flash onsets (first `--skip` 15 s, window ±450 ms):
- **GX014461** 120 BPM click, grid path: flash − click = **+13 ms** (61/61 clicks, p10..p90 +1..+24). **GX014462** 150 BPM, grid path: **+11 ms** (67/68, −1..+19). Both tempi agree, so no aliasing. **GX014463** 120 BPM, kick path: **+10 ms** (48/60, −6..+37). Conclusion: the Bluetooth calibration (315 / −30) is right; lights sit ≈ 10-13 ms after the sound, within camera frame plus room sound travel. No change made.
- **Wash vs Pot Strobe (GX014465, `camera-test wash-strobe`, `tools/alt_sync.py`):** the single-ROI clips cannot separate the two lights (both flash on the same events and the strobe lights the whole wall), so a dedicated program alternates 100 ms pulses (wash on the second, strobe +0.5 s). Strobe pulses have twice the brightness step of wash pulses (75 vs 37 luma units), which classifies the onsets without relying on order. Result: the **wash comes ≈ 12 ms after the strobe** (strobe − wash − 500 ms = −12.0 ms, wash − previous strobe − 500 ms = +13.0 ms; 30 pairs; p10..p90 −16..+9 ms; DMX frame = 25 ms). Half a DMX frame, not perceptible; **no per-group delay needed**.
- Lesson: when two lights flash together, test them against each other with a program that separates them in time; do not rely on one ROI each. Also note the light positions in the frame when sending clips.

## 13c-22. Day 7 (2026-10-15): wash colours measured, decision

`camera-test wash-palette` (GX014468, GoPro ISO 800, WB 5500 K, shutter 1/480): all 8 palette colours at 100 % and 25 % are stable (no flicker) and keep their chromaticity when dimmed (25 % / 100 % sums 0.21-0.26 except green 0.15, camera-noise level). Camera RGB at 100 %: red 62/2/4, orange 60/12/2, yellow 53/30/1, green -6/38/-4, cyan -6/34/77, blue -3/2/74, purple 34/5/66, white 45/46/61. Findings: the primaries are clean; **white at equal RGB is bluish (B ≈ 1.35 R, G in the encoded camera values)**, a blue gain of ~0.5-0.7 would neutralise it; yellow reads orange-leaning and cyan blue-leaning on the camera; green is only 19 % of white's camera brightness, which a camera with its green channel weighting cannot be compared to the eye on. These are numbers from an uncalibrated consumer camera (full-frame means, encoded values), not photometry.
**Decision (user, by eye): colour balance does not matter much for disco lighting; levels are good, timing matters.** No palette or balance change. The knobs stay available (`show.wash_gain`, `wash_gamma`, `wash_balance`, `wash_colours`, defaults neutral) if taste ever asks for it; the first thing to try would be `wash_balance: [1.0, 1.0, 0.6]`. The user watched the palette test and noted that the wash pushes more light than before. Note for later measurements: the user has a PhD in spectral imaging; set up the GoPro (fixed ISO, white balance, flat profile) together before filming.

## 13c-23. Day 8 (2026-10-16): idea, Raspberry Pi tapped after the Yamaha pre-amp

**Idea (user):** the user and a friend both have a Yamaha WXC-50 pre-amplifier feeding Genelec active speakers. A Raspberry Pi (the user already owns a **Pi 4**) would sit beside the speakers, take the analog audio from the pre-amp output and run the show. Any source that reaches the Yamaha (PC, phone, AirPlay/MusicCast) then drives the lights, and the Bluetooth-only limitation goes away. **Verdict: green light, nothing built yet; this is a design note.**

**Why it fits:**
- The Pi hears what the speakers hear, after the Yamaha's network buffering and DSP, so the Bluetooth delay (`light_delay_ms` 315) should not be needed. Expect a wired-style profile with a small delay (capture buffer plus the Art-Net path; the wired PC profile was 60 ms). Measure it with the click track and two tempi (see `measurement-lessons`), and ask which chain was used.
- Raspberry Pi OS uses PipeWire, and the panel is a terminal UI, so it can run headless over SSH.

**Open points / risks (to check, not yet known):**
1. **Audio input:** the Pi has no line-in. It needs a USB audio interface with a line input (about 15-30 EUR), fed from the pre-out through a passive RCA Y-splitter. A line input is high impedance (like a voltmeter in parallel), so it barely loads what the speakers get.
2. **Yamaha outputs:** not verified. Check the back panel for a stereo pre-out, and whether the level is fixed or follows the volume knob. A fixed level is much better. If it follows the volume, check how the detection copes with a changing level (turning the music down must not kill the lights). Genelecs may take XLR, so an RCA-to-XLR adapter may already be in use.
3. **CPU on the Pi 4:** the live analysis has not been measured there. Measure CPU use and dropped blocks first. A Pi 5 is the fallback.
4. **Ground loop:** a Pi and USB interface on a different supply than the amp can add hum to the capture, which could trigger the lights (it would not reach the speakers). A cheap audio isolator fixes it.
5. **Network:** the Pi needs Ethernet to the Art-Net node. A PoE hat could power it from the same switch.

**Cheap test first (no new code):** plug a USB audio interface into the PC, tap the Yamaha pre-out, select that input and run the current project. This tests the tap, the level behaviour and the delay before any Pi work.

**Possible follow-ups:** a `config/config_wired.yaml` profile for the Pi, a Pi setup section in the docs, and the hardware-agnostic notes for publishing (item 5 in the hand-off).

## 13d. Later hardware measurements (camera)

**GoPro Hero 7 (≈ 240 fps)** was used for the first round (see §13c-8; the planned clips below were recorded and analysed) (the Sony a6000 tops out at 1080/60p, confirmed from its specs). Planned clips, in priority order:
1. `pulse-test mover1` (dimmer pulses 33–500 ms) and `pulse-test mover1 --mode strobe` — how fast the PicoSpot's dimmer rises/falls and whether its built-in strobe gives crisp bursts; decides how the "mover flash" is implemented.
2. `pulse-test strobe1 --widths 33,67,133` — the Pot Strobe's dimmer response (our strobe flashes are 70 ms dimmer pulses).
3. **End-to-end latency** (sets `beat_lead_ms`): `python tools/make_click_track.py support/click150.wav 150 40`; run `dmxreactive run --scene beat`; play the click WAV with `paplay`; film lights + speaker with the GoPro (audio on); `python tools/latency_analyze.py clip.mp4 --lead 30` pairs every click in the audio with the first flash onset and suggests a new `beat_lead_ms` (validated on a synthetic clip with a known 80 ms offset: measured +83 ms). Note the tools decode with ffmpeg only (this machine's ffmpeg has no libx264 encoder; decoding of camera files works).
Skipped on purpose: Pot Strobe above 10 Hz (the cap stays at 10 Hz).

(Previous notes below.)

- **TODO (optional, precision): high-frame-rate strobe re-measurement.** The user recalls the a6000 can record at much higher frame rates once its settings are found again (to our knowledge the a6000 tops out at 1080/60p, so this is unconfirmed; if it is not possible, a phone's 240 fps slow-motion mode, or a photodiode/LDR into the sound card's line-in analysed as audio, would give kHz-level timing). Goals: (1) measure the Pot Strobe above 10 Hz (10–19 Hz is extrapolated) and the PicoSpot up to 25 Hz before ever raising `safe_max_hz`; (2) measure flash pulse width; (3) measure end-to-end audio→light latency (click track on the laptop → flash). Use `strobe-sweep --max-value` and `tools/strobe_analyze.py` (the analyser's ~10 Hz limit is a 60 fps limit, not an algorithm limit). Millisecond precision is a nice-to-have, not a requirement for the party use.
- PicoSpot tilt range (200° vs 230°) and gobo list.

## 13b. Implementation status

- Phase 0–1 implemented: `src/dmxreactive/dmx/` (universe, artnet, profiles, frameloop, fake_node), `config.py`, `app.py` (run loop with blackout on exit), `control/` (CLI `check-config` / `sim` / `test-fixtures`, channel walker). 25 tests pass, ruff clean.
- Measured on loopback (30 s): deadline error mean 0.001 ms / max 0.22 ms, no late ticks, no sequence gaps (10-minute soak not yet run).
- Strobe mappings measured with camera (§6.2); safety caps in Hz (`safe_max_hz`).
- Hardware verified 2026-10-02: node, both fixtures, channel walk, strobe rates. Still open: PicoSpot tilt range (200° vs 230°), gobo list, 10-minute jitter soak on the real network.

## 14. Status and hand-off (updated 2026-10-16)

**State:** v0.1.0 is merged into `main` (squash-merged from `create/foundation`; the user set up their own git identity and keeps their real email private). Everything below is built, committed, tested (135 tests) and verified on the real rig where hardware is involved, except what the 2026-10-14 update below lists. Work since v0.1.0 was squash-merged into `main` as d652eb5 (2026-10-15); the old feature branches are deleted. New work goes on a fresh branch (commits allowed freely, pushing and merging only after asking).

### What exists
| Area | State |
|------|-------|
| Art-Net output, fixture profiles (PicoSpot 9-ch, Pot Strobe 3-ch), fake node, channel walker, ArtPoll discovery | done, hardware-verified |
| Audio capture (PipeWire monitor via `parec`), device follow, unplug/replug recovery | done, verified (virtual sinks + real unplug) |
| Analysis: bands, kicks, tempo 70–240 BPM with half-time preference, predicted beat grid, `beat_locked`, **bar/downbeat detection**, `bass_energy`, drop detection | done; tuned on 6 tracks (hardstyle, frenchcore, pop, karaoke, ballad) + live recordings; downbeat verified by ear (no slipping) |
| Show: 3 scenes (beat / drive / ambient) with auto mood switching, flash limiter (≤ 8/s, hard cap 10), pan/tilt limits, colour & gobo wheel pacing, silence fade, blackout; **mover flash bursts, ambient breathing, drop blast, dark drop (≤ 1 per 90 s), gobos on bar boundaries, flash/colour changes aligned to the bar's one** | done; dark drop live test pending (needs the wall lights) |
| Runtime: status panel (rich), `preflight` (network, node ArtPoll universe check, audio, Bluetooth warning), `scripts/start_show.sh` + desktop launcher, `docs/EVENT_CHECKLIST.md` | done; launcher installed by the user |
| Timing: GoPro-calibrated (wired: `light_delay_ms` 60, `beat_lead_ms` −30; Bluetooth profile `config/config_bt.yaml`: delay 315) | done (§13c-9…13) |
| Soak: 3 h silent soak (virtual sink + fake node) | PASS (§13c-11) |

### Update 2026-10-14
- **New lights:** wall wash (PIX-72) and Derby are in the software (profiles, patch, scene roles, walker, tests: §13c-17). The user's WALK on the real rig showed both reacting, so addresses/modes work; scene behaviour and taste on the rig are still untested. The fog machine is manual and documented in the checklist.
- **GoPro measurements of the new lights (§13c-18)** via the new `dmxreactive camera-test derby-motor|wash-ramp|wash-pulse` (slated programs, `control/camera_tests.py`): wash flashes are instant and exact down to one frame (25 ms); wash ramp smooth from 2 %; Derby motor stationary at DMX 1-32, creeping 64-96, **really turning from 128 and ~13x faster at 255**; profile slots fine. Camera settings used: shutter 1/480, WB 3200 K (blue cast: use ~5500 K for colour judgement), ISO 100-1600 auto (may compress brightness numbers).
- **Wear-free mode (§13c-20):** `show.mechanics: full | static | off`, `run --mechanics …`, hotkey `m`. `static` = head parked, wheels fixed, Derby motor on hold, LEDs/flashes still work; `off` = head and Derby dark. Built and tested, not yet tried on the rig.
- **Pre-drop darkness (§13c-19): offline look was negative**, so it is **backlog**, waiting for the user's sample tracks with marked drop times.

### Waiting / next
1. **(Done in code and WALK-verified 2026-10-14, see §13c-17/18; scene behaviour on the rig pending.) Remaining lights (user is purchasing, an ambient/wall wash that illuminates the whole wall).** When they arrive: get model + manual (DMX modes and channel table), add `config/fixtures/<model>.json` (see the PicoSpot/Pot Strobe profiles for kinds `continuous`/`select`/`rate`/`fixed`), a patch entry with a free address block (convention: washes 100–199) and a new group (e.g. `washes`), then give the group roles in the scenes (`engine/show.py`): ambient = slow colour wash/breathing, beat = bass-following brightness with the one as accent, drop blast = white, **dark drop = wash also goes dark (the user wants all lights but the strobe to go dark)**, drive = fast colour steps. Check addresses against the other fixtures (`dmxreactive check-config`), run `preflight --walk`, and extend tests (`tests/test_show.py` helpers `new_show`, `mover`, `strobe_series`).
2. **Pre-drop kill (BACKLOG, offline attempt negative, §13c-19):** darkness for the last beat (or two) before a predicted drop. Counting bars from the detected breakdown start does not reach ±1 beat on the 5 test tracks (4 % hits): the detector's start/return times jitter by several beats, the tempo flips octaves inside a track (LSD 105/140/210 BPM), and most detected "breakdowns" are short bass gaps. **To continue:** (a) the user collects good sample tracks and marks drop times (mm:ss), ideally ~10 incl. their typical odd music; (b) first make the tempo stay in one octave through a track; (c) score candidate predictors against the marks with `tools/predrop_features.py` + `tools/predrop_eval.py` (phrase grid from the previous drop, riser/snare-roll signature before the drop, bar-aligned breakdown start); (d) only then build it live, rationed like the dark drop. A wrong prediction costs one needless dark beat.
3. **Rig session with the new lights (mostly done 2026-10-15: static/off work on the rig; BT timing and wash-vs-strobe measured, §13c-21; wash brightness and colours judged OK by the user, §13c-22; a wired-chain clip is optional) and the **live test of the dark drop** with all lights (user will judge `drop_blackout_s` 1.5 s and the 90 s ration).
4. **Taste tuning** of gobo palette (`show.gobo_palette`; available: open, ring, petals, star, mosaic, dots, swirl, slats), colours, flash/pulse levels, `drop_min_gap_s` (currently 10 s; ~100 blasts/h on the dance-heavy soak playlist).
5. **Publishing later (repo is private, MIT):** README with a short demo/gif and a "hardware you need" section, hardware-agnostic profile notes for hobbyists, `docs/` cleanup, check `pyproject` dependencies (PyYAML, numpy, rich; dev: librosa, matplotlib, av), Linux/PipeWire-only caveat, remove user-specific notes from the memo if desired.
6. **Raspberry Pi after the Yamaha WXC-50 (§13c-23):** green-lit design idea. First check the pre-amp's rear outputs (fixed or volume-following level), then do the cheap PC test with a USB audio interface, then the Pi 4 (measure CPU, delay, hum).
7. **Ideas, low priority:** browser GUI on top of the control API; 4/4-only limitation of the bar tracker; kick-path latency (~100 ms before the light delay) cannot be led (options: `audio.block_size` 512, predictive drive scene); tap-tempo; C++ DSP only if CPU ever becomes a problem (it would not fix latency).

### Gotchas worth remembering
- Latency measurements from click tracks repeat every click period: always confirm with two tempi (150 and 120 BPM); `tools/latency_analyze.py` now warns. The camera's A/V offset was calibrated (~0) with `tools/av_sync_flash.py`.
- Bluetooth speaker sound is ~254 ms later than wired; use the BT profile (`DMXREACTIVE_CONFIG=config/config_bt.yaml scripts/start_show.sh`) and restart after switching outputs.
- PicoSpot: speed channel inverted (0 = fast); dimmer is slew-limited (~170 ms full scale) so short flashes use its built-in strobe; tilt 230°, pan 540°; gobo wheel map in §13c-16. Pot Strobe: strobe period linear in DMX value (§6.2); its dimmer is crisp (4 ms).
- The node outputs at 40 Hz; our render rate is 40 Hz; Art-Net universe is 1 (node default).
- Photosensitivity: flashing is capped; warn guests at events; the `s` key turns all flashing off; the `m` key cycles the wear-free modes.
- Local-only (git-ignored): `config/config.yaml`, `config/config_bt.yaml`, `support/` media, `support/tracks/`, soak logs.

### Commands cheat-sheet
`dmxreactive check-config | preflight [--walk] | run [--scene …] [--flash-path grid|kick] [--mechanics full|static|off] | camera-test derby-motor|wash-ramp|wash-pulse | analyze | meter | list-devices | record | sim | test-fixtures | hold | strobe-sweep | pulse-test | gobo-walk`. `scripts/start_show.sh`, `scripts/install_desktop_entry.sh`, `scripts/setup_dongle.sh`, `scripts/net_profile.sh`, `scripts/setup_dev.sh`. Tools: `analyze_track.py`, `simulate_show.py`, `bar_check.py`, `click_eval.py`, `pipeline_latency.py`, `latency_analyze.py`, `make_click_track.py`, `av_sync_flash.py`, `strobe_analyze.py`, `video_luma.py`, `soak.py`, `jitter_check.py`, `predrop_features.py`, `predrop_eval.py`.
