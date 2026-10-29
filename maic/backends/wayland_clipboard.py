"""Native Wayland clipboard provider.

This is a deliberately small ``ext-data-control-v1`` client. It replaces
``wl-copy`` for the common case: own the clipboard/primary selection and serve
the selected text over a pipe when another client pastes. A dedicated thread
keeps the event queue alive, because a Wayland selection source is only valid
while its client keeps dispatching events.

The implementation uses only :mod:`ctypes` and the system
``libwayland-client.so``. It is not a general-purpose clipboard library: it
does not read clipboard contents, support drag-and-drop, or negotiate custom
MIME types beyond UTF-8 text.
"""

from __future__ import annotations

import asyncio
import ctypes
import logging
import os
import threading
from dataclasses import dataclass
from typing import Any

log = logging.getLogger("maic")

_LIBWAYLAND = "libwayland-client.so.0"
_MIME_TEXT = b"text/plain;charset=utf-8"


class _WlInterface(ctypes.Structure):
    pass


class _WlMessage(ctypes.Structure):
    _fields_ = [
        ("name", ctypes.c_char_p),
        ("signature", ctypes.c_char_p),
        ("types", ctypes.POINTER(ctypes.POINTER(_WlInterface))),
    ]


_WlInterface._fields_ = [
    ("name", ctypes.c_char_p),
    ("version", ctypes.c_int),
    ("method_count", ctypes.c_int),
    ("methods", ctypes.POINTER(_WlMessage)),
    ("event_count", ctypes.c_int),
    ("events", ctypes.POINTER(_WlMessage)),
]


class _WlArgument(ctypes.Union):
    _fields_ = [
        ("i", ctypes.c_int32),
        ("u", ctypes.c_uint32),
        ("f", ctypes.c_float),
        ("s", ctypes.c_char_p),
        ("o", ctypes.c_void_p),
        ("n", ctypes.c_uint32),
        ("a", ctypes.c_void_p),
        ("h", ctypes.c_int32),
    ]


# Wayland reads the interface descriptors copied into these arrays. Keep every
# ctypes object alive for the lifetime of the process; these tables are tiny.
_KEEP_ALIVE: list[object] = []


def _pointer(interface: Any) -> Any:
    if isinstance(interface, ctypes.POINTER(_WlInterface)):
        return interface
    return ctypes.pointer(interface)


def _make_interface(
    name: str,
    methods: list[tuple[str, str, list[_WlInterface]]],
    events: list[tuple[str, str, list[_WlInterface]]],
) -> _WlInterface:
    message_arrays: list[object] = []
    messages: list[_WlMessage] = []

    for message_name, signature, interfaces in methods + events:
        type_array = (ctypes.POINTER(_WlInterface) * max(1, len(interfaces)))()
        for index, interface in enumerate(interfaces):
            type_array[index] = _pointer(interface)
        message = _WlMessage(
            message_name.encode("ascii"),
            signature.encode("ascii"),
            ctypes.cast(type_array, ctypes.POINTER(ctypes.POINTER(_WlInterface))),
        )
        message_arrays.append(type_array)
        messages.append(message)

    method_messages = (_WlMessage * len(methods))(*messages[: len(methods)])
    event_messages = (_WlMessage * len(events))(*messages[len(methods) :])
    interface = _WlInterface(
        name.encode("ascii"),
        1,
        len(methods),
        method_messages if methods else None,
        len(events),
        event_messages if events else None,
    )
    _KEEP_ALIVE.extend([type_array, method_messages, event_messages, interface])
    return interface


