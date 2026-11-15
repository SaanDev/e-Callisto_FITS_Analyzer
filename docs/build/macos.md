# Build the macOS app from source (beginner guide)

## Release build: macOS 13 or later (maintainers)

The v3.2.0 release disk image targets **Apple silicon with macOS 13.0 or
later**. The signing step checks every bundled Mach-O binary and rejects any
whose minimum macOS version exceeds the deployment target, so a successful
build is itself proof that nothing in the bundle needs a newer macOS.

On a current build Mac, four things would otherwise raise that floor:

- **Homebrew's Python** is built for the macOS its bottle targets (15.0 for
  `python@3.13`). Use the python.org Python 3.13 universal2 installer instead;
  it supports macOS 11 and bundles its own OpenSSL, SQLite and liblzma.
- **pip prefers the newest wheel the host accepts**, so on macOS 14 or later it
  installs NumPy, SciPy and netCDF4 wheels tagged `macosx_14_0`. Download the
  macOS 13 wheels explicitly.
- **netCDF4 1.7.x has no Python 3.13 wheel for macOS 13.** Build it, with its
  HDF5 and netCDF-C libraries, for 13.0.
- **PySide6 6.11.2's binding modules** (`PySide6/*.abi3.so` and `shiboken6`)
  are labelled macOS 15.0, although the wheel is tagged `macosx_13_0`, its Qt
  frameworks are built for 13.0, and the bindings import only C, Objective-C
  and C++ runtime symbols that macOS 13 has. Relabel them to 13.0. Check
  again before doing this for another PySide6 version: `nm -m -u` on the
  bindings must show nothing from the system newer than macOS 13.

The steps below need the Apple command-line tools, CMake and Ninja (Homebrew's
`cmake` and `ninja` are fine; its libraries are not), and python.org Python
3.13 in `/Library/Frameworks/Python.framework/Versions/3.13`. Run them from the
repository root, in order. Keep build inputs out of `build/`:
`build_macos_dmg.sh` deletes that folder before running py2app.

1. Build private OpenSSL, liblzma and SQLite for macOS 13. The helper verifies
   the source archives' SHA-256 checksums and leaves Homebrew untouched:

   ```bash
   PYTHON_BIN=/Library/Frameworks/Python.framework/Versions/3.13/bin/python3.13 MACOSX_DEPLOYMENT_TARGET=13.0 MACOS_NATIVE_PREFIX="$PWD/.venv-macos13-native" bash packaging/macos/build_compat_libs.sh
   ```

2. Create the environment and install the macOS 13 wheels:

   ```bash
   /Library/Frameworks/Python.framework/Versions/3.13/bin/python3.13 -m venv .venv-macos13
   source .venv-macos13/bin/activate
   grep -v '^netCDF4' requirements/requirements-runtime.txt > "$TMPDIR/req-macos13.txt"
   python -m pip download --platform macosx_13_0_arm64 --python-version 3.13 --implementation cp --only-binary=:all: -d "$TMPDIR/wheels-macos13" -r "$TMPDIR/req-macos13.txt" py2app==0.28.10 dmgbuild setuptools==80.9.0 wheel==0.45.1 delocate
   python -m pip install --no-index --find-links "$TMPDIR/wheels-macos13" -r "$TMPDIR/req-macos13.txt" py2app==0.28.10 dmgbuild setuptools==80.9.0 wheel==0.45.1 delocate
   ```

   `--no-index` keeps pip from falling back to the macOS 14 wheels on PyPI. To
   reproduce an earlier build exactly, pass that environment's `pip freeze` to
   both commands with `-c`.

