from __future__ import annotations

import math

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Pango", "1.0")
from gi.repository import Gdk, GLib, Gtk, Pango  # noqa: E402

from .config import Config  # noqa: E402
from .niri import OutputInfo  # noqa: E402


CSS = b"""
window.maic-overlay {
    background-color: rgba(18, 18, 22, 0.94);
    border: 1px solid rgba(255, 255, 255, 0.18);
    border-radius: 10px;
}
.maic-box {
    padding: 10px 14px;
}
.maic-status {
    font-family: monospace;
    font-size: 10pt;
    color: #9aa0aa;
}
.maic-text {
    font-family: monospace;
    font-size: 12pt;
    color: #f0f0f0;
}
.maic-text.small { font-size: 11pt; }
.maic-text.smaller { font-size: 10pt; }
.maic-error {
    font-family: monospace;
    font-size: 11pt;
    color: #ff8a8a;
}
"""

LEVELS = "▁▂▃▄▅▆▇█"


def _load_layer_shell():
    try:
        gi.require_version("Gtk4LayerShell", "1.0")
        from gi.repository import Gtk4LayerShell as layer_shell
    except (ValueError, ImportError) as exc:
        raise RuntimeError(
            "gtk4-layer-shell is missing; install the Arch package "
            "'gtk4-layer-shell' (or set overlay.enabled = false)"
        ) from exc
    return layer_shell


def vu_bar(rms: float, width: int = 14) -> str:
    if rms <= 0:
        return " " * width
    level = min(1.0, max(0.0, (rms / 2500.0) ** 0.5))
    filled = int(round(level * width))
    return "█" * filled + " " * (width - filled)


def language_label(code: str) -> str:
    code = (code or "auto").strip()
    if code.lower() == "auto":
        return "AUTO"
    return code.split("-")[0].upper()


