"""Windows binding for the editor's own draft codec (``lvve::EncryptUtils``).

The macOS build compiles :file:`jy14_codec.cpp` against the class supplied by
the user's own JianYing installation. Windows exports the *same* class from
``videoeditor.dll``:

    ?enable@EncryptUtils@lvve@@QEAAX_N@Z            void enable(bool)
    ?encrypt@EncryptUtils@lvve@@QEAA?AV<string>...  string encrypt(string const&)
    ?decrypt@EncryptUtils@lvve@@QEAA?AV<string>...  string decrypt(string const&,
                                                                   string const&,
                                                                   bool&)

Because those symbols are ordinary exported C++ members, they can be driven
through :mod:`ctypes` directly. This keeps the Windows port free of a C++
toolchain and of the pinned, non-distributable ``jy14_codec`` binary that the
macOS runtime requires.

The ABI assumptions are narrow and are checked at import time:

* MSVC x64 member functions take the hidden return-value slot first, then
  ``this``.
* ``std::string`` is the VS2015+ layout: a 16-byte inline buffer/capacity union,
  then ``size`` and ``capacity``.
"""

from __future__ import annotations

import ctypes
import hashlib
import os
from pathlib import Path
from typing import Optional

# Exported names, spelled exactly as videoeditor.dll exports them. ``@`` and
# ``?`` are legal inside Python attribute lookups performed with getattr().
SYMBOL_ENABLE = "?enable@EncryptUtils@lvve@@QEAAX_N@Z"
SYMBOL_IS_ENABLE = "?isEnable@EncryptUtils@lvve@@QEAA_NXZ"
SYMBOL_ENCRYPT = (
    "?encrypt@EncryptUtils@lvve@@QEAA?AV?$basic_string@DU?$char_traits@D@std@@"
    "V?$allocator@D@2@@std@@AEBV34@@Z"
)
SYMBOL_DECRYPT = (
    "?decrypt@EncryptUtils@lvve@@QEAA?AV?$basic_string@DU?$char_traits@D@std@@"
    "V?$allocator@D@2@@std@@AEBV34@0AEA_N@Z"
)
SYMBOL_STRING_DTOR = (
    "??1?$basic_string@DU?$char_traits@D@std@@V?$allocator@D@2@@std@@QEAA@XZ"
)

#: ``EncryptUtils`` carries no state that the caller has to construct, but it
#: is not a trivial type either. A zeroed, over-sized slot is the smallest
#: assumption that still tolerates unexported members growing.
INSTANCE_BYTES = 512

#: Guard against a codec returning something absurd before it is parsed.
MAX_CODEC_OUTPUT_BYTES = 256 * 1024 * 1024


class CodecUnavailable(RuntimeError):
    """The editor's codec could not be loaded or driven."""


class _StringUnion(ctypes.Union):
    _fields_ = [("inline_buffer", ctypes.c_char * 16), ("pointer", ctypes.c_void_p)]


class MSVCString(ctypes.Structure):
    """``std::string`` as laid out by the MSVC x64 toolchain."""

    _fields_ = [
        ("storage", _StringUnion),
        ("size", ctypes.c_size_t),
        ("capacity", ctypes.c_size_t),
    ]

    @classmethod
    def from_bytes(cls, payload: bytes) -> "MSVCString":
        value = cls()
        length = len(payload)
        if length < 16:
            value.storage.inline_buffer = payload
            value.size = length
            value.capacity = 15
        else:
            # The callee only reads through the reference, so a caller-owned
            # buffer is safe here; keep a reference so it outlives the call.
            buffer = ctypes.create_string_buffer(payload, length + 1)
            value._backing = buffer
            value.storage.pointer = ctypes.cast(buffer, ctypes.c_void_p).value
            value.size = length
            value.capacity = length
        return value

    def to_bytes(self) -> bytes:
        # Validate the untrusted native result before dereferencing its pointer.
        # The DLL destructor still owns the result in the caller's finally block.
        if self.size > MAX_CODEC_OUTPUT_BYTES or self.size > self.capacity:
            raise CodecUnavailable("draft codec output has an invalid size")
        if self.size == 0:
            return b""
        if self.capacity >= 16:
            if not self.storage.pointer:
                raise CodecUnavailable("draft codec output has a null pointer")
            return ctypes.string_at(self.storage.pointer, self.size)
        if self.size >= 16:
            raise CodecUnavailable("draft codec inline output is oversized")
        return bytes(self.storage.inline_buffer[: self.size])


