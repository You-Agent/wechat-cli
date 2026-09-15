"""Read-only macOS kvcomm/V2 image recovery. Keys never leave this module."""
import hashlib
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import tempfile

from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad


class ImageRecoveryError(ValueError):
    """A stage-labelled recovery failure, without key material."""


def account_id(directory):
    name = Path(directory).name
    match = re.match(r"^(wxid_[^_]+)", name)
    return match[1] if match else re.sub(r"_[A-Za-z0-9]{4}$", "", name)


def cache_directories(account):
    base = Path.home() / "Library/Containers/com.tencent.xinWeChat/Data"
    roots = [base / "Documents/app_data", base / "Documents/xwechat",
             base / "Library/Application Support/com.tencent.xinWeChat/xwechat",
             base / "Library/Application Support/com.tencent.xinWeChat"]
    return list(dict.fromkeys([r / "net/kvcomm" for r in roots + list(Path(account).parents)]))


def cache_codes(directories):
    codes = set()
    for directory in directories:
        if not Path(directory).is_dir():
            continue
        for path in Path(directory).iterdir():
            match = re.fullmatch(r"key_(\d+)_.+\.statistic", path.name, re.I)
            if match and 0 < int(match[1]) <= 0xffffffff:
                codes.add(int(match[1]))
    return sorted(codes)


def decrypt_v2(data, account, codes):
    if len(data) < 15 or data[:6] != b"\x07\x08V2\x08\x07":
        raise ImageRecoveryError("file decryption: unsupported or truncated V2 header")
    aes_size, xor_size = struct.unpack_from("<II", data, 6)
    padded_size = aes_size + 16 - aes_size % 16
    payload = data[15:]
    if aes_size < 4 or padded_size + xor_size > len(payload):
        raise ImageRecoveryError("file decryption: invalid V2 segment lengths")
    for code in codes:
        key = hashlib.md5((str(code) + account).encode()).hexdigest()[:16].encode("ascii")
        try:
            prefix = unpad(AES.new(key, AES.MODE_ECB).decrypt(payload[:padded_size]), 16)
        except ValueError:
            continue
        if len(prefix) != aes_size or not prefix.startswith(
                (b"\xff\xd8\xff", b"\x89PNG", b"GIF8", b"RIFF", b"wxgf")):
            continue
        rest = payload[padded_size:]
        middle = rest[:-xor_size] if xor_size else rest
        tail = bytes(v ^ (code & 255) for v in rest[-xor_size:]) if xor_size else b""
        return prefix + middle + tail
    raise ImageRecoveryError("key validation: no cache candidate passed padding and image-header checks")


def wxgf_hevc(data):
    if len(data) < 5 or data[:4] != b"wxgf" or not 5 <= data[4] < len(data):
        raise ImageRecoveryError("format conversion: invalid WXGF header")
    parts = []
    for marker in (b"\0\0\0\1", b"\0\0\1"):
        offset = data[4]
        while offset < len(data):
            pos = data.find(marker, offset)
            if pos < 0:
                break
            length = int.from_bytes(data[pos-4:pos], "big") if pos >= 4 else 0
            if 0 < length <= len(data) - pos:
                parts.append(data[pos:pos+length])
                offset = pos + length
            else:
                offset = pos + 1
        if parts:
            break
    if len(parts) != 1 or len(parts[0]) / len(data) <= 0.6:
        raise ImageRecoveryError("format conversion: unsupported multi-part or malformed WXGF")
    return parts[0]


def recover_image(source, output, account, directories):
    source, output = Path(source), Path(output)
    if output.suffix.lower() != ".png":
        raise ImageRecoveryError("output: use a .png destination")
    if output.exists() or output.is_symlink():
        raise ImageRecoveryError("output: destination already exists")
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise ImageRecoveryError("format conversion: ffmpeg is required (brew install ffmpeg)")
    codes = cache_codes(directories)
    if not codes:
        raise ImageRecoveryError("key validation: no kvcomm cache candidates found")
    plain = decrypt_v2(source.read_bytes(), account_id(account), codes)
    is_wxgf = plain.startswith(b"wxgf")
    stream = wxgf_hevc(plain) if is_wxgf else plain
    output.parent.mkdir(parents=True, exist_ok=True)
    # Private scratch directory on the destination filesystem; publish without overwrite.
    with tempfile.TemporaryDirectory(prefix=".wechat-image-", dir=output.parent) as temporary:
        raw, png = Path(temporary) / "input", Path(temporary) / "output.png"
        raw.write_bytes(stream)
        raw.chmod(0o600)
        common = [ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-xerror", "-err_detect", "explode"]
        commands = [common + (["-f", "hevc"] if is_wxgf else []) +
                    ["-i", str(raw), "-frames:v", "1", "-update", "1", str(png)],
                    common + ["-i", str(png), "-f", "null", "-"]]
        for command in commands:
            try:
                result = subprocess.run(command, capture_output=True, timeout=60)
            except (OSError, subprocess.TimeoutExpired):
                raise ImageRecoveryError("format conversion: decoder failed or timed out") from None
            if result.returncode or not png.is_file() or not png.stat().st_size:
                raise ImageRecoveryError("format conversion: complete image decoding failed")
        png.chmod(0o600)
        os.link(png, output)  # Atomic and fails if another process created the destination.
    return output.resolve()
