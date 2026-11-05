"""Windows Core Audio volume control for the Corsair endpoints, using ctypes only.

Vtable indices follow the Windows SDK headers mmdeviceapi.h, endpointvolume.h
and propsys.h. The calling thread must have initialised COM (see com_init).
"""
from __future__ import annotations

import ctypes as C
import uuid
from ctypes import wintypes as W
from dataclasses import dataclass
from typing import Optional

E_RENDER = 0
E_CAPTURE = 1
DEVICE_STATE_ACTIVE = 1
CLSCTX_ALL = 23
COINIT_MULTITHREADED = 0
STGM_READ = 0
VT_LPWSTR = 31

ENDPOINT_MATCH = ("CORSAIR", "VOID")


class _GUID(C.Structure):
    _fields_ = [("bytes", C.c_ubyte * 16)]

    @classmethod
    def of(cls, text: str) -> "_GUID":
        guid = cls()
        guid.bytes[:] = list(uuid.UUID(text).bytes_le)
        return guid


class _PropertyKey(C.Structure):
    _fields_ = [("fmtid", _GUID), ("pid", W.DWORD)]


class _PropVariant(C.Structure):
    _fields_ = [
        ("vt", W.USHORT),
        ("reserved1", W.USHORT),
        ("reserved2", W.USHORT),
        ("reserved3", W.USHORT),
        ("pwszVal", C.c_void_p),
        ("padding", C.c_void_p),
    ]


CLSID_MM_DEVICE_ENUMERATOR = _GUID.of("BCDE0395-E52F-467C-8E3D-C4579291692E")
IID_MM_DEVICE_ENUMERATOR = _GUID.of("A95664D2-9614-4F35-A746-DE8DB63617E6")
IID_AUDIO_ENDPOINT_VOLUME = _GUID.of("5CDF2C82-841E-4546-9722-0CF74078229A")
PKEY_FRIENDLY_NAME = _PropertyKey(_GUID.of("A45C254E-DF1C-4EFD-8020-67D146A850E0"), 14)

_ole32 = C.windll.ole32
_ole32.CoCreateInstance.argtypes = [
    C.POINTER(_GUID), C.c_void_p, W.DWORD, C.POINTER(_GUID), C.POINTER(C.c_void_p),
]
_ole32.CoCreateInstance.restype = C.c_long
_ole32.CoInitializeEx.argtypes = [C.c_void_p, W.DWORD]
_ole32.CoInitializeEx.restype = C.c_long
_ole32.PropVariantClear.argtypes = [C.POINTER(_PropVariant)]


def com_init() -> None:
    _ole32.CoInitializeEx(None, COINIT_MULTITHREADED)


def com_uninit() -> None:
    _ole32.CoUninitialize()


def _call(pointer, index: int, *args, types=()) -> None:
    """Call COM method `index` on `pointer`, raising on a failed HRESULT."""
    vtable = C.cast(pointer, C.POINTER(C.POINTER(C.c_void_p))).contents
    function = C.WINFUNCTYPE(C.c_long, C.c_void_p, *types)(vtable[index])
    result = function(pointer, *args)
    if result < 0:
        raise OSError(f"Erreur audio Windows 0x{result & 0xFFFFFFFF:08X}")


def _release(pointer) -> None:
    if pointer:
        vtable = C.cast(pointer, C.POINTER(C.POINTER(C.c_void_p))).contents
        C.WINFUNCTYPE(C.c_ulong, C.c_void_p)(vtable[2])(pointer)


PP = C.POINTER(C.c_void_p)


def _friendly_name(device) -> str:
    store = C.c_void_p()
    _call(device, 4, STGM_READ, C.byref(store), types=(W.DWORD, PP))  # OpenPropertyStore
    try:
        value = _PropVariant()
        _call(store, 5, C.byref(PKEY_FRIENDLY_NAME), C.byref(value),
              types=(C.POINTER(_PropertyKey), C.POINTER(_PropVariant)))  # GetValue
        try:
            if value.vt == VT_LPWSTR and value.pwszVal:
                return C.wstring_at(value.pwszVal)
            return ""
        finally:
            _ole32.PropVariantClear(C.byref(value))
    finally:
        _release(store)


