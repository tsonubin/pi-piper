#!/bin/sh
set -e

if ! getent group pi-piper >/dev/null; then
    groupadd --system pi-piper
fi

if ! id pi-piper >/dev/null 2>&1; then
    useradd --system \
        --gid pi-piper \
        --groups audio,bluetooth \
        --home-dir /var/lib/pi-piper \
        --shell /usr/sbin/nologin \
        pi-piper
fi

install -d -m 0755 -o pi-piper -g pi-piper /var/lib/pi-piper
install -d -m 0755 /etc/pi-piper

if [ ! -f /etc/pi-piper/config.yaml ]; then
    if [ -f /usr/share/pi-piper/config.example.yaml ]; then
        install -m 0644 /usr/share/pi-piper/config.example.yaml /etc/pi-piper/config.yaml
    fi
fi

if [ -d /run/systemd/system ]; then
    systemctl daemon-reload || true
fi
