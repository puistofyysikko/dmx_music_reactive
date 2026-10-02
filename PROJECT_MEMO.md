# DMX Music-Reactive Lighting — Project Memo (v2)

Status: **planning** · Branch: `create/foundation` · Supersedes: `dmx_music_reactive_project_plan.md` (kept as the original draft)

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
| Audio capture | `sounddevice` + WASAPI/PulseAudio | **Linux only.** `sounddevice` (or `soundcard`) on the PipeWire/PulseAudio *monitor* source of the default sink; behind an `AudioSource` interface with a WAV-file fallback. | Windows dropped; the abstraction stays so the library can be swapped and tests can use files. |
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

### 6.3 Patch summary

| Universe 0 | Fixture | Mode | Channels |
|-----------|---------|------|----------|
| 1–9 | PicoSpot 20 LED | 9-ch | pan, tilt, pan-fine, tilt-fine, speed, colour, gobo, dimmer, strobe |
| 10–12 | LED Pot Strobe 100 | 3-ch | dimmer, mode (=0), strobe |
| 13–99 | free (movers block) | | |
| 100–199 | reserved: washes/PARs | | |
| 200–299 | reserved: effects (derby) | | |
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

### Phase 3 — Audio capture
- `AudioSource` implementations for PipeWire/Pulse monitor, file, synthetic; device listing CLI; level meter script.
- Spike first: confirm monitor-source capture of YouTube/karaoke audio on the laptop with `sounddevice`, and that it is independent of the output device (speaker/headphones/mixer).
- **Done when:** meter reacts to system playback with no dropouts for 30 min; callback never blocks.

### Phase 4 — Analysis
- Windowed FFT, bands, adaptive normalisation, onset detector, tempo/phase tracker.
- Offline harness vs. labelled tracks (build a small test set: 5–8 tracks across genres + click tracks).
- **Done when:** onset F-measure and beat hit-rate meet §1 targets on the test set; analysis uses < 15 % of a core; unit tests on synthetic click tracks pass (BPM ±1).

### Phase 5 — Effects + integration
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

## 13a. Node settings (verified 2026-10-02, firmware V2.2, screenshot in `support/node_config_defaults.png`)

Factory defaults kept: Static IP 192.168.1.20 / 255.255.255.0; Art-Net Net 0, SubNet 0, **Universe 1** (so our config uses `universe: 1`, port-address 1); Port 1 type **DMX Out 40 Hz** (node refreshes at 40 Hz; we send 30 Hz and it repeats the last frame in between). The node also has an sACN universe (= Art-Net address + 1) and a Light'J port, both unused.

## 13b. Implementation status

- Phase 0–1 implemented: `src/dmxreactive/dmx/` (universe, artnet, profiles, frameloop, fake_node), `config.py`, `app.py` (run loop with blackout on exit), `control/` (CLI `check-config` / `sim` / `test-fixtures`, channel walker). 25 tests pass, ruff clean.
- Measured on loopback (30 s): deadline error mean 0.001 ms / max 0.22 ms, no late ticks, no sequence gaps (10-minute soak not yet run).
- Strobe mappings measured with camera (§6.2); safety caps in Hz (`safe_max_hz`).
- Hardware verified 2026-10-02: node, both fixtures, channel walk, strobe rates. Still open: PicoSpot tilt range (200° vs 230°), gobo list, 10-minute jitter soak on the real network.

## 14. Immediate next steps

1. Phase 0: scaffold repo (`pyproject.toml`, layout from §7, `config.example.yaml` with the patch from §6.3).
2. Phase 1: Art-Net sender + fake node receiver + fixture profiles for the two fixtures (JSON from §6) + unit tests – no hardware needed.
3. In parallel: configure the dongle's static IP profile, power the node (USB-C), open its web page, confirm Net/SubNet/Universe, test with QLC+ (Fedora: `dnf install qlcplus`; it has Art-Net output and fixture definitions for both lights in its library or OFL).
4. Phase 3 spike: find the monitor source with `pactl list short sources`, check that a 30 s capture of YouTube works with `sounddevice`.
