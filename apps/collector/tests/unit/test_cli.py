"""Unit tests for the collector CLI argument handling and modes."""

from pathlib import Path

import pytest
from tokemetry_collector.cli import main

_CONFIG = """
server_url = "http://server"
api_token = "tkm_token"
machine_name = "box-1"
"""


def _write_config(tmp_path: Path) -> Path:
    path = tmp_path / "collector.toml"
    state_db = tmp_path / "state.sqlite3"
    path.write_text(
        _CONFIG + f'state_db_path = "{state_db.as_posix()}"\n', encoding="utf-8"
    )
    return path


def test_dry_run_exits_clean_without_network(tmp_path: Path) -> None:
    # No sources are registered, so a dry run does no work and no HTTP.
    config = _write_config(tmp_path)
    assert main(["--config", str(config), "--dry-run"]) == 0


def test_once_exits_clean_with_empty_queue(tmp_path: Path) -> None:
    config = _write_config(tmp_path)
    assert main(["--config", str(config), "--once"]) == 0


def test_config_flag_is_required() -> None:
    with pytest.raises(SystemExit):
        main([])
