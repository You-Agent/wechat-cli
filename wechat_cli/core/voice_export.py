"""Exact local voice matching and SILK -> WAV export, without ASR."""

import os
import re
import sqlite3
import tempfile
import wave
from contextlib import closing
from pathlib import Path

from .messages import _parse_xml_root, decompress_content


def readonly(path):
    return sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)


def collect_voices(ctx, start_ts, end_ts):
    records = {}
    for item in ctx["message_tables"]:
        table = item["table_name"]
        if not re.fullmatch(r"Msg_[0-9a-f]{32}", table):
            raise ValueError("Invalid message table")
        with closing(readonly(item["db_path"])) as db:
            rows = db.execute(
                f"SELECT local_id,server_id,create_time,message_content,WCDB_CT_message_content FROM [{table}] WHERE (local_type & 4294967295)=34 AND create_time>=? AND create_time<=? ORDER BY create_time",
                (start_ts, end_ts),
            ).fetchall()
        for local_id, server_id, timestamp, content, compression in rows:
            text = decompress_content(content, compression)
            root = _parse_xml_root(text)
            voice = root.find(".//voicemsg") if root is not None else None
            duration = int(voice.get("voicelength", "0")) if voice is not None else 0
            record = {
                "local_id": local_id,
                "server_id": str(server_id),
                "timestamp": timestamp,
                "duration_ms": duration,
            }
            key = str(server_id)
            if key in records and records[key] != record:
                raise ValueError("Conflicting voice message metadata")
            records[key] = record
    return sorted(records.values(), key=lambda record: record["timestamp"])


def find_voice_blob(paths, username, record):
    matches = []
    for path in paths:
        with closing(readonly(path)) as db:
            columns = {row[1] for row in db.execute("PRAGMA table_info(VoiceInfo)")}
            if not {"voice_data", "chat_name_id", "svr_id"} <= columns:
                continue
            rows = db.execute(
                "SELECT v.* FROM VoiceInfo v JOIN Name2Id n ON n.rowid=v.chat_name_id WHERE n.user_name=? AND v.svr_id=?",
                (username, int(record["server_id"])),
            )
            names = [column[0] for column in rows.description]
            for values in rows:
                found = dict(zip(names, values))
                if (
                    "create_time" in found
                    and found["create_time"] != record["timestamp"]
                ):
                    raise ValueError("Voice timestamp mismatch")
                if "local_id" in found and found["local_id"] != record["local_id"]:
                    raise ValueError("Voice local_id mismatch")
                blob = found["voice_data"]
                if not isinstance(blob, bytes) or not blob:
                    raise ValueError("Voice data is empty")
                matches.append(blob)
    if len(matches) != 1:
        raise ValueError(f"Expected one exact voice record; found {len(matches)}")
    return matches[0]


def decode_voice(blob, output, expected_ms):
    if not blob.startswith((b"\x02#!SILK_V3", b"#!SILK_V3")):
        raise ValueError("Unsupported voice format")
    import pilk

    output = Path(output)
    if output.exists():
        raise FileExistsError(output)
    with tempfile.TemporaryDirectory(prefix=".voice-", dir=output.parent) as temporary:
        directory = Path(temporary)
        silk, pcm = directory / "voice.silk", directory / "voice.pcm"
        silk.write_bytes(blob)
        pilk.decode(str(silk), str(pcm), pcm_rate=24000)
        data = pcm.read_bytes()
        if not data or len(data) % 2:
            raise ValueError("Invalid decoded PCM")
        duration_ms = len(data) / 48
        if expected_ms and abs(duration_ms - expected_ms) > max(
            200, expected_ms * 0.05
        ):
            raise ValueError("Decoded duration differs from message duration")
        target = directory / "voice.wav"
        with wave.open(str(target), "wb") as stream:
            stream.setnchannels(1)
            stream.setsampwidth(2)
            stream.setframerate(24000)
            stream.writeframes(data)
        os.chmod(target, 0o600)
        # Link publishes only if destination does not already exist.
        os.link(target, output)
    return round(duration_ms)