3. Build HDF5 1.14.6 and netCDF-C 4.9.3 for macOS 13, without Homebrew's
   libraries:

   ```bash
   NC_PREFIX="$PWD/.venv-macos13-native/netcdf-hdf5"
   NC_SRC="$PWD/.venv-macos13-native/src"
   mkdir -p "$NC_SRC"
   curl -fL -o "$NC_SRC/hdf5-1.14.6.tar.gz" https://github.com/HDFGroup/hdf5/releases/download/hdf5_1.14.6/hdf5-1.14.6.tar.gz
   curl -fL -o "$NC_SRC/netcdf-c-4.9.3.tar.gz" https://github.com/Unidata/netcdf-c/archive/refs/tags/v4.9.3.tar.gz
   tar -xzf "$NC_SRC/hdf5-1.14.6.tar.gz" -C "$NC_SRC"
   tar -xzf "$NC_SRC/netcdf-c-4.9.3.tar.gz" -C "$NC_SRC"
   COMMON=(-G Ninja -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX="$NC_PREFIX" -DCMAKE_OSX_ARCHITECTURES=arm64 -DCMAKE_OSX_DEPLOYMENT_TARGET=13.0 -DCMAKE_IGNORE_PREFIX_PATH=/opt/homebrew -DCMAKE_SYSTEM_IGNORE_PREFIX_PATH=/opt/homebrew -DCMAKE_PREFIX_PATH="$NC_PREFIX" -DCMAKE_INSTALL_NAME_DIR="$NC_PREFIX/lib" -DBUILD_TESTING=OFF)
   cmake -S "$NC_SRC/hdf5-1.14.6" -B "$NC_SRC/hdf5-build" "${COMMON[@]}" -DBUILD_SHARED_LIBS=ON -DBUILD_STATIC_LIBS=OFF -DHDF5_BUILD_TOOLS=OFF -DHDF5_BUILD_EXAMPLES=OFF -DHDF5_BUILD_UTILS=OFF -DHDF5_BUILD_FORTRAN=OFF -DHDF5_BUILD_CPP_LIB=OFF -DHDF5_BUILD_HL_LIB=ON -DHDF5_ENABLE_Z_LIB_SUPPORT=ON -DHDF5_ENABLE_SZIP_SUPPORT=OFF -DZLIB_USE_EXTERNAL=OFF -DHDF5_ENABLE_PLUGIN_SUPPORT=OFF
   cmake --build "$NC_SRC/hdf5-build" && cmake --install "$NC_SRC/hdf5-build"
   cmake -S "$NC_SRC/netcdf-c-4.9.3" -B "$NC_SRC/netcdf-build" "${COMMON[@]}" -DBUILD_SHARED_LIBS=ON -DNETCDF_ENABLE_HDF5=ON -DHDF5_ROOT="$NC_PREFIX" -DNETCDF_ENABLE_DAP=OFF -DNETCDF_ENABLE_BYTERANGE=OFF -DNETCDF_ENABLE_NCZARR=OFF -DNETCDF_ENABLE_PLUGINS=OFF -DNETCDF_ENABLE_FILTER_SZIP=OFF -DNETCDF_ENABLE_FILTER_BZ2=OFF -DNETCDF_ENABLE_FILTER_ZSTD=OFF -DNETCDF_ENABLE_FILTER_BLOSC=OFF -DNETCDF_ENABLE_LIBXML2=OFF -DNETCDF_BUILD_UTILITIES=OFF -DNETCDF_ENABLE_TESTS=OFF
   cmake --build "$NC_SRC/netcdf-build" && cmake --install "$NC_SRC/netcdf-build"
   for lib in libnetcdf.22.dylib libhdf5_hl.310.0.6.dylib; do install_name_tool -add_rpath "$NC_PREFIX/lib" "$NC_PREFIX/lib/$lib" && codesign --force --sign - "$NC_PREFIX/lib/$lib"; done
   ```

   The added rpath lets `delocate` find HDF5 from `libnetcdf` in the next step.
   Run step 4 in the same Terminal window, because it uses `NC_PREFIX`.

4. Build netCDF4 against those libraries, copy them into its wheel, and install
   it:

   ```bash
   MACOSX_DEPLOYMENT_TARGET=13.0 ARCHFLAGS="-arch arm64" _PYTHON_HOST_PLATFORM=macosx-13.0-arm64 NETCDF4_DIR="$NC_PREFIX" HDF5_DIR="$NC_PREFIX" PATH="$NC_PREFIX/bin:$PATH" python -m pip wheel --no-deps --no-binary netCDF4 -w "$TMPDIR/netcdf4-raw" netCDF4==1.7.3
   delocate-wheel --require-archs arm64 --require-target-macos-version 13.0 -w "$TMPDIR/wheels-macos13" "$TMPDIR"/netcdf4-raw/netcdf4-1.7.3-*.whl
   python -m pip install --no-index --find-links "$TMPDIR/wheels-macos13" netCDF4==1.7.3
   ```

   The python.org Python is universal2, so `ARCHFLAGS` keeps the build to
   arm64; the libraries from step 3 have no Intel slice.