class WindowsDraftCodec:
    """Encrypt and decrypt draft JSON through the installed editor.

    Instances own a loaded ``videoeditor.dll``; load one per process and reuse
    it. Plaintext only ever exists in memory, matching the macOS runtime's
    pipe-based guarantee.
    """

    #: Windows exports the codec from the main engine library; the separate
    #: ``libvecrptor.dll`` only wraps the same primitive at the AVIO layer.
    LIBRARY_NAME = "videoeditor.dll"

    def __init__(self, app_directory: Path):
        self.app_directory = Path(app_directory)
        library = self.app_directory / self.LIBRARY_NAME
        if not library.is_file():
            raise CodecUnavailable("draft codec library missing: %s" % library)

        # videoeditor.dll links against its siblings (Qt, CEF, codecs) by bare
        # name, so the application directory has to be searchable first.
        self._dll_directory = None
        if hasattr(os, "add_dll_directory"):
            self._dll_directory = os.add_dll_directory(str(self.app_directory))
        previous = os.getcwd()
        try:
            os.chdir(self.app_directory)
            self._library = ctypes.CDLL(str(library))
        except OSError as exc:
            raise CodecUnavailable("cannot load %s" % library) from exc
        finally:
            os.chdir(previous)

        try:
            self._enable = getattr(self._library, SYMBOL_ENABLE)
            self._is_enable = getattr(self._library, SYMBOL_IS_ENABLE)
            self._encrypt = getattr(self._library, SYMBOL_ENCRYPT)
            self._decrypt = getattr(self._library, SYMBOL_DECRYPT)
        except AttributeError as exc:
            raise CodecUnavailable(
                "installed editor does not export the reviewed codec symbols"
            ) from exc
        self._string_dtor = getattr(self._library, SYMBOL_STRING_DTOR, None)

        self._enable.restype = None
        self._enable.argtypes = [ctypes.c_void_p, ctypes.c_bool]
        self._is_enable.restype = ctypes.c_bool
        self._is_enable.argtypes = [ctypes.c_void_p]
        self._encrypt.restype = None
        self._encrypt.argtypes = [ctypes.c_void_p, ctypes.POINTER(MSVCString),
                                  ctypes.POINTER(MSVCString)]
        self._decrypt.restype = None
        self._decrypt.argtypes = [ctypes.c_void_p, ctypes.POINTER(MSVCString),
                                  ctypes.POINTER(MSVCString),
                                  ctypes.POINTER(MSVCString),
                                  ctypes.POINTER(ctypes.c_bool)]
        if self._string_dtor is not None:
            self._string_dtor.restype = None
            self._string_dtor.argtypes = [ctypes.POINTER(MSVCString)]

        self._instance = ctypes.create_string_buffer(INSTANCE_BYTES)
        self._instance_pointer = ctypes.cast(self._instance, ctypes.c_void_p)
        self._parameters = MSVCString.from_bytes(b"{}")
        self._enable(self._instance_pointer, True)
        if not self._is_enable(self._instance_pointer):
            raise CodecUnavailable("draft codec refused to enable itself")

    def __repr__(self) -> str:  # pragma: no cover - diagnostics only
        return "<WindowsDraftCodec %s>" % self.app_directory.name

    # -- public API -------------------------------------------------------

    def decrypt(self, ciphertext: bytes) -> bytes:
        """Return the draft JSON carried by ``ciphertext``."""

        source = MSVCString.from_bytes(ciphertext)
        result = MSVCString()
        valid = ctypes.c_bool(False)
        self._decrypt(self._instance_pointer, ctypes.byref(result),
                      ctypes.byref(source), ctypes.byref(self._parameters),
                      ctypes.byref(valid))
        try:
            if not valid.value:
                raise CodecUnavailable("draft codec rejected the ciphertext")
            plaintext = result.to_bytes()
        finally:
            self._release(result)
        if not plaintext:
            raise CodecUnavailable("draft codec returned empty plaintext")
        return plaintext

    def encrypt(self, plaintext: bytes) -> bytes:
        """Return the on-disk representation of ``plaintext`` draft JSON."""

        if not plaintext:
            raise CodecUnavailable("refusing to encrypt empty content")
        source = MSVCString.from_bytes(plaintext)
        result = MSVCString()
        self._encrypt(self._instance_pointer, ctypes.byref(result),
                      ctypes.byref(source))
        try:
            ciphertext = result.to_bytes()
        finally:
            self._release(result)
        if not ciphertext:
            raise CodecUnavailable("draft codec returned empty ciphertext")
        return ciphertext

    def close(self) -> None:
        if self._dll_directory is not None:
            self._dll_directory.close()
            self._dll_directory = None

    def _release(self, value: MSVCString) -> None:
        """Run the DLL's own destructor so its allocator frees the buffer."""

        if self._string_dtor is not None:
            self._string_dtor(ctypes.byref(value))


_CACHED: dict = {}


def codec_for(app_directory: Path) -> WindowsDraftCodec:
    """Return a codec bound to the current directory and library bytes."""

    directory = Path(app_directory).resolve()
    library = directory / WindowsDraftCodec.LIBRARY_NAME
    try:
        hasher = hashlib.sha256()
        with library.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                hasher.update(chunk)
    except OSError as exc:
        raise CodecUnavailable("draft codec library missing: %s" % library) from exc
    key = (str(directory), hasher.hexdigest())
    if any(cached_path == key[0] and cached_hash != key[1]
           for cached_path, cached_hash in _CACHED):
        raise CodecUnavailable("engine library changed after loading; restart this process")
    existing = _CACHED.get(key)
    if existing is None:
        existing = WindowsDraftCodec(directory)
        _CACHED[key] = existing
    return existing


def library_path(app_directory: Path) -> Optional[Path]:
    candidate = Path(app_directory) / WindowsDraftCodec.LIBRARY_NAME
    return candidate if candidate.is_file() else None