class _LibWayland:
    def __init__(self) -> None:
        lib = ctypes.CDLL(_LIBWAYLAND)
        lib.wl_display_connect.argtypes = [ctypes.c_char_p]
        lib.wl_display_connect.restype = ctypes.c_void_p
        lib.wl_display_disconnect.argtypes = [ctypes.c_void_p]
        lib.wl_display_roundtrip.argtypes = [ctypes.c_void_p]
        lib.wl_display_roundtrip.restype = ctypes.c_int
        lib.wl_display_flush.argtypes = [ctypes.c_void_p]
        lib.wl_display_flush.restype = ctypes.c_int
        lib.wl_proxy_add_listener.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.c_void_p,
        ]
        lib.wl_proxy_add_listener.restype = ctypes.c_int
        lib.wl_proxy_destroy.argtypes = [ctypes.c_void_p]
        lib.wl_proxy_marshal_array.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.POINTER(_WlArgument),
        ]
        lib.wl_proxy_marshal_array_constructor_versioned.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.POINTER(_WlArgument),
            ctypes.POINTER(_WlInterface),
            ctypes.c_uint32,
        ]
        lib.wl_proxy_marshal_array_constructor_versioned.restype = ctypes.c_void_p
        lib.wl_display_dispatch_timeout.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
        ]
        lib.wl_display_dispatch_timeout.restype = ctypes.c_int
        self.lib = lib
        self.wl_registry_interface = _WlInterface.in_dll(lib, "wl_registry_interface")
        self.wl_seat_interface = _WlInterface.in_dll(lib, "wl_seat_interface")

    def bind(self, registry: int, name: int, interface: _WlInterface, version: int) -> int:
        # wl_registry.bind has signature "usun": the new_id is the last
        # argument and is passed as a NULL placeholder.
        args = (_WlArgument * 4)()
        args[0].u = name
        args[1].s = interface.name
        args[2].u = version
        args[3].n = 0
        proxy = self.lib.wl_proxy_marshal_array_constructor_versioned(
            registry, 0, args, ctypes.byref(interface), version
        )
        if not proxy:
            raise RuntimeError(f"cannot bind Wayland interface {interface.name!r}")
        return proxy

    def request(
        self,
        proxy: int,
        opcode: int,
        args: list[tuple[str, object]] | None = None,
    ) -> None:
        array = None
        if args:
            array = (_WlArgument * len(args))()
            for index, (kind, value) in enumerate(args):
                setattr(array[index], kind, value)
        self.lib.wl_proxy_marshal_array(proxy, opcode, array)

    def new_request(
        self,
        proxy: int,
        opcode: int,
        interface: _WlInterface,
        args: list[tuple[str, object]] | None = None,
    ) -> int:
        values = [("n", 0), *list(args or [])]
        array = (_WlArgument * len(values))()
        for index, (kind, value) in enumerate(values):
            setattr(array[index], kind, value)
        child = self.lib.wl_proxy_marshal_array_constructor_versioned(
            proxy, opcode, array, ctypes.byref(interface), interface.version
        )
        if not child:
            raise RuntimeError(f"cannot create Wayland object {interface.name!r}")
        return child

    def add_listener(
        self,
        proxy: int,
        callbacks: list[Any],
        *,
        keep_alive: bool = True,
    ):
        array = (ctypes.c_void_p * len(callbacks))(
            *[ctypes.cast(callback, ctypes.c_void_p) for callback in callbacks]
        )
        # Both the callbacks and the array must outlive the proxy. Long-lived
        # protocol objects use the global keep-alive list; per-source callbacks
        # are owned by their holder and released when the source is replaced.
        if keep_alive:
            _KEEP_ALIVE.extend(callbacks)
            _KEEP_ALIVE.append(array)
        if self.lib.wl_proxy_add_listener(proxy, array, None) < 0:
            raise RuntimeError("cannot install Wayland event listener")
        return array


_L = _LibWayland()

_REGISTRY_GLOBAL = ctypes.CFUNCTYPE(
    None, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_char_p, ctypes.c_uint32
)
_REGISTRY_GLOBAL_REMOVE = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32)
_SEAT_CAPABILITIES = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32)
_DEVICE_DATA_OFFER = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)
_DEVICE_SELECTION = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)
_DEVICE_FINISHED = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p)
_SOURCE_SEND = ctypes.CFUNCTYPE(
    None, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int
)
_SOURCE_CANCELLED = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p)

