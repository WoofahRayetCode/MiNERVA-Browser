#!/usr/bin/env bash
# ==============================================================================
# Build MiNERVA Archive Browser on Linux into a standalone executable or test build
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${SCRIPT_DIR}/.venv"
DIST_DIR="${SCRIPT_DIR}/dist"
BUILD_DIR="${SCRIPT_DIR}/build"
SPEC_FILE="${SCRIPT_DIR}/minerva_browser.spec"

CLEAN=0
RUN_TESTS=1

for arg in "$@"; do
    case "${arg}" in
        --clean|-c)
            CLEAN=1
            ;;
        --skip-tests)
            RUN_TESTS=0
            ;;
        --help|-h)
            echo "Usage: ./build.sh [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  --clean, -c      Wipe build/, dist/, and .venv/ before building"
            echo "  --skip-tests     Skip running the test suite"
            echo "  --help, -h       Display this help message"
            exit 0
            ;;
        *)
            echo "Unknown option: ${arg}"
            echo "Run ./build.sh --help for usage."
            exit 1
            ;;
    esac
done

# ── Color Helpers ─────────────────────────────────────────────────────────────
CYAN='\033[0;36m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

write_step() { echo -e "\n${CYAN}==> $1${NC}"; }
write_ok()   { echo -e "    ${GREEN}✓ $1${NC}"; }
write_warn() { echo -e "    ${YELLOW}! $1${NC}"; }
write_err()  { echo -e "    ${RED}✗ $1${NC}"; }

# ── Clean ─────────────────────────────────────────────────────────────────────
if [[ "${CLEAN}" -eq 1 ]]; then
    write_step "Cleaning previous build artifacts"
    rm -rf "${BUILD_DIR}" "${DIST_DIR}" "${VENV_DIR}"
    write_ok "Removed build/, dist/, and .venv/"
fi

# ── Ensure Python 3 ───────────────────────────────────────────────────────────
write_step "Checking Python 3 installation"
PYTHON_BIN=""
for candidate in python3 python; do
    if command -v "${candidate}" >/dev/null 2>&1; then
        PYTHON_BIN="$(command -v "${candidate}")"
        break
    fi
done

if [[ -z "${PYTHON_BIN}" ]]; then
    write_err "Python 3 is required but was not found on PATH."
    exit 1
fi

PY_VERSION="$("${PYTHON_BIN}" --version 2>&1)"
write_ok "Using: ${PYTHON_BIN} (${PY_VERSION})"

# ── Setup Virtual Environment ─────────────────────────────────────────────────
write_step "Setting up virtual environment"
if [[ ! -d "${VENV_DIR}" ]]; then
    "${PYTHON_BIN}" -m venv --system-site-packages "${VENV_DIR}"
    write_ok "Created virtualenv with system site-packages at ${VENV_DIR}"
else
    write_ok "Reusing virtualenv at ${VENV_DIR}"
fi


VENV_PYTHON="${VENV_DIR}/bin/python"
VENV_PIP="${VENV_DIR}/bin/pip"
VENV_PYINSTALLER="${VENV_DIR}/bin/pyinstaller"

# ── Install / Upgrade Dependencies ────────────────────────────────────────────
write_step "Installing build dependencies"
"${VENV_PIP}" install --quiet --upgrade pip
"${VENV_PIP}" install --quiet pyinstaller pillow pystray
write_ok "PyInstaller, pillow, and pystray ready"

write_step "Installing libtorrent (optional — enables torrent downloading)"
if "${VENV_PIP}" install --quiet libtorrent 2>/dev/null; then
    write_ok "libtorrent installed — torrent engine functional"
else
    write_warn "No libtorrent binary wheel available directly from pip."
    write_warn "You can install system packages (e.g. python3-libtorrent) or continue."
fi

# ── Install / Verify chdman (mame-tools) ───────────────────────────────────────
write_step "Checking for chdman (mame-tools — enables CHD disc compression)"
if command -v chdman >/dev/null 2>&1; then
    CHDMAN_PATH="$(command -v chdman)"
    write_ok "chdman found at ${CHDMAN_PATH}"
