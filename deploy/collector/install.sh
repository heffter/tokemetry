#!/usr/bin/env bash
#
# One-command installer for the tokemetry collector on Linux and macOS.
#
# Installs uv (if missing) and the collector, scaffolds the config, and
# registers the platform service: a systemd user unit on Linux, a launchd agent
# on macOS. Run it from a clone of the repository, as the user who runs Claude
# Code (the collector reads that user's ~/.claude).
#
# See usage() below (or --help) for options. The service is registered and
# started only when the config is complete (a real --token was supplied).

set -euo pipefail

readonly GIT_SPEC="tokemetry-collector @ git+https://github.com/heffter/tokemetry.git#subdirectory=apps/collector"
readonly SERVICE_NAME="tokemetry-collector"
readonly PLIST_LABEL="com.tokemetry.collector"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
readonly SCRIPT_DIR REPO_ROOT
readonly EXAMPLE_CONFIG="${REPO_ROOT}/deploy/collector.example.toml"
readonly SYSTEMD_UNIT="${REPO_ROOT}/deploy/collector/systemd/tokemetry-collector.service"
readonly LAUNCHD_PLIST="${REPO_ROOT}/deploy/collector/launchd/com.tokemetry.collector.plist"

# Defaults, overridable by flags.
SERVER_URL=""
API_TOKEN=""
MACHINE_NAME=""
CONFIG_PATH="${XDG_CONFIG_HOME:-${HOME}/.config}/tokemetry/collector.toml"
FROM_GIT=0
NO_SERVICE=0

log()  { printf '  %s\n' "$*"; }
info() { printf '\n==> %s\n' "$*"; }
warn() { printf 'WARNING: %s\n' "$*" >&2; }
die()  { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }

usage() {
    cat <<'EOF'
Install and register the tokemetry collector (Linux and macOS).

Usage:
  deploy/collector/install.sh [options]

Options:
  --server-url URL     Server base URL, e.g. http://10.10.0.1:8787
  --token TOKEN        API bearer token (the bootstrap token for a first run)
  --machine-name NAME  Machine label in the dashboard (default: this hostname)
  --config PATH        Config file path
                       (default: ~/.config/tokemetry/collector.toml)
  --from-git           Install the collector from GitHub, not from this clone
  --no-service         Install and configure only; do not register a service
  -h, --help           Show this help and exit

The service starts only when the config is complete (a real --token was given).
Without a token the script installs the collector and writes a placeholder
config, then stops: edit the config and re-run to finish.
EOF
}

parse_args() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --server-url)   SERVER_URL="${2:?--server-url needs a value}"; shift 2 ;;
            --token)        API_TOKEN="${2:?--token needs a value}"; shift 2 ;;
            --machine-name) MACHINE_NAME="${2:?--machine-name needs a value}"; shift 2 ;;
            --config)       CONFIG_PATH="${2:?--config needs a value}"; shift 2 ;;
            --from-git)     FROM_GIT=1; shift ;;
            --no-service)   NO_SERVICE=1; shift ;;
            -h|--help)      usage; exit 0 ;;
            *)              die "unknown option: $1 (try --help)" ;;
        esac
    done
}

ensure_uv() {
    info "Ensuring uv is installed"
    if have uv; then
        log "uv already present: $(command -v uv)"
        return
    fi
    log "installing uv from https://astral.sh/uv/install.sh"
    curl -LsSf https://astral.sh/uv/install.sh | sh
    # The installer drops uv in ~/.local/bin; make it visible to this process.
    export PATH="${HOME}/.local/bin:${PATH}"
    have uv || die "uv installation did not put 'uv' on PATH; open a new shell and re-run"
}

install_collector() {
    info "Installing the collector"
    local source
    if [[ "${FROM_GIT}" -eq 1 ]]; then
        source="${GIT_SPEC}"
        log "source: GitHub (${GIT_SPEC})"
    else
        source="${REPO_ROOT}/apps/collector"
        [[ -f "${source}/pyproject.toml" ]] || die "collector package not found at ${source}; run from a clone or pass --from-git"
        log "source: ${source}"
    fi
    # --force makes re-runs upgrade in place instead of erroring.
    uv tool install --force "${source}"
}

collector_bin() {
    if have tokemetry-collector; then
        command -v tokemetry-collector
    else
        printf '%s\n' "${HOME}/.local/bin/tokemetry-collector"
    fi
}

scaffold_config() {
    info "Scaffolding config at ${CONFIG_PATH}"
    mkdir -p "$(dirname "${CONFIG_PATH}")"
    if [[ -f "${CONFIG_PATH}" ]]; then
        log "config already exists; leaving it untouched"
        if [[ -n "${SERVER_URL}${API_TOKEN}" ]]; then
            warn "config exists, so --server-url/--token/--machine-name were NOT applied; edit ${CONFIG_PATH} by hand"
        fi
        return
    fi
    [[ -f "${EXAMPLE_CONFIG}" ]] || die "example config not found at ${EXAMPLE_CONFIG}; run from a clone"
    # Rewrite the known top-level keys from the provided flags. Values pass
    # through the environment (not awk -v) so backslashes/ampersands stay
    # literal. Empty values leave the example line untouched.
    TKM_URL="${SERVER_URL}" TKM_TOKEN="${API_TOKEN}" TKM_NAME="${MACHINE_NAME}" \
        awk '
            BEGIN {
                url = ENVIRON["TKM_URL"]; tok = ENVIRON["TKM_TOKEN"];
                name = ENVIRON["TKM_NAME"]
            }
            /^server_url = / && url != ""    { print "server_url = \"" url "\""; next }
            /^api_token = / && tok != ""     { print "api_token = \"" tok "\""; next }
            /^machine_name = / && name != "" { print "machine_name = \"" name "\""; next }
            { print }
        ' "${EXAMPLE_CONFIG}" > "${CONFIG_PATH}"
    chmod 600 "${CONFIG_PATH}"
    log "wrote ${CONFIG_PATH} (mode 600)"
}

