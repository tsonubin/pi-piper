from __future__ import annotations

import json
import logging
import subprocess
import sys
from pathlib import Path

import click

from pi_piper import __version__
from pi_piper.audio import list_capture_devices
from pi_piper.audio.formats import alsa_format_bits
from pi_piper.config import Config, ConfigError
from pi_piper.sinks.base import SinkError


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )


@click.group()
@click.version_option(__version__)
@click.option("--config", "config_path", type=click.Path(path_type=Path), default=None)
@click.option("-v", "--verbose", is_flag=True)
@click.pass_context
def main(ctx: click.Context, config_path: Path | None, verbose: bool) -> None:
    """pi-piper: stream analog line-in to Bluetooth, AirPlay, or Sonos."""
    _setup_logging(verbose)
    ctx.ensure_object(dict)
    try:
        ctx.obj["cfg"] = Config.load(config_path)
    except ConfigError as e:
        raise click.ClickException(str(e)) from e
    ctx.obj["cfg_path"] = config_path


@main.command("status")
@click.pass_context
def cmd_status(ctx: click.Context) -> None:
    """Show the active config and resolved targets."""
    cfg: Config = ctx.obj["cfg"]
    click.echo(f"backend: {cfg.backend}")
    click.echo(
        f"input: {cfg.input.device} {cfg.input.rate}Hz "
        f"{cfg.input.channels}ch {cfg.input.format}"
    )
    click.echo(f"bluetooth target: {cfg.bluetooth.target_mac or '(unset)'}")
    click.echo(f"airplay target: {cfg.airplay.target_name or cfg.airplay.target_id or '(unset)'}")
    click.echo(f"sonos target: {cfg.sonos.target_name or cfg.sonos.target_uid or '(unset)'}")


@main.command("backend")
@click.argument("name", type=click.Choice(["bluetooth", "airplay", "sonos"]))
@click.pass_context
def cmd_backend(ctx: click.Context, name: str) -> None:
    """Set the active output backend."""
    cfg: Config = ctx.obj["cfg"]
    cfg.backend = name
    path = cfg.save(ctx.obj.get("cfg_path"))
    click.echo(f"backend set to {name} ({path})")


@main.group("input")
def grp_input() -> None:
    """Manage the analog audio input device."""


@grp_input.command("list")
def cmd_input_list() -> None:
    """List ALSA capture devices."""
    try:
        devs = list_capture_devices()
    except FileNotFoundError as e:
        raise click.ClickException(str(e)) from e
    if not devs:
        click.echo("no capture devices found (run `arecord -l`)", err=True)
        sys.exit(1)
    for d in devs:
        click.echo(f"{d.plughw}\t{d.card_name} / {d.device_name}")


@grp_input.command("set")
@click.argument("device")
@click.option("--rate", type=int, default=None)
@click.option("--channels", type=int, default=None)
@click.option("--format", "fmt", default=None)
@click.pass_context
def cmd_input_set(
    ctx: click.Context, device: str, rate: int | None, channels: int | None, fmt: str | None
) -> None:
    """Configure the ALSA capture device."""
    cfg: Config = ctx.obj["cfg"]
    cfg.input.device = device
    if rate is not None:
        cfg.input.rate = rate
    if channels is not None:
        cfg.input.channels = channels
    if fmt is not None:
        try:
            alsa_format_bits(fmt)
        except ValueError as e:
            raise click.ClickException(str(e)) from e
        cfg.input.format = fmt
    path = cfg.save(ctx.obj.get("cfg_path"))
    click.echo(f"saved input config to {path}")


@main.group("bluetooth")
def grp_bt() -> None:
    """Bluetooth A2DP source pairing and selection."""


