#!/usr/bin/env bash
# Switch the USB-Ethernet dongle between stored NetworkManager profiles.
# Usage: scripts/net_profile.sh dmx|internet|status [interface]
#   dmx       static 192.168.1.10/24 for the Art-Net node (created by setup_dongle.sh), manual only
#   internet  DHCP, the autoconnect default when the dongle is plugged in
# Both profiles are created on first use; re-running is safe and also re-applies autoconnect settings.
set -euo pipefail

MODE="${1:-status}"
IFACE="${2:-}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

have() { nmcli -t -f NAME connection show | grep -qx "$1"; }

detect_iface() {
    if [[ -n "$IFACE" ]]; then return; fi
    for con in dmx internet; do
        if have "$con"; then
            IFACE="$(nmcli -g connection.interface-name connection show "$con")"
            [[ -n "$IFACE" ]] && return
        fi
    done
    mapfile -t devs < <(nmcli -t -f DEVICE,TYPE device | awk -F: '$2=="ethernet"{print $1}')
    if [[ ${#devs[@]} -ne 1 ]]; then
        echo "Found ${#devs[@]} ethernet devices; plug in only the dongle or pass the interface name:" >&2
        nmcli -t -f DEVICE,TYPE,STATE device >&2
        exit 1
    fi
    IFACE="${devs[0]}"
}

ensure_profiles() {
    detect_iface
    have dmx || "$HERE/setup_dongle.sh" "$IFACE"
    if ! have internet; then
        nmcli connection add type ethernet ifname "$IFACE" con-name internet \
            ipv4.method auto ipv6.method auto >/dev/null
    fi
    # internet is the default on plug-in; dmx is switched on by hand
    nmcli connection modify internet connection.autoconnect yes connection.autoconnect-priority 10
    nmcli connection modify dmx connection.autoconnect no
}

case "$MODE" in
    dmx|internet)
        ensure_profiles
        nmcli connection up "$MODE"
        ip -br addr show "$IFACE"
        [[ "$MODE" == dmx ]] && echo "Next: dmxreactive preflight   (node at 192.168.1.20)"
        ;;
    status)
        nmcli -f NAME,DEVICE,STATE connection show | grep -E 'NAME|dmx|internet' || true
        ;;
    *)
        echo "Usage: $0 dmx|internet|status [interface]" >&2
        exit 2
        ;;
esac
