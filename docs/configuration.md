# Configuration and operations

mAIcrophone is configured with one TOML file plus a small number of environment
variables. There is no settings GUI.

## Config file location

The daemon looks for:

```text
$MAIC_CONFIG
```

if set; otherwise:

```text
$XDG_CONFIG_HOME/maic/config.toml
```

`$XDG_CONFIG_HOME` defaults to `~/.config`. A start-up copy can be made from
the example:

```bash
mkdir -p ~/.config/maic
cp config/config.toml ~/.config/maic/config.toml
```

The daemon also accepts `maic daemon --config /path/to/config.toml`.

Precedence is:

1. built-in defaults;
2. values from the TOML file;
3. `MAIC_SERVER_URL` and `MAIC_LANGUAGE` environment overrides.

`MAIC_CONFIG` only selects the file. `MAIC_LOG` controls log verbosity, for
example `MAIC_LOG=DEBUG`.

Invalid provider names, out-of-range values, and unsupported `multiline` modes
are rejected when the config is loaded. Start-up fails with a message naming
the bad setting.

## Example

```toml
[server]
url = "ws://127.0.0.1:8080/v1/audio/transcriptions/realtime"
poll_seconds = 5

[backend]
compositor = "niri"         # niri
audio = "pulse"             # pulse | pw-record
injection = "wtype"         # wtype
asr = "nemo-speech"         # nemo-speech | openai-http | faster-whisper |
                            # whisper-cpp | batch-http
overlay = "gtk"             # gtk | null

[audio]
source = ""                 # empty = default PipeWire source
sample_rate = 16000
chunk_ms = 100

[asr]
language = "en-US"
punctuation = true

[overlay]
enabled = true
width_percent = 75
max_width_px = 2000
max_height_percent = 30
margin_px = 56
font = "FiraCode Nerd Font Mono 13"
max_lines = 10
min_font_size = 11
font_steps = 3
corner_radius = 10
border_width = 1
background_opacity = 0.94

[injection]
copy_to_clipboard = true
primary_selection = true
return_to_target = true
multiline = "space"         # space | shift-enter | clipboard

[replacements]
"Pipewire" = "PipeWire"
"System D" = "systemd"
"Enemmotron" = "Nemotron"
```

## Server

| Key | Default | Meaning |
| --- | --- | --- |
| `server.url` | `ws://127.0.0.1:8080/v1/audio/transcriptions/realtime` | nemo-speech realtime WebSocket endpoint, or the base/endpoint for batch ASR. |
| `server.poll_seconds` | `5` | How often the overlay's server indicator is refreshed. Range 1–60. |

Before each recording the daemon probes readiness. For nemo-speech this is the
`/ready` endpoint derived from `server.url`; an offline server fails
immediately instead of after the user has spoken.

## Backends

Select providers under `[backend]`. Unknown names are rejected at start-up.
Backend imports are lazy, so selecting a provider does not load unrelated
libraries.

### Compositor

- `niri` — native Niri IPC through `$NIRI_SOCKET`.

Niri is the only supported compositor. The provider finds the focused window
and output, checks whether the target still exists, and can refocus the target
before injection.

### Audio

- `pulse` — default. Uses the PulseAudio simple API via `ctypes`; PipeWire
  implements it through `pipewire-pulse`.
- `pw-record` — subprocess fallback using the `pw-record` executable.

Both produce PCM16, mono, at `sample_rate`. `chunk_ms` controls the WebSocket
chunk size. `audio.source` is empty for the system default source, or a source
name from `wpctl status`.

### ASR

Streaming providers:

- `nemo-speech` — realtime WebSocket, live partials, commit/cancel, ITN.

Batch providers buffer audio until stop and then upload a WAV:

- `openai-http` — OpenAI-compatible transcription endpoint;
- `faster-whisper` — OpenAI-compatible faster-whisper server;
- `whisper-cpp` — whisper.cpp server;
- `batch-http` — generic batch endpoint.

Batch mode emits no live partials. Optional `[asr]` settings:

```toml
[asr]
# batch_url = "http://127.0.0.1:8081/v1/audio/transcriptions"
# batch_ready_url = "http://127.0.0.1:8081/"
# batch_protocol = "openai"     # openai | whisper-cpp
# batch_model = "whisper-1"
# batch_timeout = 60.0
```