class _Endpoint:
    """Owns one IAudioEndpointVolume interface."""

    def __init__(self, pointer: C.c_void_p, name: str) -> None:
        self._pointer = pointer
        self.name = name

    def close(self) -> None:
        _release(self._pointer)
        self._pointer = None

    @property
    def volume(self) -> float:
        value = C.c_float()
        _call(self._pointer, 9, C.byref(value), types=(C.POINTER(C.c_float),))
        return value.value

    @volume.setter
    def volume(self, level: float) -> None:
        _call(self._pointer, 7, C.c_float(max(0.0, min(1.0, level))), None,
              types=(C.c_float, C.c_void_p))

    @property
    def muted(self) -> bool:
        value = W.BOOL()
        _call(self._pointer, 15, C.byref(value), types=(C.POINTER(W.BOOL),))
        return bool(value.value)

    @muted.setter
    def muted(self, state: bool) -> None:
        _call(self._pointer, 14, W.BOOL(state), None, types=(W.BOOL, C.c_void_p))


def _find_endpoint(flow: int) -> Optional[_Endpoint]:
    enumerator = C.c_void_p()
    result = _ole32.CoCreateInstance(
        C.byref(CLSID_MM_DEVICE_ENUMERATOR), None, CLSCTX_ALL,
        C.byref(IID_MM_DEVICE_ENUMERATOR), C.byref(enumerator),
    )
    if result < 0:
        raise OSError(f"Erreur audio Windows 0x{result & 0xFFFFFFFF:08X}")
    collection = C.c_void_p()
    try:
        _call(enumerator, 3, flow, DEVICE_STATE_ACTIVE, C.byref(collection),
              types=(C.c_int, W.DWORD, PP))  # EnumAudioEndpoints
        count = W.UINT()
        _call(collection, 3, C.byref(count), types=(C.POINTER(W.UINT),))  # GetCount
        for index in range(count.value):
            device = C.c_void_p()
            _call(collection, 4, index, C.byref(device), types=(W.UINT, PP))  # Item
            try:
                name = _friendly_name(device)
                if all(word in name.upper() for word in ENDPOINT_MATCH):
                    volume = C.c_void_p()
                    _call(device, 3, C.byref(IID_AUDIO_ENDPOINT_VOLUME), CLSCTX_ALL, None,
                          C.byref(volume),
                          types=(C.POINTER(_GUID), W.DWORD, C.c_void_p, PP))  # Activate
                    return _Endpoint(volume, name)
            finally:
                _release(device)
        return None
    finally:
        _release(collection)
        _release(enumerator)


@dataclass
class AudioState:
    output_found: bool = False
    output_volume: float = 0.0
    output_muted: bool = False
    mic_found: bool = False
    mic_volume: float = 0.0
    mic_muted: bool = False


class HeadsetAudio:
    """Volume and mute of the headset's output and microphone endpoints."""

    def __init__(self) -> None:
        self._endpoints: dict[int, _Endpoint] = {}

    def close(self) -> None:
        for endpoint in self._endpoints.values():
            endpoint.close()
        self._endpoints.clear()

    def _endpoint(self, flow: int) -> Optional[_Endpoint]:
        endpoint = self._endpoints.get(flow)
        if endpoint is None:
            endpoint = _find_endpoint(flow)
            if endpoint is not None:
                self._endpoints[flow] = endpoint
        return endpoint

    def _forget(self, flow: int) -> None:
        endpoint = self._endpoints.pop(flow, None)
        if endpoint is not None:
            endpoint.close()

    def read(self) -> AudioState:
        state = AudioState()
        for flow in (E_RENDER, E_CAPTURE):
            try:
                endpoint = self._endpoint(flow)
                if endpoint is None:
                    continue
                volume, muted = endpoint.volume, endpoint.muted
            except OSError:
                # The endpoint disappeared (dongle unplugged): look it up next time.
                self._forget(flow)
                continue
            if flow == E_RENDER:
                state.output_found, state.output_volume, state.output_muted = True, volume, muted
            else:
                state.mic_found, state.mic_volume, state.mic_muted = True, volume, muted
        return state

    def _apply(self, flow: int, attribute: str, value) -> None:
        endpoint = self._endpoint(flow)
        if endpoint is None:
            raise OSError("Périphérique audio du casque introuvable.")
        try:
            setattr(endpoint, attribute, value)
        except OSError:
            self._forget(flow)
            raise

    def set_output_volume(self, level: float) -> None:
        self._apply(E_RENDER, "volume", level)

    def set_output_muted(self, state: bool) -> None:
        self._apply(E_RENDER, "muted", state)

    def set_mic_volume(self, level: float) -> None:
        self._apply(E_CAPTURE, "volume", level)

    def set_mic_muted(self, state: bool) -> None:
        self._apply(E_CAPTURE, "muted", state)