# The config is ready to run once the placeholder token has been replaced.
config_ready() {
    [[ -f "${CONFIG_PATH}" ]] || return 1
    ! grep -qE 'tkm_replace_me|tkm_change_me' "${CONFIG_PATH}"
}

register_linux() {
    info "Registering the systemd user service"
    local bin="$1" unit_dir="${XDG_CONFIG_HOME:-${HOME}/.config}/systemd/user"
    mkdir -p "${unit_dir}"
    # Render the shipped unit with the resolved binary and config paths.
    TKM_EXEC="${bin} --config ${CONFIG_PATH}" \
        awk '/^ExecStart=/ { print "ExecStart=" ENVIRON["TKM_EXEC"]; next } { print }' \
        "${SYSTEMD_UNIT}" > "${unit_dir}/tokemetry-collector.service"
    systemctl --user daemon-reload
    systemctl --user enable --now "${SERVICE_NAME}"
    # Keep the service running when the user is logged out (best effort; may be
    # denied without privileges).
    if ! loginctl enable-linger "${USER}" 2>/dev/null; then
        warn "could not enable linger for ${USER}; the collector stops at logout unless you run: sudo loginctl enable-linger ${USER}"
    fi
    log "started; follow logs with: journalctl --user -u ${SERVICE_NAME} -f"
}

register_macos() {
    info "Registering the launchd agent"
    local bin="$1" agent="${HOME}/Library/LaunchAgents/${PLIST_LABEL}.plist"
    mkdir -p "${HOME}/Library/LaunchAgents" "${HOME}/Library/Logs"
    # Render the shipped plist, replacing the CHANGE_ME template paths literally
    # (lrep, so no path character is treated as a regex or replacement token).
    TKM_BIN="${bin}" TKM_CFG="${CONFIG_PATH}" TKM_HOME="${HOME}" \
        awk '
            function lrep(s, from, to,   out, p) {
                out = ""
                while ((p = index(s, from)) > 0) {
                    out = out substr(s, 1, p - 1) to
                    s = substr(s, p + length(from))
                }
                return out s
            }
            BEGIN {
                bin = ENVIRON["TKM_BIN"]; cfg = ENVIRON["TKM_CFG"];
                home = ENVIRON["TKM_HOME"]
            }
            {
                line = lrep($0, "/Users/CHANGE_ME/.local/bin/tokemetry-collector", bin)
                line = lrep(line, "/Users/CHANGE_ME/.config/tokemetry/collector.toml", cfg)
                line = lrep(line, "/Users/CHANGE_ME", home)
                print line
            }
        ' "${LAUNCHD_PLIST}" > "${agent}"
    # Reload: unload first (ignore "not loaded"), then load the current plist.
    launchctl unload "${agent}" 2>/dev/null || true
    launchctl load "${agent}"
    log "loaded; follow logs with: tail -f ${HOME}/Library/Logs/tokemetry-collector.log"
}

smoke_test() {
    local bin="$1"
    info "Verifying (dry run, uploads nothing)"
    if "${bin}" --config "${CONFIG_PATH}" --dry-run; then
        log "dry run OK"
    else
        warn "dry run failed; check server_url/api_token in ${CONFIG_PATH}"
    fi
}

main() {
    parse_args "$@"

    local os
    os="$(uname -s)"
    case "${os}" in
        Linux|Darwin) : ;;
        *) die "unsupported OS '${os}'; follow the manual docs under docs/deployment/" ;;
    esac
    # Give every machine a real label even when --machine-name is omitted.
    [[ -n "${MACHINE_NAME}" ]] || MACHINE_NAME="$(hostname)"

    ensure_uv
    install_collector
    local bin
    bin="$(collector_bin)"
    log "collector binary: ${bin}"
    scaffold_config

    if ! config_ready; then
        info "Config needs values before the collector can run"
        log "edit ${CONFIG_PATH}: set server_url and api_token"
        log "then finish with: ${SCRIPT_DIR}/install.sh --config \"${CONFIG_PATH}\""
        log "(re-running is safe; it will not overwrite your edited config)"
        return
    fi

    smoke_test "${bin}"

    if [[ "${NO_SERVICE}" -eq 1 ]]; then
        info "Skipping service registration (--no-service)"
        return
    fi
    case "${os}" in
        Linux)  register_linux "${bin}" ;;
        Darwin) register_macos "${bin}" ;;
    esac
    info "Done. The collector is installed and running."
}

main "$@"
