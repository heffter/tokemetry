"""The one-command collector installers stay consistent with what they install.

The installers (deploy/collector/install.sh and install.ps1) copy the example
config, decide the config is "ready" by checking the placeholder token was
replaced, and install the package from a fixed git spec. Those couplings live in
separate files, so guard them here: if the example's placeholder token changes,
or the package/subdirectory moves, an installer would silently misbehave.
"""

from pathlib import Path

_ROOT = Path(__file__).parents[4]
_DEPLOY = _ROOT / "deploy"
_INSTALL_SH = _DEPLOY / "collector" / "install.sh"
_INSTALL_PS1 = _DEPLOY / "collector" / "install.ps1"
_EXAMPLE = _DEPLOY / "collector.example.toml"

# The placeholder token shipped in the example config; the installers treat a
# config still containing it as "not ready" and refuse to start the service.
_PLACEHOLDER = "tkm_replace_me"


def test_both_installers_exist() -> None:
    assert _INSTALL_SH.is_file()
    assert _INSTALL_PS1.is_file()


def test_example_config_carries_the_readiness_placeholder() -> None:
    # If this sentinel changes, config_ready()/Test-ConfigReady must change too.
    assert _PLACEHOLDER in _EXAMPLE.read_text(encoding="utf-8")


def test_installers_key_readiness_on_the_placeholder() -> None:
    for script in (_INSTALL_SH, _INSTALL_PS1):
        assert _PLACEHOLDER in script.read_text(encoding="utf-8"), (
            f"{script.name} does not reference the '{_PLACEHOLDER}' placeholder"
        )


def test_installers_reference_the_example_config() -> None:
    for script in (_INSTALL_SH, _INSTALL_PS1):
        assert "collector.example.toml" in script.read_text(encoding="utf-8")


def test_installers_share_one_git_source_spec() -> None:
    # Both platforms must install the same package from the same subdirectory.
    spec = "git+https://github.com/heffter/tokemetry.git#subdirectory=apps/collector"
    for script in (_INSTALL_SH, _INSTALL_PS1):
        assert spec in script.read_text(encoding="utf-8")
