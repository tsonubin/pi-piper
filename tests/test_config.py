"""Config path resolution: reads and writes must agree on which file wins."""

from __future__ import annotations

from pathlib import Path

import pytest

import pi_piper.config
from pi_piper.config import Config


@pytest.fixture
def paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    system = tmp_path / "etc" / "pi-piper" / "config.yaml"
    user = tmp_path / "home" / ".config" / "pi-piper" / "config.yaml"
    monkeypatch.setattr(pi_piper.config, "DEFAULT_SYSTEM_CONFIG", system)
    monkeypatch.setattr(pi_piper.config, "DEFAULT_USER_CONFIG", user)
    monkeypatch.delenv("PI_PIPER_CONFIG", raising=False)
    return system, user


def _write(path: Path, backend: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"backend: {backend}\n", encoding="utf-8")


def test_existing_user_config_overrides_system(paths: tuple[Path, Path]) -> None:
    system, user = paths
    _write(system, "sonos")
    _write(user, "airplay")
    assert Config.load().backend == "airplay"


def test_system_config_used_when_no_user_config(paths: tuple[Path, Path]) -> None:
    system, _user = paths
    _write(system, "sonos")
    assert Config.load().backend == "sonos"


def test_defaults_when_no_config_exists(paths: tuple[Path, Path]) -> None:
    assert Config.load().backend == "bluetooth"


def test_env_var_wins_for_load_and_save(
    paths: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    system, _user = paths
    _write(system, "sonos")
    env_path = tmp_path / "override.yaml"
    _write(env_path, "airplay")
    monkeypatch.setenv("PI_PIPER_CONFIG", str(env_path))
    cfg = Config.load()  # env file replaces the system config entirely
    assert cfg.backend == "airplay"
    cfg.backend = "bluetooth"
    cfg.save()
    assert "bluetooth" in env_path.read_text(encoding="utf-8")
    assert Config.load().backend == "bluetooth"


def test_unwritable_system_config_roundtrip(
    paths: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The regression this precedence fix exists for: a non-root save must be
    visible to the next load instead of being shadowed by /etc."""
    system, user = paths
    _write(system, "bluetooth")
    monkeypatch.setattr(
        pi_piper.config.os, "access", lambda _p, _m: False
    )

    cfg = Config.load()  # reads the system config
    assert cfg.backend == "bluetooth"
    cfg.backend = "sonos"
    saved = cfg.save()  # not writable -> user file
    assert saved == user

    assert Config.load().backend == "sonos", "saved change must survive a reload"


def test_unknown_keys_are_ignored_not_fatal(paths: tuple[Path, Path]) -> None:
    system, _user = paths
    system.parent.mkdir(parents=True, exist_ok=True)
    system.write_text(
        "backend: airplay\n"
        "backand: typo\n"  # unknown top-level key
        "input:\n"
        "  device: plughw:2,0\n"
        "  ratee: 48000\n"  # unknown section key
        "  rate: 48000\n",
        encoding="utf-8",
    )
    cfg = Config.load()
    assert cfg.backend == "airplay"
    assert cfg.input.device == "plughw:2,0"
    assert cfg.input.rate == 48000  # known keys still apply


def test_non_mapping_section_falls_back_to_defaults(paths: tuple[Path, Path]) -> None:
    system, _user = paths
    system.parent.mkdir(parents=True, exist_ok=True)
    system.write_text("input: not-a-mapping\n", encoding="utf-8")
    cfg = Config.load()
    assert cfg.input.device == "default"
    assert cfg.input.rate == 44100


def test_saved_config_is_not_world_readable(paths: tuple[Path, Path]) -> None:
    import stat

    _system, user = paths
    saved = Config().save(user)  # no explicit path resolution: new user file
    assert saved == user
    assert stat.S_IMODE(saved.stat().st_mode) == 0o600
