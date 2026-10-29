"""Backend contracts for mAIcrophone.

The daemon and session loop depend on these protocols only. Concrete
implementations live in sibling modules under :mod:`maic.backends` and are
selected through configuration.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

# Provider names accepted by the configuration. Keep these tuples in sync with
# the factories in :mod:`maic.backends`.
COMPOSITOR_PROVIDERS = ("niri",)
AUDIO_PROVIDERS = ("pulse", "pw-record")
INJECTION_PROVIDERS = ("wtype",)
ASR_PROVIDERS = (
    "nemo-speech",
    "openai-http",
    "faster-whisper",
    "whisper-cpp",
    "batch-http",
)
OVERLAY_PROVIDERS = ("gtk", "null")


@dataclass(frozen=True)
class TargetWindow:
    """Identity of the window that should receive the transcript."""

    id: int
    pid: int
    app_id: str
    title: str


@dataclass(frozen=True)
class OutputInfo:
    """Logical geometry of a compositor output."""

    name: str
    width: int
    height: int


@dataclass(frozen=True)
class AudioStats:
    rms: float = 0.0
    peak: int = 0


@dataclass(frozen=True)
class CompositorCapabilities:
    can_refocus: bool = False
    can_check_windows: bool = True
    can_query_output: bool = True


@dataclass(frozen=True)
class AudioCapabilities:
    sample_rate: int = 16000
    channels: int = 1


@dataclass(frozen=True)
class InjectionCapabilities:
    supports_unicode: bool = True
    supports_clipboard: bool = True
    supports_primary_selection: bool = True


@dataclass(frozen=True)
class AsrCapabilities:
    supports_streaming: bool = True
    supports_partials: bool = True
    supports_commit: bool = True
    supports_cancel: bool = True
    supports_punctuation: bool = True
    supports_itn: bool = True
    # Endpointing means the server may produce a final without an explicit
    # commit from the client. The core session loop does not require it.
    supports_endpointing: bool = False
    # Empty means the provider accepts arbitrary language tags.
    languages: tuple[str, ...] = ()


AsrEventKind = Literal["partial", "final", "committed", "error"]


@dataclass(frozen=True)
class AsrEvent:
    """A normalized event emitted by an ASR session.

    ``partial`` carries a best-effort in-progress transcript, ``final`` the
    authoritative transcript, ``committed`` signals that the server accepted
    the end of the audio buffer, and ``error`` carries a user-facing message.
    """

    kind: AsrEventKind
    text: str = ""

    @classmethod
    def partial(cls, text: str) -> AsrEvent:
        return cls("partial", text)

    @classmethod
    def final(cls, text: str) -> AsrEvent:
        return cls("final", text)

    @classmethod
    def committed(cls) -> AsrEvent:
        return cls("committed")

    @classmethod
    def error(cls, message: str) -> AsrEvent:
        return cls("error", message)


AsrEventListener = Callable[[AsrEvent], None]


@runtime_checkable
class Compositor(Protocol):
    capabilities: CompositorCapabilities

    async def focused_window(self) -> TargetWindow | None: ...

    async def focused_output(self) -> OutputInfo | None: ...

    async def window_exists(self, window_id: int) -> bool: ...

    async def current_focused_id(self) -> int | None: ...

    async def focus_window(self, window_id: int) -> bool: ...


@runtime_checkable
class AudioCapture(Protocol):
    capabilities: AudioCapabilities

    async def start(self) -> None: ...

    async def read_chunk(self) -> bytes: ...

    async def stop(self) -> None: ...

    def level(self, data: bytes) -> float: ...


@runtime_checkable
class Clipboard(Protocol):
    """A clipboard destination used by an injector.

    ``primary`` selects the Wayland PRIMARY selection (middle-click paste)
    instead of the regular clipboard.
    """

    async def copy(self, text: str, *, primary: bool = False) -> bool: ...


@runtime_checkable
class Injector(Protocol):
    capabilities: InjectionCapabilities

    async def type_text(self, text: str) -> bool: ...

    async def copy_to_clipboard(self, text: str) -> bool: ...


@runtime_checkable
class AsrSession(Protocol):
    capabilities: AsrCapabilities

    async def connect(self) -> None: ...

    async def send_audio(self, data: bytes) -> None: ...

    async def commit(self, timeout: float = 6.0) -> str | None: ...

    async def cancel(self) -> None: ...

    async def close(self) -> None: ...


@runtime_checkable
class AsrProvider(Protocol):
    capabilities: AsrCapabilities

    async def check_ready(self, timeout: float = 0.4) -> tuple[bool, str]: ...

    def create_session(
        self,
        *,
        on_event: AsrEventListener,
    ) -> AsrSession: ...


@runtime_checkable
class Overlay(Protocol):
    def start(self, output: OutputInfo) -> None: ...

    def set_partial(self, text: str) -> None: ...

    def set_level(self, rms: float) -> None: ...

    def set_state(self, state: str) -> None: ...

    def set_language(self, code: str) -> None: ...

    def set_server_status(self, status: str) -> None: ...

    def show_error(self, message: str, timeout_ms: int = 5000) -> None: ...

    def hide(self) -> None: ...

    def destroy(self) -> None: ...


@dataclass
class Backends:
    """Concrete providers used by a daemon instance."""

    compositor: Compositor
    audio: AudioCapture
    injection: Injector
    asr: AsrProvider
