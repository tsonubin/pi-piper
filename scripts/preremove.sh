#!/bin/sh
set -e

if [ -d /run/systemd/system ]; then
    systemctl stop pi-piper.service 2>/dev/null || true
    systemctl disable pi-piper.service 2>/dev/null || true
fi
