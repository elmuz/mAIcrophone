# Contributing

Contributions are welcome, especially around reliability on Niri/Wayland,
backend implementations, tests, and documentation. This project is a local,
single-user dictation tool: favour small, focused changes that preserve the
existing contracts.

## Before you start

- Read [architecture.md](architecture.md) for the process model, session state
  machine, and backend seams.
- Read [development.md](development.md) for setup, tests, and the repository
  layout.
- Check [backlog.md](backlog.md) and [roadmap.md](roadmap.md) before starting
  work that is already planned or explicitly deferred.
- For a substantial change, open an issue or describe the approach first. Small
  bug fixes and documentation improvements can go straight to a pull request.

The current scope is Niri on Wayland. Non-Niri support, endpointing, history,
spoken punctuation, and a Rust rewrite are intentionally deferred; see the
roadmap for the gates that would change that.

## Development workflow

1. Fork or create a branch from the current default branch.
2. Set up the environment described in [development.md](development.md).
3. Make the smallest coherent change.
4. Add or update tests for behaviour changes.
5. Update the relevant documentation.
6. Run the full test suite:

   ```bash
   python -m unittest discover -s tests -v
   ```

7. Open a pull request with a short description of the problem, the approach,
   and how you tested it. Include desktop-specific verification details when a
   change affects Niri, audio, or the overlay.

Please do not mix unrelated refactors, formatting-only changes, and feature work
in one pull request. It makes review and rollback harder.

## What to contribute

Useful areas, roughly in order of value:

- **Tests for native backends.** Niri IPC, the native clipboard, and Pulse
  capture are covered by protocol/lifecycle tests; more edge cases are always
  useful. Real-session smoke tests are welcome but must remain optional.
- **Reliability fixes.** Focus changes, missing windows, server disconnects,
  clipboard ownership, and audio device changes are the places where bugs tend
  to appear.
- **Backend implementations.** New compositors, ASR transports, or injection
  methods should implement the existing protocols rather than adding
  conditionals to `daemon.py`.
- **Configuration and diagnostics.** Clearer validation errors, `maic doctor`-style
  checks, and better journald messages.
- **Documentation.** Setup notes for other distributions, troubleshooting
  recipes, and clarifications are valuable.

Before implementing a new backend, consider whether the protocol in
`maic/backends/protocols.py` is sufficient. If it is not, propose the contract
change separately.

## Coding conventions

- Target Python 3.11+. Use standard-library features such as `tomllib` and
  `asyncio` where they fit.
- Keep the daemon independent of concrete backends. It should depend on the
  protocols and capabilities, not on Niri/GTK/nemo-speech internals.
- Keep imports lazy on optional paths. Importing the CLI or starting a headless
  daemon must not load GTK or require the GUI stack.
- Do not put `gtk4-layer-shell` preload logic in application code. It belongs in
  the launcher and systemd unit.
- Prefer clear, small modules and explicit error messages. Config and protocol
  validation should name the offending value and the accepted alternatives.
- Avoid broad exception handling in the session loop unless it preserves a
  documented fallback (for example, clipboard on injection failure).
- Do not persist raw audio or transcripts by default. Logging should stay
  useful without leaking dictated content.
- Keep the IPC protocol private to the bundled CLI unless a deliberate
  compatibility decision is documented in [ipc.md](ipc.md).

## Testing expectations

The default suite uses only `unittest` and must run without a live desktop:

```bash
python -m unittest discover -s tests -v
```

For behaviour changes, add tests at the lowest useful level:

- pure helpers: reconciliation, normalization, validation;
- backends: fake sockets/subprocesses and lifecycle assertions;
- daemon: fake providers through the `Backends` dataclass;
- IPC: envelope, error codes, and status snapshots.

`tests/test_cli_imports.py` enforces the GTK-free CLI path. If you add a new
eager import, make sure it does not break that test.

If you manually test against a live Niri/PipeWire session, mention what you ran
and any relevant environment details in the pull request.

## Commit messages

The repository uses short, conventional-style subjects, for example:

```text
fix: reconcile ASR partials and decouple CLI from GTK
feat: add ASR providers and versioned IPC packaging
refactor: introduce backend protocols and provider selection
```

Use `feat`, `fix`, `refactor`, `docs`, `test`, or `chore` where appropriate.
Keep the subject imperative and under about 72 characters. The body should
explain why the change is needed, not restate the diff.

## Updating documentation

When a change affects users or contributors, update the matching document:

- configuration or service behaviour → [configuration.md](configuration.md);
- architecture, state machine, or backend contracts →
  [architecture.md](architecture.md);
- socket messages or errors → [ipc.md](ipc.md);
- setup, tests, tooling, or libraries → [development.md](development.md);
- planned or deferred work → [roadmap.md](roadmap.md) or
  [backlog.md](backlog.md).

Keep the README as a short entry point and link to these documents instead of
duplicating their content.

## Reporting bugs

Include:

- the output of `maic status`;
- the relevant journal lines (`journalctl --user -u maic` and
  `maic-server`);
- whether the server was online, and whether the overlay was enabled;
- the compositor/session type (Niri version, PipeWire/PulseAudio setup);
- reproduction steps and whether `maic type "hello"` works.

Remove dictated text or other private content from logs before posting.