If `batch_url` is empty, the provider derives an endpoint from `server.url` when
it is HTTP(S), or uses a protocol default (`127.0.0.1:8081` for OpenAI-compatible
servers, `127.0.0.1:8080/inference` for whisper.cpp).

### Injection

- `wtype` — final text is typed with `wtype`; Unicode is supported.

`[injection]` settings:

| Key | Default | Meaning |
| --- | --- | --- |
| `copy_to_clipboard` | `true` | Copy the final transcript to the regular clipboard. |
| `primary_selection` | `true` | Also own the PRIMARY selection for middle-click paste. |
| `return_to_target` | `true` | Refocus the original window if focus moved while recording. |
| `multiline` | `space` | `space` flattens newlines; `shift-enter` sends Shift+Enter; `clipboard` uses paste semantics. |

The native Wayland clipboard uses `ext-data-control-v1` and
`libwayland-client`. If unavailable, `wl-copy` is used when installed. The
default path works in both GTK and headless daemons.

`long_text_threshold` exists in the example config but is not yet used by the
injection path; mAIcrophone currently always tries `wtype` for the final text.

### Overlay

- `gtk` — GTK4 layer-shell overlay.
- `null` — no overlay; equivalent to `enabled = false` or
  `maic daemon --no-overlay`.

The overlay is focusless, anchored to the bottom centre of the focused output,
and grows upward up to `max_height_percent` of the monitor. It shows state, a
coloured VU meter, language, server status, and the evolving transcript. See
[architecture.md](architecture.md#7-overlay) for the layout and long-text
policy.

## Languages and ITN

The default language is `en-US`. Language is per session:

```bash
maic language          # show current value
maic language en-US
maic language it-IT
maic language auto
```

Changing it affects the next dictation without restarting the ASR server.
The overlay shows `EN`, `IT`, or `AUTO`.

Inverse text normalization requires the external cased grammars. In
NeMo-Speech 0.2.0 the Italian grammar directory is present but empty, so
Italian ITN is unavailable. Installation is covered in
[development.md](development.md#local-setup).

## Niri bindings

Add bindings to `~/.config/niri/config.kdl`, choosing keys that do not
conflict:

```kdl
Mod+Alt+V       { spawn "maic" "toggle"; }
Mod+Alt+Shift+V { spawn "maic" "cancel"; }
```

`maic toggle` starts recording when idle and stops-and-types when recording.
`maic cancel` discards the current recording. Additional diagnostics:

```bash
maic status
maic stop
maic type "hello"
```

## Running as a user service

Install the units:

```bash
mkdir -p ~/.config/systemd/user
cp systemd/maic.service ~/.config/systemd/user/
cp systemd/maic-server.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now maic-server.service
systemctl --user enable --now maic.service
```

`maic.service` starts the daemon and sets the layer-shell preload. It expects
`maic` to be on `PATH` (usually `~/.local/bin` after a user install).
`maic-server.service` runs `nemo-speech serve` with `rnnt_right_context = 6`
and the ITN directory under `~/.local/share/nemo-speech/itn`.

Useful commands:

```bash
systemctl --user status maic.service
journalctl --user -u maic.service -f
journalctl --user -u maic-server.service -f
```

For manual overlay runs without systemd, use:

```bash
scripts/maic-layer-shell daemon
```

Headless runs need neither the launcher nor GTK:

```bash
maic daemon --no-overlay
```

## Troubleshooting

**`maic: daemon is not running`**

The CLI could not connect to `$XDG_RUNTIME_DIR/maic.sock`. Start `maic daemon`
or check the user service.

**Overlay appears as a normal centred window**

`gtk4-layer-shell` is being loaded after GTK's Wayland client. Start through
`scripts/maic-layer-shell` or keep the `LD_PRELOAD` setting from
`systemd/maic.service`.

**Recording refuses to start / server offline**

Check the ASR server:

```bash
curl -fsS http://127.0.0.1:8080/ready
```

The daemon intentionally fails before capturing audio when the server is not
ready.

**No audio**

Check the default source:

```bash
wpctl status
```

Set `audio.source` to a specific source name, or switch `backend.audio` to
`pw-record` to test the alternate capture path.

**Text is not typed**

Run `maic type "hello"`. If that works, the problem is likely focus or
injection timing; if not, check that `wtype` is installed and that the daemon
can run it. The transcript may still have been copied to the clipboard.

**GTK is missing but only headless mode is needed**

Use `maic daemon --no-overlay` and install the core package without
`.[overlay]`. The CLI and headless daemon must not require GTK.
