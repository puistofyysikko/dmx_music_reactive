# dmx_music_reactive

Music-reactive DMX lighting. Audio is captured from the sound card's output monitor (any player works), analysed in real time, and converted to lighting cues sent over Art-Net to a DMX node.

Target rig: Eurolite DXT PoE Art-Net Node I, Fun Generation PicoSpot 20 LED (moving head) and LED Pot Strobe 100. Linux (Fedora KDE) only. Full plan and fixture channel maps: [PROJECT_MEMO.md](PROJECT_MEMO.md).

**Status:** Phase 0–1 done and hardware-verified (Art-Net output, fixture profiles, fake node, channel walker, strobe calibration tools). Phase 3 (audio capture) and Phase 4 (analysis: bands, kicks, tempo/beat tracking) work; Phase 5 (effects engine + live runtime) is done in software and tested against a fake node; real-rig tuning and launcher/preflight/status panel are done; the event checklist is in `docs/EVENT_CHECKLIST.md`.

## Setup

```bash
scripts/setup_dev.sh          # creates .venv, installs the package + dev tools
cp config/config.example.yaml config/config.yaml   # then edit node IP / patch
```

## Usage

```bash
.venv/bin/dmxreactive check-config                  # validate config, print the patch
.venv/bin/dmxreactive sim                           # fake Art-Net node (no hardware needed)
.venv/bin/dmxreactive test-fixtures --target 127.0.0.1   # walk all channels against the sim
.venv/bin/dmxreactive test-fixtures                 # same, against the real node (strobe <= 3 Hz)
.venv/bin/dmxreactive list-devices                  # audio sources (default output's monitor is marked)
.venv/bin/dmxreactive meter                         # live level of whatever the laptop is playing
.venv/bin/dmxreactive preflight                     # checks: network, node (ArtPoll), audio, config
scripts/start_show.sh                               # one-click: pre-flight, then the live show panel
scripts/install_desktop_entry.sh                    # optional KDE launcher "DMX Music Reactive"
.venv/bin/dmxreactive run                           # LIVE SHOW: music from the laptop -> lights (b = blackout)
.venv/bin/dmxreactive run --file song.mp3 --target 127.0.0.1:16454   # same, from a file, into the sim
.venv/bin/dmxreactive analyze                       # live view: bands, BPM, beat/kick markers
.venv/bin/dmxreactive analyze --file song.mp3       # same on a file, played in real time
.venv/bin/python tools/analyze_track.py song.mp3 --plot out.png --reference   # offline report (needs librosa, matplotlib)
.venv/bin/pytest                                    # tests (~45 s)
.venv/bin/ruff check .                              # lint
.venv/bin/python tools/jitter_check.py 60           # send-timing check
```

Fixtures are JSON profiles in `config/fixtures/`; the patch (which fixture at which DMX address) is in the config file.

## Safety

Strobe rates are capped in the fixture profiles (`safe_max_hz`, default 10 Hz), and every shutdown path (normal exit, Ctrl-C, SIGTERM, exception) sends blackout frames. Strobe lights can trigger seizures in photosensitive people.
