#!/usr/bin/env bash
# Install a KDE/GNOME launcher ("DMX Music Reactive") that opens a terminal and starts the show.
# Writes only ~/.local/share/applications/dmxreactive.desktop; remove that file to uninstall.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
target="${XDG_DATA_HOME:-$HOME/.local/share}/applications/dmxreactive.desktop"
mkdir -p "$(dirname "$target")"
if command -v konsole >/dev/null; then
    term="konsole --hold -e"
elif command -v gnome-terminal >/dev/null; then
    term="gnome-terminal --"
else
    term="xterm -hold -e"
fi
cat > "$target" <<DESKTOP
[Desktop Entry]
Type=Application
Name=DMX Music Reactive
Comment=Pre-flight checks, then the music-reactive light show
Exec=$term $root/scripts/start_show.sh
Path=$root
Terminal=false
Categories=AudioVideo;Audio;
DESKTOP
chmod +x "$target"
echo "Installed $target"
echo "Find 'DMX Music Reactive' in the application menu, or pin it to the panel/desktop."