@grp_bt.command("scan")
@click.option("--duration", type=float, default=8.0)
@click.pass_context
def cmd_bt_scan(ctx: click.Context, duration: float) -> None:
    """Discover nearby Bluetooth devices."""
    from pi_piper.sinks.bluetooth import BluetoothCtl
    cfg: Config = ctx.obj["cfg"]
    try:
        ctl = BluetoothCtl(cfg.bluetooth.adapter)
        click.echo(f"scanning for {duration:.0f}s...")
        devs = ctl.scan(duration)
    except (SinkError, subprocess.TimeoutExpired) as e:
        raise click.ClickException(str(e)) from e
    if not devs:
        click.echo("no devices found")
        return
    for d in devs:
        flags = []
        if d.paired:
            flags.append("paired")
        if d.connected:
            flags.append("connected")
        suffix = f" [{','.join(flags)}]" if flags else ""
        click.echo(f"{d.mac}\t{d.name}{suffix}")


@grp_bt.command("pair")
@click.argument("mac")
@click.option("--connect/--no-connect", default=True)
@click.pass_context
def cmd_bt_pair(ctx: click.Context, mac: str, connect: bool) -> None:
    """Pair (and optionally connect) a Bluetooth speaker by MAC."""
    from pi_piper.sinks.bluetooth import BluetoothCtl
    cfg: Config = ctx.obj["cfg"]
    try:
        ctl = BluetoothCtl(cfg.bluetooth.adapter)
        ctl.pair(mac)
        if connect:
            ctl.connect(mac)
    except (SinkError, subprocess.TimeoutExpired) as e:
        raise click.ClickException(str(e)) from e
    cfg.bluetooth.target_mac = mac
    path = cfg.save(ctx.obj.get("cfg_path"))
    click.echo(f"paired {mac}; saved to {path}")


@grp_bt.command("use")
@click.argument("mac")
@click.pass_context
def cmd_bt_use(ctx: click.Context, mac: str) -> None:
    """Mark a previously paired device as the active Bluetooth target."""
    cfg: Config = ctx.obj["cfg"]
    cfg.bluetooth.target_mac = mac
    path = cfg.save(ctx.obj.get("cfg_path"))
    click.echo(f"set bluetooth target to {mac} ({path})")


@grp_bt.command("connect")
@click.pass_context
def cmd_bt_connect(ctx: click.Context) -> None:
    """Connect to the configured Bluetooth target."""
    from pi_piper.sinks.bluetooth import BluetoothCtl
    cfg: Config = ctx.obj["cfg"]
    if not cfg.bluetooth.target_mac:
        click.echo("no target configured; run `pi-piper bluetooth pair <MAC>`", err=True)
        sys.exit(1)
    try:
        ctl = BluetoothCtl(cfg.bluetooth.adapter)
        ctl.connect(cfg.bluetooth.target_mac)
    except (SinkError, subprocess.TimeoutExpired) as e:
        raise click.ClickException(str(e)) from e
    click.echo(f"connected {cfg.bluetooth.target_mac}")


@grp_bt.command("disconnect")
@click.pass_context
def cmd_bt_disconnect(ctx: click.Context) -> None:
    """Disconnect the configured Bluetooth target."""
    from pi_piper.sinks.bluetooth import BluetoothCtl
    cfg: Config = ctx.obj["cfg"]
    if not cfg.bluetooth.target_mac:
        click.echo("no target configured", err=True)
        sys.exit(1)
    try:
        ctl = BluetoothCtl(cfg.bluetooth.adapter)
        ctl.disconnect(cfg.bluetooth.target_mac)
    except (SinkError, subprocess.TimeoutExpired) as e:
        raise click.ClickException(str(e)) from e
    click.echo(f"disconnected {cfg.bluetooth.target_mac}")


@main.group("airplay")
def grp_airplay() -> None:
    """AirPlay receiver discovery and selection."""


@grp_airplay.command("scan")
@click.option("--timeout", type=float, default=5.0)
@click.option("--json", "as_json", is_flag=True)
def cmd_airplay_scan(timeout: float, as_json: bool) -> None:
    """Discover AirPlay receivers via mDNS."""
    from pi_piper.sinks.airplay import scan_airplay
    try:
        devices = scan_airplay(timeout)
    except SinkError as e:
        raise click.ClickException(str(e)) from e
    if as_json:
        click.echo(json.dumps(devices, indent=2))
        return
    if not devices:
        click.echo("no AirPlay devices found")
        return
    for d in devices:
        click.echo(f"{d['id']}\t{d['name']}\t{d['address']}\t{d['model']}")


