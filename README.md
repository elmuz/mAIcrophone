# mAIcrophone

Local, focusless push-to-talk dictation for Niri/Wayland, backed by
`nemo-speech serve` and the Nemotron 3.5 streaming ASR model.

Status: first vertical slice. It records, streams to the ASR server, shows a
focusless overlay, and types the final transcript into the window that was
focused when recording started.

## Requirements

- Arch Linux / Wayland / Niri
- Python 3.11+
- `gtk4`, `gtk4-layer-shell`, `python-gobject`
- `pipewire` (`pw-record`)
- `wtype`, `wl-copy`
- `nemo-speech` with `nemotron-3.5`

Install the GTK layer-shell package:

```bash
sudo pacman -S gtk4-layer-shell
```

On Arch, `gtk4-layer-shell` may be linked after `libwayland-client`, which makes
GTK fall back to a normal centred window. `maic daemon` automatically re-execs
itself with the documented workaround:

```bash
LD_PRELOAD=/usr/lib/libgtk4-layer-shell.so
```

The systemd unit sets the same environment variable.

## Development setup

```bash
cd ~/projects/mAIcrophone
python -m venv --system-site-packages .venv
. .venv/bin/activate
pip install -e .
```

## Run

Start the ASR server:

```bash
nemo-speech serve \
  --asr-model nemotron-3.5 \
  --host 127.0.0.1 --port 8080 \
  --asr.streaming.rnnt_right_context 6
```

Start the daemon:

```bash
maic daemon
```

In another shell:

```bash
maic status
maic toggle     # start; call again to stop and type
maic cancel     # discard current recording
maic type "hello"   # diagnostic: type directly via the daemon
```

For testing without the overlay:

```bash
maic daemon --no-overlay
```

## Niri bindings

Add to `~/.config/niri/config.kdl`, choosing keys that do not conflict:

```kdl
Mod+Alt+V       { spawn "maic" "toggle"; }
Mod+Alt+Shift+V { spawn "maic" "cancel"; }
```

## Config

Copy `config/config.toml` to `$XDG_CONFIG_HOME/maic/config.toml`
(usually `~/.config/maic/config.toml`).

The daemon connects to the server URL from config. It checks `/ready` before
starting a recording, so an offline server fails immediately instead of after
you finish speaking.

By default, the final transcript is copied to both the regular clipboard and
the PRIMARY selection (middle-click paste). Set `primary_selection = false` if
you do not want that.

## Systemd

```bash
mkdir -p ~/.config/systemd/user
cp systemd/maic.service ~/.config/systemd/user/
cp systemd/maic-server.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now maic-server.service
systemctl --user enable --now maic.service
```

## Current limitations

- Niri only.
- Overlay is capped at 30% of the monitor height; no scrolling yet.
- Newlines are flattened to spaces by default.
- No history, no endpointing, no runtime language switching.
- Partial text is best-effort; the final transcript is authoritative.
