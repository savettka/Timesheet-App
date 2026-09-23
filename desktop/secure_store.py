"""Keeping the sign-in token unreadable to anyone but this Windows account.

Windows' Data Protection API (DPAPI) encrypts with a key tied to the signed-
in Windows user, so the token saved on disk is useless if the file is copied
off the PC or read by another account.
"""

import base64
import ctypes
import sys

_PREFIX_DPAPI = "dpapi:"
_PREFIX_PLAIN = "plain:"  # only where DPAPI doesn't exist (not Windows)


if sys.platform == "win32":
    from ctypes import wintypes

    class _Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    _crypt32 = ctypes.windll.crypt32
    _kernel32 = ctypes.windll.kernel32
    _CRYPTPROTECT_UI_FORBIDDEN = 0x01

    def _call(func, data):
        buffer = ctypes.create_string_buffer(data, len(data))
        blob_in = _Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
        blob_out = _Blob()
        ok = func(ctypes.byref(blob_in), None, None, None, None,
                  _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(blob_out))
        if not ok:
            raise OSError("Windows couldn't protect or unlock the saved sign-in.")
        try:
            return ctypes.string_at(blob_out.pbData, blob_out.cbData)
        finally:
            _kernel32.LocalFree(blob_out.pbData)

    def _protect(data):
        return _call(_crypt32.CryptProtectData, data)

    def _unprotect(data):
        return _call(_crypt32.CryptUnprotectData, data)


def protect(text):
    if text is None:
        return None
    raw = text.encode("utf-8")
    if sys.platform == "win32":
        return _PREFIX_DPAPI + base64.b64encode(_protect(raw)).decode("ascii")
    return _PREFIX_PLAIN + base64.b64encode(raw).decode("ascii")


def unprotect(stored):
    if not stored:
        return None
    if stored.startswith(_PREFIX_DPAPI) and sys.platform == "win32":
        return _unprotect(base64.b64decode(stored[len(_PREFIX_DPAPI):])).decode("utf-8")
    if stored.startswith(_PREFIX_PLAIN):
        return base64.b64decode(stored[len(_PREFIX_PLAIN):]).decode("utf-8")
    return None
