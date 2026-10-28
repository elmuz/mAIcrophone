# mAIcrophone — architecture plan

Goal: make the core independent of Niri, `wtype`, `pw-record`, `nemo-speech`,
and GTK **before** expanding the feature set or deciding on a rewrite. The
product backlog stays in `TODO.md`; this file tracks the architectural
sequence.

Principle: introduce seams while there is only one implementation. Adding a
second compositor, ASR engine, or language is much more expensive after the
current coupling has hardened.

## Step 1 — contracts and portability baseline

Status: **done**.

- [x] Reconcile streaming ASR deltas instead of blind `partial += delta`.
      Full replacements and revisions are detected with a longest-common-prefix
      heuristic; ordinary suffix chunks still append.
- [x] Add a stdlib test suite (`python -m unittest discover -s tests -v`) for
      reconciliation, normalization, config parsing/validation, and CLI imports.
- [x] Make GTK lazy: the CLI and `maic daemon --no-overlay` no longer import
      `gi`/GTK or require layer-shell.
- [x] Keep `NullOverlay` in a GTK-free module (`maic.overlay_null`).

Acceptance: tests pass without GTK; headless daemon starts and answers
`maic status`; the overlay path still loads GTK only when enabled.

Known limit: the ASR delta protocol does not say whether an event is a suffix
or a replacement. A revision that changes the first characters can still be
mistaken for a suffix. Revisit if the server exposes fuller partial events.

## Step 2 — backend seams

Status: **next**.

- [ ] Define protocols/ABCs for:
      - compositor: focused window/output, window existence, refocus;
      - audio capture: start/read/stop/level;
      - injection: type text, clipboard/primary selection;
      - ASR session: connect/send/commit/cancel/close plus capabilities;
      - overlay: the existing `Overlay`/`NullOverlay` surface.
- [ ] Move the current implementations under `maic/backends/`: Niri,
      `pw-record`, `wtype`/`wl-copy`, nemo-speech. Existing modules may remain
      as thin provider modules at first.
- [ ] Add explicit capabilities (`can_refocus`, `supports_unicode`,
      `supports_streaming`, `supports_partials`, `supports_commit`, languages,
      punctuation, ITN).
- [ ] Select providers in config (`[backend]` or equivalent) with validation.
- [ ] Keep `daemon.py` depending only on the protocols, not concrete modules.

Acceptance: the daemon can be constructed with fake providers in tests, and no
Niri/`pw-record`/`wtype`/nemo-specific logic remains in the session loop.

Avoid: writing a second compositor or ASR backend before the interface has
tests and the current provider passes through it.

## Step 3 — ASR abstraction

Status: backlog.

- [ ] Formalize `AsrSession` events: `partial`, `final`, `committed`, `error`.
- [ ] Move readiness/health probing into the engine provider.
- [ ] Keep nemo-speech's WebSocket, `/ready`, commit, and delta/revision behavior
      inside its implementation.
- [ ] Add a batch adapter that buffers PCM and transcribes on stop, enabling
      whisper.cpp/faster-whisper-style servers.
- [ ] Make the session flow adapt to capabilities (streaming vs batch,
      partials optional, endpointing optional).

Acceptance: at least one non-streaming engine can be selected without changing
`daemon.py` or the overlay contract.

## Step 4 — IPC, clipboard, packaging

Status: backlog.

- [ ] Version and document the Unix-socket JSON commands, responses, errors, and
      status snapshot.
- [ ] Decide whether the socket protocol is private CLI-only or a supported
      client API.
- [ ] Replace `wl-copy` with `Gdk.Clipboard` where the GTK daemon is available;
      keep the subprocess as a fallback.
- [ ] Treat GTK/GI as an optional overlay backend for packaging; core daemon and
      CLI should install and run without it.
- [ ] Move the layer-shell `LD_PRELOAD` re-exec out of application logic into a
      launcher/systemd concern.

Acceptance: a headless install can run the daemon and CLI; the overlay package
adds GTK as an explicit optional dependency.

## Step 5 — language / Rust decision

Status: deferred.

Do not rewrite in Rust now. Reconsider only if one of these becomes true:

1. Distribution requires a single binary without Python/PyGObject.
2. The GTK layer-shell stack remains fragile or blocks required UI behavior.
3. Native PipeWire/libei/input APIs are needed for reliability or latency.
4. UI performance becomes a measured user-facing problem.

If triggered, port behind the Step 2 contracts. Do not run a Rust overlay and a
Python daemon as a permanent split; choose one runtime per deployment.

## Non-goals until the seams exist

- Endpointing, transcript history, multiple finals per session.
- Spoken punctuation.
- A second compositor implementation (the seams are preparation, not a promise).
- Native PipeWire/input backends, unless needed to validate a protocol.
