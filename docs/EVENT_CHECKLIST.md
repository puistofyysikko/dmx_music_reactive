# Event checklist — DMX Music Reactive

Print this or keep it open on the phone. Estimated time: 15 minutes before guests arrive.

## Setup (30 min before)
1. **Place the fixtures.** PicoSpot where the beam cannot reach faces (its pan/tilt limits are in
   `config/config.yaml`: `show.pan_limits`, `show.tilt_limits`); the strobe light aimed at a wall or
   ceiling, never at seated guests. Both secured against being knocked over.
2. **DMX chain:** node → 10 m cable → PicoSpot (DMX IN→OUT) → 5 m cable → LED Pot Strobe →
   **120 Ω terminator in the last DMX OUT**. Fixtures powered; PicoSpot set to 9-channel mode
   `A001`, Pot Strobe `d.010`, 3-channel mode.
3. **Node:** powered over USB-C (phone charger); Ethernet to the USB dongle in the laptop.
4. **Laptop:** plugged into mains; power profile "performance"; screen lock / sleep / notifications
   off (KDE: Do Not Disturb); close browsers' extra tabs.
5. **Audio:** laptop output → speakers/mixer. **Wired or USB audio is best.** A Bluetooth speaker
   delays the sound by ~250 ms: start with `DMXREACTIVE_CONFIG=config/config_bt.yaml scripts/start_show.sh`
   (light delay 315 ms instead of the wired value 60 ms).

## Checks (5 min)
6. `scripts/start_show.sh` (or the "DMX Music Reactive" launcher) runs the **pre-flight**:
   network, node answers an ArtPoll and its universe matches, audio capture works.
   Fix anything marked ✘ (the hint under each line says how).
7. `.venv/bin/dmxreactive preflight --walk` steps every fixture through its functions: head moves and
   changes colour, strobe light flashes gently, everything ends dark. If a fixture does not respond:
   address/mode on its display, cable ends, terminator.
8. Play a track, start the show, watch the status panel: BPM and LOCKED show up after ~10 s on
   beat-driven music; `b` blackout works and Ctrl-C leaves everything dark.

## During the party
- `b` blackout (instantly dark, press again to resume) · `s` all flashing on/off ·
  `+`/`-` brightness · `a` auto scene · `1` beat · `2` ambient · `3` drive.
- Warn guests: **flashing lights** (photosensitivity). Flashing is capped at 8 per second
  (`show.max_flash_hz`, hard limit 10) and the `s` key turns all flashing off at once.
- Karaoke: microphones plugged into the mixer are not heard by the laptop; the lights follow the
  backing track only (this is intended).

## If something goes wrong
- **Lights frozen / stuck:** Ctrl-C (sends blackout frames) and restart with the launcher.
- **No audio in the panel ("no audio from the capture"):** is the player on the default output?
  `dmxreactive list-devices` shows which monitor source is captured; unplugging/replugging the
  output device is handled automatically within ~2 s.
- **Fixtures dark but the panel shows packets:** check the DMX cables/terminator and the fixture
  addresses; run the walk.
- **Network lost mid-show:** the show keeps running and recovers by itself when the link returns.
- **Spare plan:** QLC+ (`dnf install qlcplus`) with a static scene on universe 1, in case the laptop
  misbehaves.

## Packing list
USB-Ethernet dongle · Ethernet cable · phone charger + USB-C cable for the node · DMX cables
(10 m + 5 m) · 120 Ω terminator · 3↔5-pin adapters · spare DMX cable · gaffer tape · switched
power strip · laptop charger · audio cable to the mixer.
