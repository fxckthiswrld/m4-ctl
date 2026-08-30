# Momentum 4 Control

[Russian version](README.ru.md)

Unofficial control app for Sennheiser Momentum 4 over Classic Bluetooth SPP
(RFCOMM) and the GAIA3 protocol.

Windows and macOS are supported.

## Features

- Discover paired Bluetooth devices and connect to Momentum 4.
- ANC modes: Adaptive, Custom, Comfort, and Off.
- Anti-Wind: Off, Max, and Auto.
- Transparency adjustment in Custom mode.
- Electron desktop app with a Python Bluetooth bridge.

## Development Requirements

- Python 3.10+ and [uv](https://docs.astral.sh/uv/).
- Node.js 18+ and npm for the Electron UI.
- Momentum 4 paired with the computer.

Standalone Windows and macOS releases embed the Python bridge, so end users do
not need Python or `uv`.

## Quick Start

Install the Python bridge dependencies and start the desktop UI:

```bash
uv sync
cd ui
npm ci
npm run dev
```

## Desktop UI

```bash
cd ui
npm ci
npm run dev
```

Electron starts `bridge.py` automatically. Choose the headphones, connect, and
adjust the available modes in the app.

## Standalone Builds

Build on the target operating system. PyInstaller cannot build macOS binaries on
Windows or Windows binaries on macOS.

### Windows

```powershell
cd ui
npm ci
npm run dist:win
```

Artifacts: `ui/release/*.exe`.

GitHub Actions runs the bridge tests and UI checks for every push and pull
request. Pushing a tag matching `v*` additionally builds native Windows and
macOS artifacts (`arm64`, `x64`, and universal) and publishes them to a GitHub
Release.

### macOS

Install `uv` and Node.js on the Mac, then run:

```bash
uv sync
cd ui
npm ci
npm run dist:mac
```

Artifacts: `ui/release/*.dmg` and `ui/release/*.zip`.

Build on Apple Silicon for Apple Silicon, and on Intel for Intel. macOS may show
a warning on first launch until the application is signed and notarized by Apple.

## Release

1. Bump versions in `pyproject.toml`, `ui/package.json`, and `ui/package-lock.json`.
2. Build on Windows with `npm run dist:win` and on macOS with `npm run dist:mac`.
3. Test the applications on machines without Python or `uv`.
4. Commit source and lock files, but not `ui/release` or `build`.
5. Create a Git tag and GitHub Release, then attach the `.exe`, `.dmg`, and `.zip` files.

Example:

```bash
git status
git add -u
git commit -m "release: v0.2.1"
git push origin master
git tag -a v0.2.1 -m "Release v0.2.1"
git push origin v0.2.1
gh release create v0.2.1 --title "v0.2.1" --generate-notes
```

## Technical Notes

Momentum 4 uses GAIA3 with vendor `0x0495` and RFCOMM service
`a2129ff3-081b-4c45-8afe-469d9c4842ec`.

- `bridge.py`: JSON Lines bridge for Electron.
- `gaia_transport.py`: SPP transport using WinRT on Windows and IOBluetooth on macOS.

## Disclaimer

This project is not affiliated with Sennheiser. The protocol was reconstructed
empirically and may differ between firmware versions. Use at your own risk.

## License

MIT
