from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .backends.protocols import (
    ASR_PROVIDERS,
    AUDIO_PROVIDERS,
    COMPOSITOR_PROVIDERS,
    INJECTION_PROVIDERS,
    OVERLAY_PROVIDERS,
)


@dataclass
class Config:
    server_url: str = "ws://127.0.0.1:8080/v1/audio/transcriptions/realtime"
    server_poll_seconds: int = 5
    language: str = "en-US"
    punctuation: bool = True
    # Batch (non-streaming) engines. Empty batch_url derives an endpoint from
    # server.url when that URL is HTTP(S), otherwise from batch_protocol.
    batch_url: str = ""
    batch_ready_url: str = ""
    batch_model: str = "whisper-1"
    batch_protocol: str = "openai"  # openai | whisper-cpp
    batch_timeout: float = 60.0
    audio_source: str = ""
    sample_rate: int = 16000
    chunk_ms: int = 100

    backend_compositor: str = "niri"
    backend_audio: str = "pulse"
    backend_injection: str = "wtype"
    backend_asr: str = "nemo-speech"
    backend_overlay: str = "gtk"

    overlay_enabled: bool = True
    overlay_width_percent: int = 75
    overlay_max_width_px: int = 2000
    overlay_max_height_percent: int = 30
    overlay_margin_px: int = 56
    overlay_font: str = "FiraCode Nerd Font Mono 13"
    overlay_max_lines: int = 10
    overlay_min_font_size: int = 11
    overlay_font_steps: int = 3
    overlay_corner_radius: int = 10
    overlay_border_width: int = 1
    overlay_background_opacity: float = 0.94

    copy_to_clipboard: bool = True
    primary_selection: bool = True
    return_to_target: bool = True
    multiline: str = "space"  # space | shift-enter | clipboard
    long_text_threshold: int = 400
    replacements: dict[str, str] = field(default_factory=dict)

    @property
    def chunk_bytes(self) -> int:
        return int(self.sample_rate * 2 * self.chunk_ms / 1000)

    @property
    def ready_url(self) -> str:
        url = self.server_url
        if url.startswith("wss://"):
            url = "https://" + url[len("wss://") :]
        elif url.startswith("ws://"):
            url = "http://" + url[len("ws://") :]
        base, _, _ = url.partition("/v1/")
        return base + "/ready"


def config_path() -> Path:
    explicit = os.environ.get("MAIC_CONFIG")
    if explicit:
        return Path(explicit).expanduser()
    base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "maic" / "config.toml"


def _section(raw: dict, name: str) -> dict:
    value = raw.get(name, {})
    return value if isinstance(value, dict) else {}


