# Daemon IPC protocol

Status: **version 1**. The protocol is **private to the bundled `maic` CLI**.
It is documented so the CLI and daemon can evolve together and so debugging
tools can inspect it, but it is not yet a supported third-party client API.
Third-party clients must not rely on it until this document says otherwise.

## Transport

- Unix stream socket at `$XDG_RUNTIME_DIR/maic.sock`, or
  `/tmp/maic-<uid>.sock` when `XDG_RUNTIME_DIR` is unset.
- Socket mode is `0600`; the daemon is single-user and single-instance.
- One request and one response per connection.
- Messages are UTF-8 JSON objects terminated by a newline (`\n`).
- Requests larger than 64 KiB are rejected.
- A stale socket is replaced only if no daemon answers on it.

The CLI is normally the only client. `socat`/`nc` can be used for diagnostics.

## Versioning

Every new request and response contains `"protocol": 1`. The daemon treats a
missing protocol field as version 1 for compatibility with the first CLI
builds, but new clients always send it.

If the versions differ, the daemon returns a `protocol_mismatch` error and the
CLI refuses a response from a daemon with a different protocol version. There
is no negotiation or fallback: upgrade the daemon and CLI together.

## Requests

Common envelope:

```json
{"protocol": 1, "command": "status"}
```

Supported commands:

| Command | Fields | Meaning |
| --- | --- | --- |
| `status` | none | Return the status snapshot. |
| `toggle` | `clipboard` (boolean, default `false`) | Start recording when idle; stop and commit when recording/starting. |
| `stop` | none | Stop and commit the current recording. No-op when idle. |
| `cancel` | none | Discard the current recording without injecting text. |
| `language` | `language` (string, optional) | With a non-empty value, set the next session's ASR language. Without one, read the current value. `auto` means model detection. |
| `type` | `text` (string, required) | Diagnostic direct injection through the configured injector. |

Examples:

```json
{"protocol": 1, "command": "toggle", "clipboard": true}
{"protocol": 1, "command": "language", "language": "it-IT"}
{"protocol": 1, "command": "language"}
{"protocol": 1, "command": "type", "text": "hello"}
```

## Responses

Every response contains `"protocol": 1` and a boolean `"ok"`. Successful
responses add command-specific fields:

```json
{"protocol": 1, "ok": true, "state": "recording"}
{"protocol": 1, "ok": true, "language": "en-US"}
```

`status` returns the snapshot documented below. `type` returns
`{"ok": true, "state": "<current state>"}` on success.

### Errors

A failed response has a stable machine-readable `code` and a human-readable
`error` string:

```json
{
  "protocol": 1,
  "ok": false,
  "code": "unknown_command",
  "error": "unknown command: frobnicate"
}
```

Defined error codes:

| Code | Meaning |
| --- | --- |
| `bad_request` | Malformed JSON, wrong envelope, oversized request, or invalid message shape. |
| `protocol_mismatch` | Client and daemon use different protocol versions. |
| `unknown_command` | The command name is not implemented. |
| `invalid_argument` | A command field has the wrong type or is missing. |
| `injection_failed` | The `type` command could not inject text. |
| `internal_error` | Unexpected daemon failure; details are in the error string and the journal. |

The CLI prints the `error` text and exits non-zero for any failed response.

## Status snapshot

`status` returns the current daemon view:

```json
{
  "protocol": 1,
  "ok": true,
  "state": "idle",
  "language": "en-US",
  "server_ready": true,
  "server_status": "online",
  "overlay": true,
  "version": "0.0.1",
  "session": null
}
```

Fields:

- `state`: one of `idle`, `starting`, `recording`, `stopping`,
  `cancelling`, `injecting`.
- `language`: the language that will be sent for the next session.
- `server_ready`: `true`, `false`, or `null` before the first readiness probe.
- `server_status`: derived from `server_ready`: `online`, `offline`, or
  `checking`.
- `overlay`: whether the GTK overlay is enabled for this daemon.
- `version`: the mAIcrophone application version (not the protocol version).
- `session`: `null` when idle, otherwise an object with the captured target:
  `{"target": {"id": 42, "pid": 7, "app_id": "kitty", "title": "..."}}`.

The target is captured at recording start. It is the window that will receive
the transcript if focus refocusing is available.

## Compatibility policy

- The protocol version changes when request/response shapes or semantics change
  incompatibly.
- Additive fields may be introduced without a version bump; clients must ignore
  unknown response fields.
- The socket protocol remains CLI-private for now. A supported client API would
  require a separate decision covering stability guarantees, authentication for
  multi-user systems, and documented error recovery. The versioned envelope is
  the seam that makes that possible later.
