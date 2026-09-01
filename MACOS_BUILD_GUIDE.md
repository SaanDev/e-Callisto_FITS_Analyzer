# Build the macOS app from source (beginner guide)

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
python src/Installation/install_requirements.py --with-build
```

```bash
python -m pip install dmgbuild
```

The middle command downloads many scientific packages and can take a while.
Warnings are usually harmless. Stop only if Terminal ends with `ERROR` or
`Failed`.

Optional check: run the program directly from the source before packaging it:

```bash
python src/UI/main.py
```

If the Analyzer window opens, quit it normally and continue.

## Step 7: Build the `.app`

Paste this single command:

```bash
PYTHON_BIN="$PWD/.venv-macos/bin/python" bash src/Installation/build_macos_dmg.sh
```

The build prints a lot of text and may appear to pause for several minutes. Let
it continue. The important line near the start should show a Python path ending
in `.venv-macos/bin/python (3.13.x)`.

The build is complete when Terminal prints `==> Done` followed by two paths:

- `dist/e-Callisto FITS Analyzer.app` — the requested application.
- `dist/e-callisto-fits-analyzer_..._macOS_....dmg` — a convenient installer
  disk image.

The CPU name in the DMG filename is normally `arm64` for an Apple-silicon Mac
or `x86_64` for an Intel Mac.

## Step 8: Test and open the new app

First run the included automatic launch check:

```bash
python src/Installation/smoke_test_packaged.py --timeout 25
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
python src/Installation/install_requirements.py --with-build
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
3. Return to the project and reactivate its existing environment:

   ```bash
   cd "/Users/sindhug/Desktop/FITS Analyzer/e-Callisto_FITS_Analyzer"
   source .venv/bin/activate
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
python src/Installation/install_requirements.py --with-build
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
