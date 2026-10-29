"""Backend provider registry and factory."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .protocols import (
    AUDIO_PROVIDERS,
    ASR_PROVIDERS,
    COMPOSITOR_PROVIDERS,
    INJECTION_PROVIDERS,
    Backends,
    Clipboard,
)

if TYPE_CHECKING:
    from ..config import Config


def _build_compositor(name: str):
    if name == "niri":
        from .niri import NiriCompositor

        return NiriCompositor()
    raise ValueError(
        f"unknown backend.compositor {name!r}; choose from {', '.join(COMPOSITOR_PROVIDERS)}"
    )


def _build_audio(name: str, cfg: Config):
    if name == "pw-record":
        from .pipewire import PwRecordAudioCapture

        return PwRecordAudioCapture(cfg)
    raise ValueError(
        f"unknown backend.audio {name!r}; choose from {', '.join(AUDIO_PROVIDERS)}"
    )


def _build_injection(name: str, cfg: Config, clipboard: Clipboard | None = None):
    if name == "wtype":
        from .wtype import WtypeInjector

        return WtypeInjector(cfg, clipboard=clipboard)
    raise ValueError(
        f"unknown backend.injection {name!r}; choose from {', '.join(INJECTION_PROVIDERS)}"
    )


def _build_asr(name: str, cfg: Config):
    if name == "nemo-speech":
        from .nemo import NemoSpeechProvider

        return NemoSpeechProvider(cfg)
    if name in {"openai-http", "faster-whisper", "whisper-cpp", "batch-http"}:
        from .batch import BatchHttpProvider

        protocol = {
            "openai-http": "openai",
            "faster-whisper": "openai",
            "whisper-cpp": "whisper-cpp",
        }.get(name)
        return BatchHttpProvider(cfg, protocol)
    raise ValueError(
        f"unknown backend.asr {name!r}; choose from {', '.join(ASR_PROVIDERS)}"
    )


def build_backends(cfg: Config, clipboard: Clipboard | None = None) -> Backends:
    """Construct the configured provider set.

    Imports are intentionally lazy so headless CLI commands and config-only
    tests do not need ASR/GTK dependencies until a daemon actually starts.
    ``clipboard`` is the optional GTK clipboard provider; injection providers
    fall back to their own subprocess implementation when it is absent.
    """
    return Backends(
        compositor=_build_compositor(cfg.backend_compositor),
        audio=_build_audio(cfg.backend_audio, cfg),
        injection=_build_injection(cfg.backend_injection, cfg, clipboard),
        asr=_build_asr(cfg.backend_asr, cfg),
    )