_OFFER_INTERFACE = _make_interface(
    "ext_data_control_offer_v1",
    [("receive", "sh", []), ("destroy", "", [])],
    [("offer", "s", [])],
)
_SOURCE_INTERFACE = _make_interface(
    "ext_data_control_source_v1",
    [("offer", "s", []), ("destroy", "", [])],
    [("send", "sh", []), ("cancelled", "", [])],
)
_DEVICE_INTERFACE = _make_interface(
    "ext_data_control_device_v1",
    [
        ("set_selection", "?o", [_SOURCE_INTERFACE]),
        ("destroy", "", []),
        ("set_primary_selection", "?o", [_SOURCE_INTERFACE]),
    ],
    [
        ("data_offer", "n", [_OFFER_INTERFACE]),
        ("selection", "?o", [_OFFER_INTERFACE]),
        ("finished", "", []),
        ("primary_selection", "?o", [_OFFER_INTERFACE]),
    ],
)
_MANAGER_INTERFACE = _make_interface(
    "ext_data_control_manager_v1",
    [
        ("create_data_source", "n", [_SOURCE_INTERFACE]),
        ("get_data_device", "no", [_DEVICE_INTERFACE, _L.wl_seat_interface]),
        ("destroy", "", []),
    ],
    [],
)

_MANAGER_OP_CREATE_SOURCE = 0
_MANAGER_OP_GET_DEVICE = 1
_DEVICE_OP_SET_SELECTION = 0
_DEVICE_OP_SET_PRIMARY_SELECTION = 2
_SOURCE_OP_OFFER = 0


@dataclass
class _CopyCommand:
    text: str
    primary: bool
    done: threading.Event
    ok: bool = False
    cancelled: bool = False


class _SourceHolder:
    def __init__(self, text: str) -> None:
        self.text = text
        self.proxy: int | None = None
        self.callbacks: list[Any] = []
        self.listener: ctypes.Array | None = None


