# Development guide

This document covers the local development workflow, the repository layout,
backend development, debugging, and the libraries used by mAIcrophone. For
product and protocol decisions, see [architecture.md](architecture.md) and
[ipc.md](ipc.md).

## Prerequisites

mAIcrophone targets Arch Linux, Wayland, and Niri. The core daemon and CLI need:

- Python 3.11 or newer;
- `wtype` for text injection;
- `libwayland-client` for native focus and clipboard support;
- `libpulse`/`libpulse-simple` and a PulseAudio-compatible server, normally
  `pipewire-pulse`;
- `nemo-speech` with the `nemotron-3.5` model.

The optional GTK4 overlay additionally needs:

- `gtk4`, `gtk4-layer-shell`, `python-gobject`, and `python-cairo` from the
  distro;
- the `.[overlay]` Python extra.

Optional runtime tools:

- `wl-copy` as a clipboard fallback;
- `pw-record` as an alternative audio backend;
- `notify-send` for desktop error notifications.

The Niri provider talks directly to `$NIRI_SOCKET`, so the `niri` executable is
not required at runtime. When a systemd user service does not inherit
`NIRI_SOCKET`, the provider falls back to the newest `niri*.sock` in
`$XDG_RUNTIME_DIR`.

## Local setup

From a checkout:

```bash
python -m venv --system-site-packages .venv
. .venv/bin/activate
pip install -e '.[overlay]'
```

Use `pip install -e .` for a headless setup. `--system-site-packages` lets the
virtual environment see distro packages such as `python-gobject` while pip
installs the `maic` entry point and `websockets`.

Install the cased Sparrowhawk ITN grammars once if you want inverse text
normalization. Grammars are loaded lazily; in NeMo-Speech 0.2.0 the Italian
(`it`) directory exists but is empty.

```bash
mkdir -p ~/.local/share/nemo-speech/itn
curl -fL \
  https://github.com/NVIDIA/NeMo-Speech.cpp/releases/download/v0.2.0/itn_configs_cased.tar.bz2 \
  -o /tmp/itn_configs_cased.tar.bz2
tar -xjf /tmp/itn_configs_cased.tar.bz2 \
  -C ~/.local/share/nemo-speech/itn \
  --strip-components=1
```

## Running the stack locally

Start the ASR server in one terminal:

```bash
nemo-speech serve \
  --asr-model nemotron-3.5 \
  --host 127.0.0.1 --port 8080 \
  --asr.streaming.rnnt_right_context 6 \
  --itn-model-dir ~/.local/share/nemo-speech/itn
```

Start the daemon in another:

```bash
cp config/config.toml ~/.config/maic/config.toml
maic daemon
```

For UI-free work:

```bash
maic daemon --no-overlay
```

Useful client commands:

```bash
maic status
maic toggle
maic cancel
maic language
maic language it-IT
maic type "diagnostic text"
```

The overlay needs `gtk4-layer-shell` to be linked before GTK's
`libwayland-client`, otherwise GTK falls back to a normal centred window. Use
the shipped launcher for manual runs:

```bash
scripts/maic-layer-shell daemon
```

The systemd unit sets the same `LD_PRELOAD` in its service environment. Do not
move this workaround into application logic.

## Tests and quality checks

