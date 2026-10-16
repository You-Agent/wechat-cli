"""LLDB-compatible validation helpers for runtime SQLCipher key pairs."""

import ctypes
import hashlib
import hmac
import struct

import lldb

STATE = {}


def _read_memory(process, address, size):
    error = lldb.SBError()
    value = process.ReadMemory(address, size, error)
    if not error.Success() or len(value) != size:
        return None
    return bytes(value)


def _valid_pair(encryption_key, hmac_key):
    page = STATE["page"]
    hmac_start = 4096 - 64
    authenticated = page[16:hmac_start] + struct.pack("<I", 1)
    actual = hmac.new(hmac_key, authenticated, hashlib.sha512).digest()
    if not hmac.compare_digest(actual, page[hmac_start:4096]):
        return False

    iv_start = 4096 - 80
    ciphertext = page[16:iv_start]
    output = ctypes.create_string_buffer(len(ciphertext))
    moved = ctypes.c_size_t()
    crypt = ctypes.CDLL("/usr/lib/libSystem.B.dylib").CCCrypt
    crypt.argtypes = [
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_size_t,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_size_t,
        ctypes.c_void_p,
        ctypes.c_size_t,
        ctypes.POINTER(ctypes.c_size_t),
    ]
    crypt.restype = ctypes.c_int32
    status = crypt(
        1,
        0,
        0,
        encryption_key,
        len(encryption_key),
        page[iv_start : iv_start + 16],
        ciphertext,
        len(ciphertext),
        output,
        len(ciphertext),
        ctypes.byref(moved),
    )
    if status != 0 or moved.value != len(ciphertext):
        return False
    header = output.raw[:8]
    return (
        int.from_bytes(header[:2], "big") == 4096
        and header[2] in (1, 2)
        and header[3] in (1, 2)
        and header[4:] == b"\x50\x40\x20\x20"
    )