class _WaylandClipboardThread:
    def __init__(self) -> None:
        self._ready = threading.Event()
        self._init_error: Exception | None = None
        self._lock = threading.Lock()
        self._commands: list[_CopyCommand] = []
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._display: int | None = None
        self._manager: int | None = None
        self._device: int | None = None
        self._sources: dict[bool, _SourceHolder] = {}

    def copy(self, text: str, primary: bool, timeout: float = 2.0) -> bool:
        self._ensure_started()
        if not self._ready.wait(timeout):
            log.warning("Wayland clipboard initialization timed out")
            return False
        if self._init_error is not None:
            log.warning("Wayland clipboard unavailable: %s", self._init_error)
            return False

        command = _CopyCommand(text=text, primary=primary, done=threading.Event())
        with self._lock:
            self._commands.append(command)
        if not command.done.wait(timeout):
            # Do not let a timed-out copy overwrite the selection later if the
            # compositor was merely slow to answer.
            with self._lock:
                command.cancelled = True
                try:
                    self._commands.remove(command)
                except ValueError:
                    pass
            log.warning("Wayland clipboard copy timed out")
            return False
        return command.ok

    def close(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
        if self._display:
            _L.lib.wl_display_disconnect(self._display)
            self._display = None

    def _ensure_started(self) -> None:
        with self._lock:
            if self._thread is not None:
                return
            self._thread = threading.Thread(
                target=self._run, name="maic-wayland-clipboard", daemon=True
            )
            self._thread.start()

    def _run(self) -> None:
        try:
            self._setup()
        except Exception as exc:
            self._init_error = exc
            self._ready.set()
            return
        self._ready.set()

        timespec = (ctypes.c_long * 2)(0, 20_000_000)  # 20 ms
        while not self._stop.is_set():
            commands = self._take_commands()
            for command in commands:
                if command.cancelled:
                    command.done.set()
                    continue
                try:
                    command.ok = self._apply_copy(command.text, command.primary)
                except Exception:
                    log.exception("Wayland clipboard copy failed")
                    command.ok = False
                command.done.set()

            # If commands are produced faster than this timeout, they are still
            # handled on the next iteration without unbounded latency.
            _L.lib.wl_display_dispatch_timeout(self._display, timespec)

    def _take_commands(self) -> list[_CopyCommand]:
        with self._lock:
            commands = self._commands
            self._commands = []
        return commands

    def _setup(self) -> None:
        self._display = _L.lib.wl_display_connect(None)
        if not self._display:
            raise RuntimeError("cannot connect to Wayland display")

        registry = _L.new_request(
            self._display,
            1,  # wl_display.get_registry
            _L.wl_registry_interface,
        )
        found: dict[str, tuple[int, int]] = {}

        def on_global(_data, _registry, name, interface, version):
            if interface is None:
                return
            name_bytes = ctypes.string_at(interface)
            if name_bytes in (b"wl_seat", b"ext_data_control_manager_v1"):
                found[name_bytes.decode()] = (name, version)

        global_cb = _REGISTRY_GLOBAL(on_global)
        global_remove_cb = _REGISTRY_GLOBAL_REMOVE(lambda *_args: None)
        _L.add_listener(registry, [global_cb, global_remove_cb])
        if _L.lib.wl_display_roundtrip(self._display) < 0:
            raise RuntimeError("Wayland registry roundtrip failed")

        if "ext_data_control_manager_v1" not in found:
            raise RuntimeError("compositor does not support ext-data-control-v1")
        if "wl_seat" not in found:
            raise RuntimeError("compositor did not expose a wl_seat")

        manager_name, _ = found["ext_data_control_manager_v1"]
        seat_name, seat_version = found["wl_seat"]
        manager = _L.bind(registry, manager_name, _MANAGER_INTERFACE, 1)
        seat = _L.bind(registry, seat_name, _L.wl_seat_interface, min(seat_version, 1))

        seat_cb = _SEAT_CAPABILITIES(lambda *_args: None)
        _L.add_listener(seat, [seat_cb])

        device = _L.new_request(manager, _MANAGER_OP_GET_DEVICE, _DEVICE_INTERFACE, [("o", seat)])
        device_cbs = [
            _DEVICE_DATA_OFFER(lambda *_args: None),
            _DEVICE_SELECTION(lambda *_args: None),
            _DEVICE_FINISHED(lambda *_args: None),
            _DEVICE_SELECTION(lambda *_args: None),
        ]
        _L.add_listener(device, device_cbs)
        if _L.lib.wl_display_roundtrip(self._display) < 0:
            raise RuntimeError("Wayland data-control roundtrip failed")

        self._manager = manager
        self._device = device

    def _apply_copy(self, text: str, primary: bool) -> bool:
        if self._manager is None or self._device is None:
            return False

        holder = _SourceHolder(text)
        source = _L.new_request(self._manager, _MANAGER_OP_CREATE_SOURCE, _SOURCE_INTERFACE)
        holder.proxy = source

        def send(_data, _source, _mime, fd):
            try:
                payload = holder.text.encode("utf-8")
                while payload:
                    written = os.write(fd, payload)
                    payload = payload[written:]
            except OSError:
                log.exception("failed to send clipboard data")
            finally:
                try:
                    os.close(fd)
                except OSError:
                    pass

        def cancelled(_data, source_proxy):
            with self._lock:
                for selection, item in list(self._sources.items()):
                    if item is holder:
                        del self._sources[selection]
                        break
            holder.proxy = None
            try:
                _L.lib.wl_proxy_destroy(source_proxy)
            except Exception:
                pass

        holder.callbacks = [_SOURCE_SEND(send), _SOURCE_CANCELLED(cancelled)]
        holder.listener = _L.add_listener(source, holder.callbacks, keep_alive=False)
        _L.request(source, _SOURCE_OP_OFFER, [("s", _MIME_TEXT)])

        opcode = _DEVICE_OP_SET_PRIMARY_SELECTION if primary else _DEVICE_OP_SET_SELECTION
        _L.request(self._device, opcode, [("o", source)])

        with self._lock:
            previous = self._sources.get(primary)
            self._sources[primary] = holder

        if _L.lib.wl_display_flush(self._display) < 0:
            return False
        if _L.lib.wl_display_roundtrip(self._display) < 0:
            return False

        if previous is not None and previous.proxy is not None:
            try:
                _L.lib.wl_proxy_destroy(previous.proxy)
            except Exception:
                pass
        return True


class WaylandClipboard:
    """Clipboard provider backed by an in-process Wayland data-control client."""

    def __init__(self, timeout: float = 2.0):
        self.timeout = timeout
        self._backend = _WaylandClipboardThread()

    async def copy(self, text: str, *, primary: bool = False) -> bool:
        return await asyncio.to_thread(self._backend.copy, text, primary, self.timeout)