Development tooling is managed with [uv](https://docs.astral.sh/uv/). The
`dev` dependency group includes [Ruff](https://docs.astral.sh/ruff/) for
formatting and linting, and [ty](https://docs.astral.sh/ty/) for type checking:

```bash
uv sync --locked

uv run ruff format --check .
uv run ruff check .
uv run ty check
uv run python -m unittest discover -s tests -v
```

The test suite uses only the standard library:

```bash
uv run python -m unittest discover -s tests -v
```

Run a single module or test:

```bash
uv run python -m unittest tests.test_config -v
uv run python -m unittest tests.test_ipc.DaemonDispatchTests -v
```

Tests must not require GTK, a live Niri session, PipeWire, an ASR server, or
network access. Use fake providers from `tests/test_backends.py` and
`tests/test_ipc.py` as examples. `tests/test_cli_imports.py` checks that
importing the CLI does not load GTK; keep that guarantee when changing imports.

Good additions to the test suite:

- protocol/validation edge cases for config and IPC;
- delta reconciliation and transcript normalization;
- backend lifecycle tests with mocks or temporary sockets;
- clipboard fallback ordering;
- batch ASR multipart/WAV encoding.

Real-session smoke tests are useful when a disposable Niri/PipeWire session is
available, but they must not be required for the default test run.

## Repository layout

```text
maic/
  cli.py              argparse entry point and GTK/headless startup
  daemon.py           session loop, state machine, IPC server, polling
  config.py           TOML loading, defaults, environment overrides, validation
  ipc.py              versioned JSON wire protocol and validation
  text.py             provider-independent transcript normalization
  niri.py             compatibility import for the Niri backend
  asr.py              compatibility import for the nemo-speech backend
  inject.py           compatibility import for injection helpers
  overlay.py          GTK4 layer-shell overlay
  overlay_null.py     no-op overlay for headless mode
  subprocess_env.py   environment handling for child processes
  backends/
    protocols.py      backend contracts, capabilities, provider names
    __init__.py       backend factory and lazy imports
    niri.py           native Niri IPC compositor
    sway.py           native Sway i3-IPC compositor
    hyprland.py       native Hyprland IPC compositor
    auto.py           compositor auto-detection
    pulse.py          libpulse-simple capture through ctypes
    pipewire.py       pw-record capture fallback
    nemo.py           streaming nemo-speech WebSocket ASR
    batch.py          OpenAI/whisper.cpp batch HTTP ASR
    wtype.py          wtype injection and clipboard orchestration
    clipboard.py      Gdk/wl-copy clipboard providers
    wayland_clipboard.py native ext-data-control-v1 clipboard
config/config.toml    example configuration
systemd/              user service units
scripts/              layer-shell launcher
tests/                stdlib unittest suite
docs/                 architecture, development, configuration, protocol
```

## How the pieces fit together

The CLI is a thin Unix-socket client. The daemon owns all long-lived state:
the audio reader, ASR WebSocket, overlay, focus target, clipboard ownership,
and server readiness. `maic toggle` sends one JSON request and exits.

`daemon.Daemon` depends on the protocols in `maic/backends/protocols.py`, not
on concrete backends. The factory in `maic/backends/__init__.py` constructs
the configured provider set. This seam is the main extensibility point:
compositor, audio capture, injection, ASR, and overlay can be replaced without
rewriting the session loop.

Backend imports are deliberately lazy. The CLI and `maic daemon --no-overlay`
must work without GTK installed. Keep module-level imports light, especially
outside the overlay path.

The GTK overlay runs on the GLib main loop while the daemon runs its asyncio
loop in a dedicated thread. Overlay methods are thread-safe entry points; do
not call GTK directly from arbitrary daemon threads.

## Adding or changing a backend

1. Read the relevant protocol and capability dataclass in
   `maic/backends/protocols.py`.
2. Implement the protocol in a new module under `maic/backends/`. Import
   optional system libraries inside functions or `__init__`, not at module
   import time, unless the backend is always required.
3. Add the provider name to the matching tuple in `protocols.py` and to the
   factory in `maic/backends/__init__.py`.
4. Add config validation in `maic/config.py` if the provider has new settings.
5. Add or update tests using fakes/mocks; no live desktop session should be
   required.
6. Document the provider in `docs/configuration.md` and, if the design changes,
   in `docs/architecture.md`.

Prefer the smallest useful abstraction. Do not add a second implementation
only to justify a seam; the protocols exist so new implementations can be added
without destabilising the daemon.

## Debugging

- Increase logging with `MAIC_LOG=DEBUG`.
- Inspect daemon state with `maic status`; it reports state, language, server
  readiness, overlay availability, and the captured target.
- Use `socat` or `nc` against `$XDG_RUNTIME_DIR/maic.sock` for raw IPC
  diagnostics. Send one newline-terminated JSON object per connection; see
  [ipc.md](ipc.md).
- Use `maic type "hello"` to test injection without recording.
- Check the ASR server readiness endpoint directly:

  ```bash
  curl -fsS http://127.0.0.1:8080/ready
  ```

- Under systemd, inspect `journalctl --user -u maic -f` and
  `journalctl --user -u maic-server -f`.
- For overlay placement issues, verify `LD_PRELOAD` is set to
  `/usr/lib/libgtk4-layer-shell.so` in the daemon environment.
- If the ASR server is offline, the daemon fails before recording rather than
  after the user has spoken.

## Libraries and technology choices

| Dependency | Role | Notes |
| --- | --- | --- |
| Python `asyncio` | Session loop, IPC, audio chunking, subprocesses | Standard library. |
| `ctypes` | Native PulseAudio and Wayland clipboard bindings | Standard library; no C extension build step. |
| `tomllib` | Configuration parsing | Standard library (Python 3.11+). |
| `wave`, `urllib`, `json` | Batch ASR WAV encoding and HTTP calls | Standard library. |
| `websockets` | Streaming nemo-speech client | The only required PyPI dependency. |
| `PyGObject` + GTK4 | Optional overlay and GLib main loop | Distro packages; `.[overlay]` extra adds the Python packages. |
| `gtk4-layer-shell` | Focusless layer-shell surface | Requires launcher/systemd `LD_PRELOAD` workaround on Arch. |
| `pycairo` | Overlay drawing support | Optional extra. |
| `libpulse-simple` / `libpulse` | Native microphone capture | Provided by PipeWire's `pipewire-pulse`. |
| `libwayland-client` | Native `ext-data-control-v1` clipboard | Falls back to `wl-copy` when unavailable. |
| `wtype` | Final text injection | External executable, invoked asynchronously. |
| `nemo-speech` | Streaming ASR server | Separate process/service, not embedded. |
| `unittest` | Test suite | Standard library only. |

There is no build system beyond `setuptools` in `pyproject.toml`. Versioning is
currently `0.1.0` in `maic/__init__.py`; the IPC protocol version is separate
and lives in `maic/ipc.py`.

## Documentation expectations

Update the relevant document whenever behaviour or public interfaces change:

- user-facing configuration → `configuration.md`;
- process/session/backend design → `architecture.md`;
- socket request/response shapes → `ipc.md`;
- new contributor workflow or tooling → this file;
- planned work → `roadmap.md` or `backlog.md`.

Keep the README short. It is an entry point, not the full manual.