def load_config(path: Path | None = None) -> Config:
    cfg = Config()
    path = path or config_path()
    if path.exists():
        with path.open("rb") as fh:
            raw = tomllib.load(fh)
    else:
        raw = {}

    server = _section(raw, "server")
    backend = _section(raw, "backend")
    audio = _section(raw, "audio")
    asr = _section(raw, "asr")
    overlay = _section(raw, "overlay")
    injection = _section(raw, "injection")

    cfg.server_url = str(server.get("url", cfg.server_url))
    cfg.server_poll_seconds = int(
        server.get("poll_seconds", cfg.server_poll_seconds)
    )
    cfg.language = str(asr.get("language", cfg.language))
    cfg.punctuation = bool(asr.get("punctuation", cfg.punctuation))
    cfg.batch_url = str(asr.get("batch_url", cfg.batch_url))
    cfg.batch_ready_url = str(asr.get("batch_ready_url", cfg.batch_ready_url))
    cfg.batch_model = str(asr.get("batch_model", cfg.batch_model))
    cfg.batch_protocol = str(asr.get("batch_protocol", cfg.batch_protocol))
    cfg.batch_timeout = float(asr.get("batch_timeout", cfg.batch_timeout))
    cfg.audio_source = str(audio.get("source", cfg.audio_source))
    cfg.sample_rate = int(audio.get("sample_rate", cfg.sample_rate))
    cfg.chunk_ms = int(audio.get("chunk_ms", cfg.chunk_ms))

    cfg.backend_compositor = str(
        backend.get("compositor", cfg.backend_compositor)
    )
    cfg.backend_audio = str(backend.get("audio", cfg.backend_audio))
    cfg.backend_injection = str(
        backend.get("injection", cfg.backend_injection)
    )
    cfg.backend_asr = str(backend.get("asr", cfg.backend_asr))
    cfg.backend_overlay = str(backend.get("overlay", cfg.backend_overlay))

    cfg.overlay_enabled = bool(overlay.get("enabled", cfg.overlay_enabled))
    cfg.overlay_width_percent = int(
        overlay.get("width_percent", cfg.overlay_width_percent)
    )
    cfg.overlay_max_width_px = int(
        overlay.get("max_width_px", cfg.overlay_max_width_px)
    )
    cfg.overlay_max_height_percent = int(
        overlay.get("max_height_percent", cfg.overlay_max_height_percent)
    )
    cfg.overlay_margin_px = int(overlay.get("margin_px", cfg.overlay_margin_px))
    cfg.overlay_font = str(overlay.get("font", cfg.overlay_font))
    cfg.overlay_max_lines = int(
        overlay.get("max_lines", cfg.overlay_max_lines)
    )
    cfg.overlay_min_font_size = int(
        overlay.get("min_font_size", cfg.overlay_min_font_size)
    )
    cfg.overlay_font_steps = int(
        overlay.get("font_steps", cfg.overlay_font_steps)
    )
    cfg.overlay_corner_radius = int(
        overlay.get("corner_radius", cfg.overlay_corner_radius)
    )
    cfg.overlay_border_width = int(
        overlay.get("border_width", cfg.overlay_border_width)
    )
    cfg.overlay_background_opacity = float(
        overlay.get("background_opacity", cfg.overlay_background_opacity)
    )

    cfg.copy_to_clipboard = bool(
        injection.get("copy_to_clipboard", cfg.copy_to_clipboard)
    )
    cfg.primary_selection = bool(
        injection.get("primary_selection", cfg.primary_selection)
    )
    cfg.return_to_target = bool(
        injection.get("return_to_target", cfg.return_to_target)
    )
    cfg.multiline = str(injection.get("multiline", cfg.multiline))
    cfg.long_text_threshold = int(
        injection.get("long_text_threshold", cfg.long_text_threshold)
    )
    replacements = raw.get("replacements", {})
    if isinstance(replacements, dict):
        cfg.replacements = {str(k): str(v) for k, v in replacements.items()}

    env_url = os.environ.get("MAIC_SERVER_URL")
    if env_url:
        cfg.server_url = env_url
    env_lang = os.environ.get("MAIC_LANGUAGE")
    if env_lang:
        cfg.language = env_lang

    if cfg.backend_compositor not in COMPOSITOR_PROVIDERS:
        raise ValueError(
            f"invalid backend.compositor: {cfg.backend_compositor!r} "
            f"(choose from {', '.join(COMPOSITOR_PROVIDERS)})"
        )
    if cfg.backend_audio not in AUDIO_PROVIDERS:
        raise ValueError(
            f"invalid backend.audio: {cfg.backend_audio!r} "
            f"(choose from {', '.join(AUDIO_PROVIDERS)})"
        )
    if cfg.backend_injection not in INJECTION_PROVIDERS:
        raise ValueError(
            f"invalid backend.injection: {cfg.backend_injection!r} "
            f"(choose from {', '.join(INJECTION_PROVIDERS)})"
        )
    if cfg.backend_asr not in ASR_PROVIDERS:
        raise ValueError(
            f"invalid backend.asr: {cfg.backend_asr!r} "
            f"(choose from {', '.join(ASR_PROVIDERS)})"
        )
    if cfg.backend_overlay not in OVERLAY_PROVIDERS:
        raise ValueError(
            f"invalid backend.overlay: {cfg.backend_overlay!r} "
            f"(choose from {', '.join(OVERLAY_PROVIDERS)})"
        )
    if cfg.multiline not in {"space", "shift-enter", "clipboard"}:
        raise ValueError(f"invalid injection.multiline: {cfg.multiline!r}")
    if cfg.batch_protocol not in {"openai", "whisper-cpp"}:
        raise ValueError(
            f"invalid asr.batch_protocol: {cfg.batch_protocol!r} "
            "(choose from openai, whisper-cpp)"
        )
    if not 1.0 <= cfg.batch_timeout <= 600.0:
        raise ValueError("asr.batch_timeout must be between 1 and 600 seconds")
    if not 8_000 <= cfg.sample_rate <= 96_000:
        raise ValueError("audio.sample_rate must be between 8000 and 96000")
    if not 20 <= cfg.chunk_ms <= 1000:
        raise ValueError("audio.chunk_ms must be between 20 and 1000")
    if not 1 <= cfg.server_poll_seconds <= 60:
        raise ValueError("server.poll_seconds must be between 1 and 60")
    if not 1 <= cfg.overlay_max_lines <= 50:
        raise ValueError("overlay.max_lines must be between 1 and 50")
    if not 8 <= cfg.overlay_min_font_size <= 48:
        raise ValueError("overlay.min_font_size must be between 8 and 48")
    if not 0 <= cfg.overlay_font_steps <= 5:
        raise ValueError("overlay.font_steps must be between 0 and 5")
    if not 0.0 <= cfg.overlay_background_opacity <= 1.0:
        raise ValueError("overlay.background_opacity must be between 0 and 1")

    return cfg
