"""Shared fixtures. ``network`` tests are skipped unless ``--run-network`` is given, and every test
runs with the shell's Settings variables (``APP_ENV``, ``CORS_ORIGINS``, …) removed."""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

import pytest

from airbrief_core.data import DataPack


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--run-network",
        action="store_true",
        default=False,
        help="Run tests marked 'network' (they download real data).",
    )


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "native: requires the optional airbrief_native C++ extension")
    config.addinivalue_line("markers", "network: touches the internet; skipped unless --run-network")
    config.addinivalue_line("markers", "slow: slow test")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if config.getoption("--run-network"):
        return
    skip_network = pytest.mark.skip(reason="network test: pass --run-network to run")
    for item in items:
        if "network" in item.keywords:
            item.add_marker(skip_network)


@pytest.fixture(autouse=True)
def _settings_env_isolated(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the shell's environment out of :class:`api.config.Settings` in every test.

    pydantic-settings reads process variables even with ``_env_file=None``, and RUNBOOK §5.5 has
    you set ``APP_ENV``/``CORS_ORIGINS`` in the shell, which would flip the defaults under test.
    """
    try:
        from api.config import Settings
    except ImportError:  # core-only install (no API extras): nothing to isolate
        return
    fields = {name.lower() for name in Settings.model_fields}
    for key in list(os.environ):
        if key.lower() in fields:
            monkeypatch.delenv(key, raising=False)


@pytest.fixture(scope="session")
def synthetic_pack_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Directory containing the synthetic pack folder ``demo`` (use as ``DATAPACK_DIR``)."""
    from seed.datapack import build_synthetic_pack

    parent = tmp_path_factory.mktemp("datapacks")
    build_synthetic_pack(parent, "demo")
    return parent


@pytest.fixture(scope="session")
def synthetic_pack(synthetic_pack_dir: Path) -> DataPack:
    """The deterministic synthetic data pack (name ``demo``), built once per session."""
    return DataPack(synthetic_pack_dir / "demo")


@pytest.fixture(scope="session")
def sample_bytes() -> Callable[[str], bytes]:
    """``sample_bytes(name)`` → bytes of the built-in sample ``name`` (deterministic)."""
    from airbrief_core.samples import build_sample

    cache: dict[str, bytes] = {}

    def _get(name: str) -> bytes:
        if name not in cache:
            cache[name] = build_sample(name)
        return cache[name]

    return _get