else
    write_warn "chdman not found. Attempting to install mame-tools..."
    INSTALLED=0
    if command -v pacman >/dev/null 2>&1; then
        if sudo pacman -S --noconfirm --needed mame-tools 2>/dev/null || sudo pacman -S --noconfirm --needed mame 2>/dev/null; then
            INSTALLED=1
        fi
    elif command -v apt-get >/dev/null 2>&1; then
        if sudo apt-get update -qq && sudo apt-get install -y mame-tools 2>/dev/null; then
            INSTALLED=1
        fi
    elif command -v dnf >/dev/null 2>&1; then
        if sudo dnf install -y mame-tools 2>/dev/null; then
            INSTALLED=1
        fi
    elif command -v brew >/dev/null 2>&1; then
        if brew install mame 2>/dev/null; then
            INSTALLED=1
        fi
    fi

    if [[ "${INSTALLED}" -eq 1 ]] && command -v chdman >/dev/null 2>&1; then
        write_ok "mame-tools installed successfully — chdman is ready"
    else
        write_warn "Could not auto-install mame-tools."
        write_warn "You can install it manually (e.g. 'sudo pacman -S mame-tools' or 'sudo apt install mame-tools')."
    fi
fi

# ── Run Unit Tests ────────────────────────────────────────────────────────────
if [[ "${RUN_TESTS}" -eq 1 ]]; then
    write_step "Running unit tests"
    "${VENV_PYTHON}" -m unittest discover -s "${SCRIPT_DIR}/tests" -v
    write_ok "All unit tests passed"
fi

# ── Stamp Build Version ───────────────────────────────────────────────────────
write_step "Stamping build version"
BUILD_VERSION="$(date +'%Y.%m%d.%H%M')"
CONSTANTS_FILE="${SCRIPT_DIR}/minerva/constants.py"
INIT_FILE="${SCRIPT_DIR}/minerva/__init__.py"

# Backup original files to restore after build
cp "${CONSTANTS_FILE}" "${CONSTANTS_FILE}.bak"
cp "${INIT_FILE}" "${INIT_FILE}.bak"

cleanup_version_stamp() {
    if [[ -f "${CONSTANTS_FILE}.bak" ]]; then
        mv "${CONSTANTS_FILE}.bak" "${CONSTANTS_FILE}"
    fi
    if [[ -f "${INIT_FILE}.bak" ]]; then
        mv "${INIT_FILE}.bak" "${INIT_FILE}"
    fi
}
trap cleanup_version_stamp EXIT

sed -i -E "s/^APP_VERSION\s*=\s*\"[^\"]*\"/APP_VERSION = \"${BUILD_VERSION}\"/" "${CONSTANTS_FILE}"
sed -i -E "s/^APP_VERSION\s*=\s*\"[^\"]*\"/APP_VERSION = \"${BUILD_VERSION}\"/" "${INIT_FILE}"
write_ok "APP_VERSION = \"${BUILD_VERSION}\""

# ── Build with PyInstaller ────────────────────────────────────────────────────
write_step "Building standalone Linux binary"
cd "${SCRIPT_DIR}"
"${VENV_PYINSTALLER}" "${SPEC_FILE}" --noconfirm

# ── Verify Output ─────────────────────────────────────────────────────────────
write_step "Verifying output"
OUT_BIN="${DIST_DIR}/MiNERVA-Browser"
if [[ -f "${OUT_BIN}" ]]; then
    SIZE_MB="$(du -m "${OUT_BIN}" | cut -f1)"
    write_ok "Built successfully: ${OUT_BIN} (~${SIZE_MB} MB)"
    echo -e "\n  Linux binary is ready: ./dist/MiNERVA-Browser\n"
elif [[ -f "${DIST_DIR}/MiNERVA-Browser.exe" ]]; then
    SIZE_MB="$(du -m "${DIST_DIR}/MiNERVA-Browser.exe" | cut -f1)"
    write_ok "Built successfully: ${DIST_DIR}/MiNERVA-Browser.exe (~${SIZE_MB} MB)"
else
    write_err "Build completed but executable was not found in dist/"
    exit 1
fi