5. Relabel the PySide6 bindings with the command in
   [Step 6 of the guide below](#macos-13-and-14-only-relabel-the-pyside6-bindings).

6. Build the app. Point the OpenSSL repair at step 1's libraries and keep
   Homebrew's liblzma out of the bundle:

   ```bash
   PYTHON_BIN="$PWD/.venv-macos13/bin/python" MACOSX_DEPLOYMENT_TARGET=13.0 MACOS_NATIVE_LIB_DIR="$PWD/.venv-macos13-native/lib" OPENSSL_LIB_DIR="$PWD/.venv-macos13-native/lib" bash packaging/macos/build_macos_dmg.sh
   ```

7. The bundle inherits universal2 binaries from python.org Python and the Qt
   wheels, and on an Intel Mac it would then start and crash instead of being
   refused. Thin every universal file to arm64 (this also takes the bundle from
   about 1.9 GB to 1.4 GB), then sign, smoke-test and package it again:

   ```bash
   python - "dist/e-Callisto FITS Analyzer.app" <<'PY'
   import os, subprocess, sys
   for folder, _, names in os.walk(sys.argv[1]):
       for name in names:
           path = os.path.join(folder, name)
           if os.path.islink(path):
               continue
           with open(path, "rb") as handle:
               if handle.read(4) != b"\xca\xfe\xba\xbe":
                   continue
           mode = os.stat(path).st_mode
           subprocess.run(["lipo", path, "-thin", "arm64", "-output", path + ".arm64"], check=True)
           os.replace(path + ".arm64", path)
           os.chmod(path, mode)
   PY
   SKIP_APP=1 PYTHON_BIN="$PWD/.venv-macos13/bin/python" MACOSX_DEPLOYMENT_TARGET=13.0 MACOS_NATIVE_LIB_DIR="$PWD/.venv-macos13-native/lib" OPENSSL_LIB_DIR="$PWD/.venv-macos13-native/lib" bash packaging/macos/build_macos_dmg.sh
   ```

The finished `Info.plist` must show `LSMinimumSystemVersion` `13.0`. The binary
scan and launch smoke checks do not replace a functional test on a physical
macOS 13 machine.

This guide creates a new **e-Callisto FITS Analyzer.app** on the Mac where it
will be used. No programming knowledge is required.

## Why build it locally?

An error containing `pyexpat`, `_XML_SetReparseDeferralEnabled`, and “built for
macOS ... newer than running OS” means the downloaded app contains a Python
library made on a newer version of macOS. It does **not** mean the FITS files or
the user's Mac are damaged.

Building from source on the affected Mac makes the bundled libraries match that
Mac. Build on the Mac that will run the app; building it on another, newer Mac
can reproduce the same problem.

This process creates a new app. It does not modify the broken app already in
`Applications`.

From v3.2.0, the disk image on the release page runs on any Mac with Apple
silicon and macOS 13 Ventura or later. Try it before building. Building locally
is still the way to run the app on an Intel Mac or an older macOS.

## Before starting

You need:

- The Mac on which the app will be used.
- An administrator password.
- A reliable internet connection.
- At least **15 GB of free disk space**. The final app is large, and the build
  needs temporary working space.
- About 30–90 minutes. The exact time depends on the Mac and internet speed.

Keep the Mac connected to power. Do not close Terminal while a command is
running.

## Step 1: Install Apple's command-line tools

1. Open **Finder → Applications → Utilities → Terminal**.
2. Copy the following line, paste it into Terminal, and press **Return**:

   ```bash
   xcode-select --install
   ```

3. If a window appears, select **Install** and wait for it to finish.
4. If Terminal says the tools are already installed, continue to Step 2.

The full Xcode application is not required.

## Step 2: Install a clean Python 3.13

Do not use the Python inside Anaconda or Spyder for this build. The `(base)`
shown at the start of a Terminal prompt means Anaconda is active, but the steps
below create a separate, clean environment.

1. Open the [official Python macOS downloads page](https://www.python.org/downloads/macos/).
2. Choose the latest **Python 3.13** release.
3. Download its **macOS 64-bit universal2 installer** (`.pkg`).
4. Open the downloaded package and accept the normal installation options.
5. Close Terminal, open it again, and paste this command:

   ```bash
   /Library/Frameworks/Python.framework/Versions/3.13/bin/python3.13 --version
   ```

It should print `Python 3.13.x`. If it says “No such file or directory”, repeat
this step and make sure the universal2 `.pkg` installer completed.

## Step 3: Download the source code

1. Open the [e-Callisto FITS Analyzer source page](https://github.com/SaanDev/e-Callisto_FITS_Analyzer).
2. Select the green **Code** button, then **Download ZIP**.
3. Open the ZIP file if macOS does not unpack it automatically.
4. Move the resulting `e-Callisto_FITS_Analyzer...` folder somewhere easy to
   find, such as the Desktop.

If the source folder is already on the Mac, use that folder and skip the
download.

## Step 4: Open the source folder in Terminal

1. Open a new Terminal window.
2. If the prompt starts with `(base)`, enter this once:

   ```bash
   conda deactivate
   ```

3. Type `cd` followed by one space, but do not press Return yet:

   ```text
   cd 
   ```

4. Drag the downloaded source folder from Finder into the Terminal window. Its
   full location will appear after `cd `.
5. Press **Return**.
6. Paste this command:

   ```bash
   ls
   ```

The list must include `README.md`, `assets`, and `src`. If it does not, repeat
this step and drag the correct folder.

## Step 5: Create a private build environment

Paste these commands into Terminal **one line at a time**. Wait for each command
to finish before pasting the next one.

```bash
/Library/Frameworks/Python.framework/Versions/3.13/bin/python3.13 -m venv .venv-macos
```

```bash
source .venv-macos/bin/activate
```

```bash
python --version
```

The last command must print `Python 3.13.x`. The Terminal prompt should now
start with `(.venv-macos)`.

## Step 6: Install the app and build requirements

Run these commands one at a time:

```bash
python -m pip install --upgrade pip
```

```bash
python scripts/install_requirements.py --with-build
```

```bash
python -m pip install dmgbuild
```

The middle command downloads many scientific packages and can take a while.
Warnings are usually harmless. Stop only if Terminal ends with `ERROR` or
`Failed`.

Optional check: run the program directly from the source before packaging it:

```bash
python src/main.py
```

If the Analyzer window opens, quit it normally and continue.

### macOS 13 and 14 only: relabel the PySide6 bindings

Skip this on macOS 15 or later. To see the version, run `sw_vers -productVersion`.

PySide6, the package that draws the app's windows, marks some of its files as
needing macOS 15, although they run on macOS 13. The build refuses files marked
for a newer macOS than the Mac's own, so change that mark first. With the
private environment still active, paste this whole block at once:

```bash
python - <<'PY'
import pathlib, subprocess
import PySide6, shiboken6
for package in (PySide6, shiboken6):
    for path in pathlib.Path(package.__file__).parent.rglob("*"):
        if path.suffix not in (".so", ".dylib") or path.is_symlink():
            continue
        info = subprocess.run(["vtool", "-arch", "arm64", "-show-build", str(path)],
                              capture_output=True, text=True).stdout.split()
        if "minos" not in info or info[info.index("minos") + 1] != "15.0":
            continue
        sdk = info[info.index("sdk") + 1]
        tmp = path.with_name(path.name + ".tmp")
        subprocess.run(["vtool", "-set-build-version", "macos", "13.0", sdk, "-replace",
                        "-output", str(tmp), str(path)], check=True)
        tmp.replace(path)
        subprocess.run(["codesign", "--force", "--sign", "-", str(path)],
                       check=True, capture_output=True)
        print("relabelled", path.name)
PY
```

It prints one `relabelled ...` line per file, about 60 in all. Running it again
prints nothing, because the files are already done.

## Step 7: Build the `.app`

Paste this single command:

```bash
MACOSX_DEPLOYMENT_TARGET="$(sw_vers -productVersion | cut -d. -f1).0" PYTHON_BIN="$PWD/.venv-macos/bin/python" bash packaging/macos/build_macos_dmg.sh
```

The first part builds the app for this Mac's macOS version. Without it, the
build script assumes macOS 15, and macOS 13 or 14 would refuse to open the
finished app.

The build prints a lot of text and may appear to pause for several minutes. Let
it continue. The important lines near the start should show a deployment target
matching this Mac's macOS version and a Python path ending in
`.venv-macos/bin/python (3.13.x)`.

The build is complete when Terminal prints `==> Done` followed by two paths:

- `dist/e-Callisto FITS Analyzer.app` — the requested application.
- `dist/e-callisto-fits-analyzer_..._macOS_....dmg` — a convenient installer
  disk image.

The CPU name in the DMG filename is normally `arm64` for an Apple-silicon Mac
or `x86_64` for an Intel Mac.

## Step 8: Test and open the new app

First run the included automatic launch check:

```bash
python scripts/smoke_test_packaged.py --timeout 25
```

It takes about a minute and should finish with `Packaged smoke checks passed.`

Then open the output folder:

```bash
open dist
```

Right-click **e-Callisto FITS Analyzer.app** and select **Open**. On the first
launch, macOS may display a security message because this personal build is not
notarized. Select **Open** again when offered.

Once it launches correctly, either:

- Drag the new `.app` into the Mac's **Applications** folder; or
- Open the generated `.dmg` and drag the app onto its **Applications** alias.

If Finder asks whether to replace the old broken copy, verify that the source
is the newly built app in `dist`, then choose **Replace**.

## If something goes wrong

### The original `pyexpat` error still appears

The old copy in `/Applications` was probably opened. From the project folder,
launch the newly built copy directly:

```bash
"$PWD/dist/e-Callisto FITS Analyzer.app/Contents/MacOS/e-Callisto FITS Analyzer"
```

If this new copy opens, replace the old app in `Applications` with it.

### `No module named py2app` or `No module named dmgbuild`

Reactivate the private environment and repeat the installation commands:

```bash
source .venv-macos/bin/activate
python scripts/install_requirements.py --with-build
python -m pip install dmgbuild
```

Then repeat Step 7.

### `Failed to build 'netCDF4'` or `did not find HDF5 headers`

This can happen on an older macOS release when PyPI has no compatible
prebuilt `netCDF4` package. Keep the private environment active; there is no
need to delete it or download the project again.

Install the native NetCDF library through Homebrew. It installs HDF5 as a
dependency:

```bash
brew install netcdf
```

Do not continue if that command ends with an `Error`. In particular, if the
download of `libaec` from `gitlab.dkrz.de` times out, retry that dependency
before doing anything with Python:

```bash
brew postinstall gcc
brew fetch --retry libaec
brew install libaec
brew install netcdf
```

If the same website times out again, switch to another internet connection
(a phone hotspot is sufficient), disable any VPN or network filter, and repeat
those commands.

If `gitlab.dkrz.de` remains unreachable on macOS 13, use MacPorts instead of
Homebrew for these two native libraries:

1. Download and install the **macOS Ventura v13** package from the
   [official MacPorts installer page](https://www.macports.org/install.php).
2. Close Terminal and open it again.
3. Return to the project folder used in Step 4 and reactivate the environment
   created in Step 5:

   ```bash
   source .venv-macos/bin/activate
   ```

4. Install HDF5 without its unneeded Fortran component, followed by NetCDF:

   ```bash
   sudo /opt/local/bin/port selfupdate
   sudo /opt/local/bin/port install hdf5 -fortran
   sudo /opt/local/bin/port install netcdf
   ```

   Terminal does not show any characters while the administrator password is
   entered. Type it normally and press Return.

5. Confirm that the required files exist:

   ```bash
   test -f /opt/local/include/hdf5.h && echo "HDF5 OK"
   test -x /opt/local/bin/nc-config && echo "NetCDF OK"
   ```

6. Point the Python build at MacPorts instead of the incomplete Homebrew
   locations:

   ```bash
   export HDF5_DIR=/opt/local
   export NETCDF4_DIR=/opt/local
   ```

If using MacPorts, skip the following Homebrew verification and `export`
commands and resume at `python -m pip install ...` below. Do not run
`pip install netcdf`; there is no Python package with that name.

If the Homebrew installation succeeded, confirm that it actually produced the
required files:

```bash
test -f "$(brew --prefix hdf5)/include/hdf5.h" && echo "HDF5 OK"
test -x "$(brew --prefix netcdf)/bin/nc-config" && echo "NetCDF OK"
```

Both `HDF5 OK` and `NetCDF OK` must appear. `brew --prefix` can print a planned
location even after installation has failed, so the file checks are important.

For the Homebrew route, only after both checks pass, run these commands one at
a time from the project folder:

```bash
export HDF5_DIR="$(brew --prefix hdf5)"
```

```bash
export NETCDF4_DIR="$(brew --prefix netcdf)"
```

```bash
python -m pip install --no-cache-dir netCDF4==1.7.3
```

```bash
python scripts/install_requirements.py --with-build
```

```bash
python -m pip install dmgbuild
```

Check both libraries involved in the original and current errors:

```bash
python -c "import netCDF4, pyexpat; print('netCDF4 and pyexpat are OK')"
```

If that prints `netCDF4 and pyexpat are OK`, continue with Step 7. The two
`export` commands apply to the current Terminal window, so do not close it
until installation has finished.

### `No space left on device`

Delete or move unrelated files until the Mac has at least 15 GB free, empty the
Trash, and repeat Step 7. The build script safely replaces its own incomplete
build output.

### The install command reports that this macOS version is unsupported

Install all macOS updates offered in **System Settings → General → Software
Update**, then retry. If the Mac cannot be updated and dependency installation
still refuses to continue, save the complete Terminal error text and send it
to the application's maintainer; that operating-system version may be too old
for the pinned Qt/Python packages.

### A different error appears

Do not send only the last line. In Terminal, scroll up to the first line that
contains `ERROR`, copy from there to the bottom, and include:

```bash
sw_vers
uname -m
```

These two commands report the macOS version and whether the Mac is Intel or
Apple silicon; they do not reveal passwords or personal files.
