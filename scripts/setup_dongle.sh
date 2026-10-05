#!/usr/bin/env bash
# Give the USB-Ethernet dongle a static IP on the Art-Net node's subnet (NetworkManager).
# Usage: scripts/setup_dongle.sh [interface]   (interface auto-detected if exactly one ethernet device)
# Env:   DMX_HOST_IP (default 192.168.1.10/24), DMX_CON_NAME (default dmx)
set -euo pipefail

IP="${DMX_HOST_IP:-192.168.1.10/24}"
NAME="${DMX_CON_NAME:-dmx}"
IFACE="${1:-}"

if [[ -z "$IFACE" ]]; then
    mapfile -t devs < <(nmcli -t -f DEVICE,TYPE device | awk -F: '$2=="ethernet"{print $1}')
    if [[ ${#devs[@]} -ne 1 ]]; then
        echo "Found ${#devs[@]} ethernet devices; plug in only the dongle or pass the interface name:" >&2
        nmcli -t -f DEVICE,TYPE,STATE device >&2
        exit 1
    fi
    IFACE="${devs[0]}"
fi

echo "Configuring $IFACE as $IP (connection '$NAME', no default route, Wi-Fi untouched)"
nmcli -t -f NAME connection show | grep -qx "$NAME" && nmcli connection delete "$NAME" >/dev/null
nmcli connection add type ethernet ifname "$IFACE" con-name "$NAME" \
    ipv4.method manual ipv4.addresses "$IP" ipv4.never-default yes \
    ipv6.method disabled connection.autoconnect yes >/dev/null
nmcli connection up "$NAME"
ip -br addr show "$IFACE"
echo "Next: ping -c3 192.168.1.20  (the node's factory IP)"