@grp_airplay.command("use")
@click.argument("identifier")
@click.option("--name", default=None, help="Match by name instead of identifier")
@click.pass_context
def cmd_airplay_use(ctx: click.Context, identifier: str, name: str | None) -> None:
    """Set the active AirPlay target."""
    cfg: Config = ctx.obj["cfg"]
    if name:
        cfg.airplay.target_name = name
        cfg.airplay.target_id = None
    else:
        cfg.airplay.target_id = identifier
        cfg.airplay.target_name = None
    path = cfg.save(ctx.obj.get("cfg_path"))
    click.echo(f"set airplay target ({path})")


@grp_airplay.command("credentials")
@click.argument("protocol")
@click.argument("creds")
@click.pass_context
def cmd_airplay_credentials(ctx: click.Context, protocol: str, creds: str) -> None:
    """Store pairing credentials for an AirPlay protocol (raop, airplay, ...)."""
    cfg: Config = ctx.obj["cfg"]
    cfg.airplay.credentials[protocol.lower()] = creds
    path = cfg.save(ctx.obj.get("cfg_path"))
    click.echo(f"saved {protocol} credentials ({path})")


@main.group("sonos")
def grp_sonos() -> None:
    """Sonos zone discovery and selection."""


@grp_sonos.command("scan")
@click.option("--json", "as_json", is_flag=True)
def cmd_sonos_scan(as_json: bool) -> None:
    """Discover Sonos zones on the LAN."""
    from pi_piper.sinks.sonos import discover_sonos
    try:
        devices = discover_sonos()
    except SinkError as e:
        raise click.ClickException(str(e)) from e
    if as_json:
        click.echo(json.dumps(devices, indent=2))
        return
    if not devices:
        click.echo("no Sonos zones found")
        return
    for d in devices:
        click.echo(f"{d['uid']}\t{d['name']}\t{d['ip']}")


@grp_sonos.command("use")
@click.argument("identifier")
@click.option("--name", default=None, help="Match by zone name instead of UID")
@click.pass_context
def cmd_sonos_use(ctx: click.Context, identifier: str, name: str | None) -> None:
    """Set the active Sonos target."""
    cfg: Config = ctx.obj["cfg"]
    if name:
        cfg.sonos.target_name = name
        cfg.sonos.target_uid = None
    else:
        cfg.sonos.target_uid = identifier
        cfg.sonos.target_name = None
    path = cfg.save(ctx.obj.get("cfg_path"))
    click.echo(f"set sonos target ({path})")


@main.group("service")
def grp_service() -> None:
    """Manage the pi-piper systemd service."""


@grp_service.command("start")
def cmd_svc_start() -> None:
    subprocess.run(["systemctl", "start", "pi-piper.service"], check=False)


@grp_service.command("stop")
def cmd_svc_stop() -> None:
    subprocess.run(["systemctl", "stop", "pi-piper.service"], check=False)


@grp_service.command("restart")
def cmd_svc_restart() -> None:
    subprocess.run(["systemctl", "restart", "pi-piper.service"], check=False)


@grp_service.command("enable")
def cmd_svc_enable() -> None:
    subprocess.run(["systemctl", "enable", "--now", "pi-piper.service"], check=False)


@grp_service.command("disable")
def cmd_svc_disable() -> None:
    subprocess.run(["systemctl", "disable", "--now", "pi-piper.service"], check=False)


@grp_service.command("status")
def cmd_svc_status() -> None:
    subprocess.run(["systemctl", "status", "pi-piper.service", "--no-pager"], check=False)


@grp_service.command("logs")
@click.option("-f", "--follow", is_flag=True)
def cmd_svc_logs(follow: bool) -> None:
    cmd = ["journalctl", "-u", "pi-piper.service", "--no-pager"]
    if follow:
        cmd.append("-f")
    subprocess.run(cmd, check=False)


@main.command("run")
@click.pass_context
def cmd_run(ctx: click.Context) -> None:
    """Run the pipeline in the foreground (useful for debugging)."""
    from pi_piper.service import run
    cfg: Config = ctx.obj["cfg"]
    sys.exit(run(cfg))


if __name__ == "__main__":
    main()
