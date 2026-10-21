"""Config path resolution: reads and writes must agree on which file wins."""

from __future__ import annotations

import stat
from pathlib import Path

import pytest

import pi_piper.config
from pi_piper.config import Config, ConfigError


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
    _system, user = paths
    saved = Config().save(user)  # no explicit path resolution: new user file
    assert saved == user
    assert stat.S_IMODE(saved.stat().st_mode) == 0o600


def test_invalid_yaml_raises_config_error_naming_file(
    paths: tuple[Path, Path], tmp_path: Path
) -> None:
    broken = tmp_path / "broken.yaml"
    broken.write_text("backend: [unclosed\n", encoding="utf-8")
    with pytest.raises(ConfigError) as exc:
        Config.load(broken)
    assert str(broken) in str(exc.value)
    assert "invalid YAML" in str(exc.value)


def test_empty_config_file_loads_defaults(paths: tuple[Path, Path]) -> None:
    empty = paths[1]
    empty.parent.mkdir(parents=True, exist_ok=True)
    empty.touch()
    cfg = Config.load(empty)
    assert cfg.backend == "bluetooth"
    assert cfg.input.rate == 44100


def test_failed_save_leaves_existing_config_intact(
    paths: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    _system, user = paths
    Config().save(user)
    original = user.read_text(encoding="utf-8")

    def _boom(*_a: object, **_k: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr("pi_piper.config.yaml.safe_dump", _boom)
    with pytest.raises(OSError, match="disk full"):
        Config().save(user)

    assert user.read_text(encoding="utf-8") == original, "old config must survive"
    assert not user.with_name(user.name + ".tmp").exists(), "no temp file residue"


def test_save_preserves_existing_file_mode(paths: tuple[Path, Path]) -> None:
    _system, user = paths
    Config().save(user)
    user.chmod(0o640)
    Config().save(user)
    assert stat.S_IMODE(user.stat().st_mode) == 0o640


def test_wrong_typed_scalars_are_coerced(paths: tuple[Path, Path]) -> None:
    """Hand-edited YAML commonly quotes numbers or drops quotes; both must
    load as the declared type instead of crashing the daemon mid-run."""
    system, _user = paths
    system.parent.mkdir(parents=True, exist_ok=True)
    system.write_text(
        "bluetooth:\n  target_mac: 12345\n"  # int where str is declared
        "input:\n  rate: '48000'\n"  # digit string where int is declared
        "sonos:\n  stream_port: 8974.0\n",  # integral float where int is declared
        encoding="utf-8",
    )
    cfg = Config.load(system)
    assert cfg.bluetooth.target_mac == "12345"
    assert cfg.input.rate == 48000
    assert cfg.sonos.stream_port == 8974


def test_incoercible_values_fall_back_to_defaults(paths: tuple[Path, Path]) -> None:
    system, _user = paths
    system.parent.mkdir(parents=True, exist_ok=True)
    system.write_text(
        "log_level: 42\n"
        "input:\n  rate: fast\n"
        "bluetooth:\n  target_mac: {a: b}\n"
        "airplay:\n  credentials: nope\n",
        encoding="utf-8",
    )
    cfg = Config.load(system)
    assert cfg.log_level == "INFO"
    assert cfg.input.rate == 44100
    assert cfg.bluetooth.target_mac is None
    assert cfg.airplay.credentials == {}


def test_concurrent_saves_use_distinct_temporary_files(monkeypatch, tmp_path):
    """Two atomic saves must not unlink or rename each other's temp file."""
    import threading
    from concurrent.futures import ThreadPoolExecutor

    import yaml

    path = tmp_path / "config.yaml"
    barrier = threading.Barrier(2)
    original_dump = yaml.safe_dump

    def simultaneous_dump(*args, **kwargs):
        barrier.wait(timeout=5)
        return original_dump(*args, **kwargs)

    monkeypatch.setattr(yaml, "safe_dump", simultaneous_dump)
    first = Config(backend="airplay")
    second = Config(backend="sonos")
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(cfg.save, path) for cfg in (first, second)]
        for future in futures:
            assert future.result(timeout=5) == path
    assert Config.load(path).backend in {"airplay", "sonos"}
    assert not list(tmp_path.glob("*.tmp*"))
