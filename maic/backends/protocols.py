"""Backend contracts for mAIcrophone.

The daemon and session loop depend on these protocols only. Concrete
implementations live in sibling modules under :mod:`maic.backends` and are
selected through configuration.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol, runtime_checkable


# Provider names accepted by the configuration. Keep these tuples in sync with
# the factories in :mod:`maic.backends`.
COMPOSITOR_PROVIDERS = ("niri",)
AUDIO_PROVIDERS = ("pw-record",)
INJECTION_PROVIDERS = ("wtype",)
ASR_PROVIDERS = ("nemo-speech",)
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
    # Empty means the provider accepts arbitrary language tags.
    languages: tuple[str, ...] = ()


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
        on_partial: Callable[[str], None],
        on_final: Callable[[str], None],
        on_error: Callable[[str], None],
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
