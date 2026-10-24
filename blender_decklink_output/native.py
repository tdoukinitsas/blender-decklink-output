# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Thomas Doukinitsas

"""ctypes wrapper around bin/bdo_decklink.dll (source in native/ of the repository)."""

import ctypes
import hashlib
import json
import os
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
DLL_PATH = os.path.join(HERE, "bin", "bdo_decklink.dll")
API_VERSION = 1

FLIP_Y = 1
RGBA = 2

FOURCC_BGRA = 0x42475241
FOURCC_2VUY = 0x32767579


class Stats(ctypes.Structure):
    _fields_ = [(name, ctypes.c_int64) for name in (
        "pushed", "scheduled", "completed", "late", "dropped", "flushed", "resyncs", "buffered",
        "width", "height", "time_scale", "frame_duration", "pixel_format", "playing",
        "mirror_frames", "mirror_us")]


class MirrorConfig(ctypes.Structure):
    _fields_ = [(name, ctypes.c_int32) for name in (
        "enabled", "win_w", "win_h", "win_x", "win_y", "src_x", "src_y", "src_w", "src_h",
        "dst_x", "dst_y", "dst_w", "dst_h", "max_fps")]


class _Lib:
    handle = None
    error = ""


def _shadow_copy():
    """Load a copy from %TEMP% so the installed DLL is never locked: re-installing the
    add-on while Blender is open keeps working (the new DLL loads after a restart)."""
    with open(DLL_PATH, "rb") as handle:
        data = handle.read()
    folder = os.path.join(tempfile.gettempdir(), "blender_decklink_output")
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, "bdo_decklink_%s.dll" % hashlib.sha1(data).hexdigest()[:12])
    if not os.path.isfile(path):
        temporary = "%s.%d.tmp" % (path, os.getpid())
        with open(temporary, "wb") as handle:
            handle.write(data)
        os.replace(temporary, path)
    return path


def lib():
    if _Lib.handle is not None or _Lib.error:
        return _Lib.handle
    try:
        if os.name != "nt":
            raise OSError("DeckLink output is Windows-only in this version")
        if not os.path.isfile(DLL_PATH):
            raise OSError("native library missing: %s (run native\\build.bat)" % DLL_PATH)
        dll = ctypes.CDLL(_shadow_copy())
        c_void_p, c_int, c_char_p = ctypes.c_void_p, ctypes.c_int, ctypes.c_char_p
        dll.bdo_api_version.restype = c_int
        dll.bdo_list_devices.argtypes = (c_char_p, c_int)
        dll.bdo_list_devices.restype = c_int
        dll.bdo_open.argtypes = (c_char_p, ctypes.c_uint32, c_int, c_char_p, c_int)
        dll.bdo_open.restype = c_void_p
        dll.bdo_push.argtypes = (c_void_p, c_void_p, c_int, c_int, c_int, c_int)
        dll.bdo_push.restype = c_int
        dll.bdo_mirror.argtypes = (c_void_p, ctypes.POINTER(MirrorConfig))
        dll.bdo_mirror.restype = c_int
        dll.bdo_mirror_error.argtypes = (c_void_p, c_char_p, c_int)
        dll.bdo_mirror_error.restype = c_int
        dll.bdo_get_stats.argtypes = (c_void_p, ctypes.POINTER(Stats))
        dll.bdo_get_stats.restype = c_int
        dll.bdo_close.argtypes = (c_void_p,)
        dll.bdo_close.restype = None
        if dll.bdo_api_version() != API_VERSION:
            raise OSError("native library API %d, add-on expects %d (rebuild it)"
                          % (dll.bdo_api_version(), API_VERSION))
        _Lib.handle = dll
    except OSError as err:
        _Lib.error = str(err)
    return _Lib.handle


def load_error():
    lib()
    return _Lib.error


# ---------------------------------------------------------------- devices

class Devices:
    """Last enumeration: {"available", "api_version", "error", "devices": [...]}."""
    data = None

    @classmethod
    def refresh(cls):
        dll = lib()
        if dll is None:
            cls.data = {"available": False, "api_version": "", "error": _Lib.error, "devices": []}
            return cls.data
        cap = 1 << 16
        while True:
            buffer = ctypes.create_string_buffer(cap)
            needed = dll.bdo_list_devices(buffer, cap)
            if needed < cap:
                break
            cap = needed + 1
        try:
            cls.data = json.loads(buffer.value.decode("utf-8"))
        except ValueError as err:
            cls.data = {"available": False, "api_version": "", "error": str(err), "devices": []}
        return cls.data

    @classmethod
    def get(cls):
        return cls.data if cls.data is not None else cls.refresh()

    @classmethod
    def device(cls, name):
        for device in cls.get()["devices"]:
            if device["name"] == name:
                return device
        return None

    @classmethod
    def mode(cls, device_name, mode_name):
        device = cls.device(device_name)
        for mode in (device or {}).get("modes", ()):
            if mode["name"] == mode_name:
                return mode
        return None


def mode_fps(mode):
    return mode["scale"] / float(mode["duration"]) if mode and mode["duration"] else 25.0


def mode_description(mode):
    field = {"p": "p", "psf": "PsF", "i_upper": "i", "i_lower": "i"}.get(mode["field"], "")
    formats = "BGRA" if mode["bgra"] else "YUV 4:2:2"
    return "%d x %d  %.3f fps %s  (%s)" % (mode["width"], mode["height"], mode_fps(mode), field,
                                          formats)


# ---------------------------------------------------------------- output

class NativeOutput:
    def __init__(self, device, mode_code, preroll):
        dll = lib()
        if dll is None:
            raise RuntimeError(_Lib.error)
        error = ctypes.create_string_buffer(1024)
        handle = dll.bdo_open(device.encode("utf-8"), int(mode_code) & 0xFFFFFFFF, int(preroll),
                              error, len(error))
        if not handle:
            raise RuntimeError(error.value.decode("utf-8", "replace") or "could not open output")
        self._handle = ctypes.c_void_p(handle)
        stats = self.stats()
        self.width, self.height = int(stats.width), int(stats.height)
        self.fps = stats.time_scale / float(stats.frame_duration) if stats.frame_duration else 25.0
        self.pixel_format = int(stats.pixel_format)
        self._mirror = None

    @property
    def is_open(self):
        return self._handle is not None

    def push(self, address, width, height, row_bytes, flags):
        if self._handle is None:
            return -1
        return lib().bdo_push(self._handle, ctypes.c_void_p(address), width, height, row_bytes,
                              flags)

    def mirror(self, **values):
        """Configure the native screen-capture thread (enabled=0 stops it). Only sends
        when something changed."""
        if self._handle is None:
            return
        key = tuple(sorted(values.items()))
        if key == self._mirror:
            return
        self._mirror = key
        config = MirrorConfig(**{name: int(value) for name, value in values.items()})
        lib().bdo_mirror(self._handle, ctypes.byref(config))

    def mirror_error(self):
        if self._handle is None:
            return ""
        buffer = ctypes.create_string_buffer(512)
        lib().bdo_mirror_error(self._handle, buffer, len(buffer))
        return buffer.value.decode("utf-8", "replace")

    def stats(self):
        stats = Stats()
        if self._handle is not None:
            lib().bdo_get_stats(self._handle, ctypes.byref(stats))
        return stats

    def close(self):
        if self._handle is not None:
            lib().bdo_close(self._handle)
            self._handle = None
