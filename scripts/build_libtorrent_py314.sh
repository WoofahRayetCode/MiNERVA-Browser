#!/usr/bin/env bash
# Build python-libtorrent bindings for CPython 3.14 against system
# libtorrent-rasterbar + Boost.Python (no PyPI cp314 wheel yet).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
VENV_PYTHON="${ROOT}/.venv/bin/python"
SITE="$("${VENV_PYTHON}" -c 'import sysconfig; print(sysconfig.get_paths()["platlib"])')"
EXT_SUFFIX="$("${VENV_PYTHON}" -c 'import sysconfig; print(sysconfig.get_config_var("EXT_SUFFIX"))')"
OUT="${SITE}/libtorrent${EXT_SUFFIX}"
SRC_ROOT="${ROOT}/vendor/libtorrent-rasterbar-2.1.1"
SRC="${SRC_ROOT}/bindings/python/src"
TARBALL="${ROOT}/vendor/libtorrent-rasterbar-2.1.1.tar.gz"
TARBALL_URL="https://github.com/arvidn/libtorrent/releases/download/v2.1.1/libtorrent-rasterbar-2.1.1.tar.gz"

if [[ -f "${OUT}" ]]; then
  if "${VENV_PYTHON}" -c 'import libtorrent as lt; print(lt.__version__)' >/dev/null 2>&1; then
    echo "libtorrent already importable: ${OUT}"
    exit 0
  fi
fi

if ! command -v g++ >/dev/null || ! command -v pkg-config >/dev/null; then
  echo "g++ and pkg-config are required" >&2
  exit 1
fi
if ! pkg-config --exists libtorrent-rasterbar; then
  echo "Install libtorrent-rasterbar (and boost) first" >&2
  exit 1
fi
if [[ ! -f /usr/lib/libboost_python314.so && ! -f /usr/lib/libboost_python314.so.1.92.0 ]]; then
  echo "Boost.Python for 3.14 not found (libboost_python314)" >&2
  exit 1
fi

mkdir -p "${ROOT}/vendor"
if [[ ! -d "${SRC}" ]]; then
  if [[ ! -f "${TARBALL}" ]]; then
    curl -L --fail -o "${TARBALL}" "${TARBALL_URL}"
  fi
  tar -xzf "${TARBALL}" -C "${ROOT}/vendor"
fi

OBJDIR="${ROOT}/vendor/ltpy-obj"
rm -rf "${OBJDIR}"
mkdir -p "${OBJDIR}"

CXXFLAGS=(
  -O2 -fPIC -std=c++17 -Wno-deprecated-declarations
  $(pkg-config --cflags libtorrent-rasterbar)
  -I/usr/include/python3.14
  -I"${SRC}"
)

SOURCES=(
  alert converters create_torrent datetime entry error_code file_storage
  fingerprint info_hash ip_filter load_torrent magnet_uri module peer_info
  session session_settings sha1_hash sha256_hash string torrent_handle
  torrent_info torrent_status utility version
)

OBJS=()
for name in "${SOURCES[@]}"; do
  obj="${OBJDIR}/${name}.o"
  g++ -c "${CXXFLAGS[@]}" "${SRC}/${name}.cpp" -o "${obj}"
  OBJS+=("${obj}")
done

mkdir -p "${SITE}"
g++ -shared -o "${OUT}" "${OBJS[@]}" \
  $(pkg-config --libs libtorrent-rasterbar) \
  -lboost_python314 -lpython3.14

"${VENV_PYTHON}" -c 'import libtorrent as lt; print(f"Built libtorrent {lt.__version__} -> {lt.__file__}")'
