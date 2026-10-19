# pi-piper

Pipe analog audio (RCA / 3.5mm line-in via a USB sound card or audio HAT) from a
Raspberry Pi or OrangePi into a Bluetooth speaker, an AirPlay receiver, or a
Sonos zone. Ships as a CLI plus a systemd service.

## What's in the box

- `pi-piper` — CLI for input setup, target discovery, pairing, and service
  control.
- `pi-piperd` — long-running daemon (entry point used by the systemd unit).
- Three output backends, selectable at runtime:
  - **Bluetooth A2DP source** (BlueZ + PulseAudio/PipeWire).
  - **AirPlay** (pyatv; AirPlay 1/2 receivers including HomePod, Apple TV,
    shairport-sync).
  - **Sonos** (SoCo + an internal HTTP server that serves a live WAV stream
    the Sonos zone pulls from).

## Hardware

The Raspberry Pi family and OrangePi Zero 3 don't have a real line-in. Use a
USB ADC or an audio HAT and pick its capture device with `pi-piper input set`.
Anything ALSA can see in `arecord -l` works.

## Install

### Debian / Ubuntu / Armbian

```sh
sudo apt install ./pi-piper_0.1.0-1_all.deb
```

To build the deb yourself:

```sh
sudo apt install devscripts debhelper dh-python python3-all python3-setuptools
ln -s packaging/debian debian
dpkg-buildpackage -us -uc -b
```

### Arch / Manjaro (yay)

```sh
yay -S pi-piper      # once published to AUR
# or, locally:
cd packaging/arch && makepkg -si
```

### From source

```sh
pip install .
sudo install -m 0644 packaging/systemd/pi-piper.service /etc/systemd/system/
sudo install -d /etc/pi-piper
sudo install -m 0644 src/pi_piper/data/config.example.yaml /etc/pi-piper/config.yaml
sudo systemctl daemon-reload
```

## Configure

Config lives at `/etc/pi-piper/config.yaml` (system) or
`~/.config/pi-piper/config.yaml` (user). Precedence: `PI_PIPER_CONFIG`, then
an existing user config, then the system config — so `pi-piper` commands run
without sudo write to the user file, which then wins on every later run. The
systemd unit pins `PI_PIPER_CONFIG=/etc/pi-piper/config.yaml`, so the service
always reads the system file.

```sh
# 1. Pick your capture device.
pi-piper input list
sudo pi-piper input set plughw:1,0 --rate 44100 --channels 2

# 2. Pick a backend and a target.
sudo pi-piper backend bluetooth
sudo pi-piper bluetooth scan
sudo pi-piper bluetooth pair AA:BB:CC:DD:EE:FF

# or AirPlay
sudo pi-piper backend airplay
sudo pi-piper airplay scan
sudo pi-piper airplay use <identifier>

# or Sonos
sudo pi-piper backend sonos
sudo pi-piper sonos scan
sudo pi-piper sonos use <uid>

# 3. Run it.
sudo pi-piper service enable     # systemctl enable --now
sudo pi-piper service logs -f
```

For one-shot debugging without systemd:

```sh
sudo -u pi-piper pi-piper run
```

## How the backends work

- **Bluetooth**: `bluetoothctl` for pair/connect, then `paplay` writes the
  captured PCM into the BlueZ sink that PulseAudio/PipeWire creates.
- **AirPlay**: a private FIFO is created in the state dir; the daemon writes
  WAV-framed PCM to it, and pyatv streams the FIFO to the receiver. ffmpeg is
  pulled in by pyatv for transcoding.
- **Sonos**: the daemon hosts an HTTP server on `sonos.stream_port` (default
  8973) that serves the captured audio as `audio/wav`, then issues `play_uri`
  on the target zone so it pulls the stream.

## Service

The systemd unit runs as the `pi-piper` user with `audio` and `bluetooth`
supplementary groups, with `ProtectSystem=strict` and a writable state dir
under `/var/lib/pi-piper`.

```sh
pi-piper service start | stop | restart | enable | disable | status | logs [-f]
```

## Troubleshooting

- `paplay: Connection failure: Connection refused` — install `pipewire-pulse`
  (or `pulseaudio`) and make sure it runs as the `pi-piper` user. On a
  headless box you typically want PipeWire's system service or a per-user
  socket the service can reach.
- BT speaker pairs but no audio — check that
  `pulseaudio-bluetooth` / `libspa-0.2-bluetooth` is installed and
  `pactl list short sinks` shows a `bluez_*` sink after connection.
- AirPlay device asks for a PIN — pair once with
  `atvremote --id <id> pair`, then store the credentials with
  `pi-piper airplay credentials raop <creds>`.
- Sonos plays nothing — make sure the Pi's IP is reachable from the Sonos
  zone and `sonos.stream_port` is open in the firewall.

## Limitations

- AirPlay 2 multi-room requires a recent pyatv; some receivers are flaky.
- Sonos ignores arbitrary push streams, so latency is at the mercy of its
  buffering — expect ~1s.
- No transcoding is done for Bluetooth; the BT codec is whatever
  PulseAudio/PipeWire negotiates with the speaker (SBC by default).
- The Sonos and AirPlay backends stream 16-bit WAV, so `input.format` must
  be `S16_LE` for them; the Bluetooth backend passes any ALSA capture
  format through to paplay unchanged.

## Development

`uv` is the blessed toolchain (any venv + pip works too):

```sh
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -e '.[dev]'
.venv/bin/python -m pytest -q    # targeted tests live in tests/
.venv/bin/ruff check src tests
```

## License

MIT — see [LICENSE](./LICENSE).