class Overlay:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.layer_shell = _load_layer_shell()
        self.window: Gtk.Window | None = None
        self.status: Gtk.Label | None = None
        self.text: Gtk.Label | None = None
        self.width = 800
        self.max_height = 400
        self.output: OutputInfo | None = None
        self._hide_source: int | None = None
        self._last_text = ""
        self._last_rms = 0.0
        self._state = "idle"
        self.language = language_label(cfg.language)
        self._build()

    def _build(self) -> None:
        self.window = Gtk.Window()
        self.window.add_css_class("maic-overlay")
        self.window.set_decorated(False)
        self.window.set_resizable(False)
        self.window.set_can_focus(False)
        self.window.set_can_target(False)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.add_css_class("maic-box")
        self.status = Gtk.Label(xalign=0)
        self.status.add_css_class("maic-status")
        self.text = Gtk.Label(xalign=0)
        self.text.add_css_class("maic-text")
        self.text.set_wrap(True)
        self.text.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self.text.set_selectable(False)
        box.append(self.status)
        box.append(self.text)
        self.window.set_child(box)

        display = Gdk.Display.get_default()
        if display is not None:
            provider = Gtk.CssProvider()
            provider.load_from_data(CSS)
            Gtk.StyleContext.add_provider_for_display(
                display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
            )

        self.layer_shell.init_for_window(self.window)
        self.layer_shell.set_layer(self.window, self.layer_shell.Layer.OVERLAY)
        self.layer_shell.set_namespace(self.window, "maic-overlay")
        self.layer_shell.set_anchor(self.window, self.layer_shell.Edge.BOTTOM, True)
        self.layer_shell.set_anchor(self.window, self.layer_shell.Edge.LEFT, True)
        self.layer_shell.set_anchor(self.window, self.layer_shell.Edge.RIGHT, False)
        self.layer_shell.set_keyboard_mode(
            self.window, self.layer_shell.KeyboardMode.NONE
        )
        self.window.set_visible(False)

    def _find_monitor(self, output_name: str):
        display = Gdk.Display.get_default()
        if display is None:
            return None
        monitors = display.get_monitors()
        for i in range(monitors.get_n_items()):
            monitor = monitors.get_item(i)
            try:
                connector = monitor.get_connector()
            except Exception:
                connector = None
            if connector == output_name:
                return monitor
        return None

    def _calc_height(self, text: str, font_size: int) -> int:
        chars_per_line = max(20, int(self.width / (font_size * 0.62)))
        lines = 0
        for para in text.splitlines() or [""]:
            lines += max(1, math.ceil(len(para) / chars_per_line))
        line_height = int(font_size * 1.55)
        return min(self.max_height, max(72, 42 + lines * line_height))

    def _apply_text(self, text: str) -> None:
        assert self.text is not None and self.window is not None
        self.text.remove_css_class("small")
        self.text.remove_css_class("smaller")
        height = self._calc_height(text, 12)
        if height >= self.max_height:
            self.text.add_css_class("small")
            height = self._calc_height(text, 11)
        if height >= self.max_height:
            self.text.add_css_class("smaller")
            height = self._calc_height(text, 10)
        self.text.set_text(text)
        self.window.set_size_request(self.width, height)
        self.window.set_default_size(self.width, height)
        self.window.queue_resize()

    def _start_ui(self, output: OutputInfo, state: str) -> None:
        assert self.window is not None and self.status is not None
        self.output = output
        self.width = min(
            self.cfg.overlay_max_width_px,
            max(320, int(output.width * self.cfg.overlay_width_percent / 100)),
        )
        self.max_height = max(120, int(output.height * self.cfg.overlay_max_height_percent / 100))
        monitor = self._find_monitor(output.name)
        if monitor is not None:
            self.layer_shell.set_monitor(self.window, monitor)
        left_margin = max(
            self.cfg.overlay_margin_px,
            (output.width - self.width) // 2,
        )
        self.layer_shell.set_margin(
            self.window, self.layer_shell.Edge.LEFT, left_margin
        )
        self.layer_shell.set_margin(
            self.window, self.layer_shell.Edge.BOTTOM, self.cfg.overlay_margin_px
        )
        self._state = state
        self._last_text = ""
        self._last_rms = 0.0
        self.status.set_text(self._status_text(state, 0.0))
        self._apply_text("")
        self.window.set_visible(True)
        return False

    def _status_text(self, state: str, rms: float) -> str:
        return f"{state:<6} {vu_bar(rms)}  {self.language}"

    def start(self, output: OutputInfo) -> None:
        GLib.idle_add(self._start_ui, output, "REC")

    def set_partial(self, text: str) -> None:
        self._last_text = text
        GLib.idle_add(self._update_text, text)

    def _update_text(self, text: str) -> bool:
        if not self.window or not self.window.get_visible():
            return False
        self._apply_text(text)
        return False

    def set_level(self, rms: float) -> None:
        self._last_rms = rms
        GLib.idle_add(self._update_level, rms)

    def _update_level(self, rms: float) -> bool:
        if not self.window or not self.window.get_visible() or self.status is None:
            return False
        self.status.set_text(self._status_text(self._state, rms))
        return False

    def set_state(self, state: str) -> None:
        self._state = state
        GLib.idle_add(self._update_state, state)

    def _update_state(self, state: str) -> bool:
        if self.status is not None:
            self.status.set_text(self._status_text(state, self._last_rms))
        return False

    def set_language(self, code: str) -> None:
        self.language = language_label(code)
        GLib.idle_add(self._update_language)

    def _update_language(self) -> bool:
        if self.status is not None:
            self.status.set_text(self._status_text(self._state, self._last_rms))
        return False

    def show_error(self, message: str, timeout_ms: int = 5000) -> None:
        GLib.idle_add(self._show_error_ui, message, timeout_ms)

    def _show_error_ui(self, message: str, timeout_ms: int) -> bool:
        if not self.window or not self.status or not self.text:
            return False
        self.status.set_text(self._status_text("ERR", self._last_rms))
        self.text.set_text(message)
        self.text.add_css_class("maic-error")
        self.window.set_size_request(self.width, 100)
        self.window.set_default_size(self.width, 100)
        self.window.set_visible(True)
        if self._hide_source is not None:
            GLib.source_remove(self._hide_source)
        self._hide_source = GLib.timeout_add(timeout_ms, self._hide_error)
        return False

    def _hide_error(self) -> bool:
        self._hide_source = None
        if self.text is not None:
            self.text.remove_css_class("maic-error")
        self.hide()
        return False

    def hide(self) -> None:
        GLib.idle_add(self._hide_ui)

    def _hide_ui(self) -> bool:
        if self.window is not None:
            self.window.set_visible(False)
        return False

    def destroy(self) -> None:
        GLib.idle_add(self._destroy_ui)

    def _destroy_ui(self) -> bool:
        if self.window is not None:
            self.window.destroy()
            self.window = None
        return False


class NullOverlay:
    def start(self, output): pass
    def set_partial(self, text): pass
    def set_level(self, rms): pass
    def set_state(self, state): pass
    def set_language(self, code): pass
    def show_error(self, message, timeout_ms=5000): pass
    def hide(self): pass
    def destroy(self): pass
