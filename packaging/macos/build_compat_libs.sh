#!/usr/bin/env bash
set -euo pipefail

# Build private libraries without changing the host's Homebrew installation.
# Then pass MACOS_NATIVE_LIB_DIR and OPENSSL_LIB_DIR to build_macos_dmg.sh.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PREFIX="${MACOS_NATIVE_PREFIX:-$ROOT/.venv-macos-native}"
PYTHON_BIN="${PYTHON_BIN:-$ROOT/.venv/bin/python}"
export MACOSX_DEPLOYMENT_TARGET="${MACOSX_DEPLOYMENT_TARGET:-15.0}"
ARCH="$(uname -m)"
case "$ARCH" in
  arm64) OPENSSL_TARGET=darwin64-arm64-cc ;;
  x86_64) OPENSSL_TARGET=darwin64-x86_64-cc ;;
  *) echo "Unsupported architecture: $ARCH" >&2; exit 1 ;;
esac
mkdir -p "$PREFIX/src" "$PREFIX/lib"

"$PYTHON_BIN" - "$PREFIX/src" <<'PY'
import hashlib
from pathlib import Path
import sys
import tarfile
import urllib.request

sources = [
    ("https://github.com/openssl/openssl/releases/download/openssl-3.5.9/openssl-3.5.9.tar.gz",
     "603f5602e2eef00d77fbd429d34dcd5822bb301757a1bc9cdb24c670f1eb859a"),
    ("https://github.com/tukaani-project/xz/releases/download/v5.8.4/xz-5.8.4.tar.gz",
     "0014c7886930454fe8bd4228665b51af55eeae560ea135c9c4cd33f55b2591d9"),
    ("https://www.sqlite.org/2026/sqlite-autoconf-3540100.tar.gz",
     "926ef8d3eab93fbf6e06011901273eec076b25594a4e1cdadd50c987f4f6d64e"),
]
base = Path(sys.argv[1])
for url, expected in sources:
    archive = base / url.rsplit("/", 1)[-1]
    if not archive.exists():
        urllib.request.urlretrieve(url, archive)
    if hashlib.sha256(archive.read_bytes()).hexdigest() != expected:
        raise SystemExit(f"Checksum mismatch: {archive}")
    with tarfile.open(archive) as source:
        source.extractall(base, filter="data")
    print(f"Verified {archive.name}", flush=True)
PY

cd "$PREFIX/src/openssl-3.5.9"
./Configure "$OPENSSL_TARGET" shared no-tests --prefix="$PREFIX/openssl" \
  "-mmacosx-version-min=$MACOSX_DEPLOYMENT_TARGET"
make -j8
cp libssl.3.dylib libcrypto.3.dylib "$PREFIX/lib/"

cmake -S "$PREFIX/src/xz-5.8.4" -B "$PREFIX/xz-build" \
  -DCMAKE_BUILD_TYPE=Release -DBUILD_SHARED_LIBS=ON -DBUILD_TESTING=OFF \
  "-DCMAKE_OSX_DEPLOYMENT_TARGET=$MACOSX_DEPLOYMENT_TARGET" \
  "-DCMAKE_OSX_ARCHITECTURES=$ARCH"
cmake --build "$PREFIX/xz-build" -j8
cp "$PREFIX/xz-build/liblzma.5.dylib" "$PREFIX/lib/"

clang -dynamiclib -O2 -arch "$ARCH" "-mmacosx-version-min=$MACOSX_DEPLOYMENT_TARGET" \
  -DSQLITE_ENABLE_FTS5 -DSQLITE_ENABLE_RTREE -DSQLITE_ENABLE_COLUMN_METADATA \
  -DSQLITE_ENABLE_DBSTAT_VTAB -DSQLITE_ENABLE_MATH_FUNCTIONS -DSQLITE_THREADSAFE=1 \
  -DSQLITE_ENABLE_LOAD_EXTENSION -install_name @rpath/libsqlite3.dylib \
  -compatibility_version 9.0.0 -current_version 9.6.0 \
  "$PREFIX/src/sqlite-autoconf-3540100/sqlite3.c" -o "$PREFIX/lib/libsqlite3.dylib"

echo "Compatible libraries: $PREFIX/lib"
