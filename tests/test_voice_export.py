import importlib.util
import math
import sqlite3
import struct
import tempfile
import unittest
import wave
from contextlib import closing
from pathlib import Path

from wechat_cli.core.voice_export import decode_voice, find_voice_blob


class ExactVoiceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "media.db"
        with closing(sqlite3.connect(self.path)) as db:
            db.executescript(
                "CREATE TABLE Name2Id(user_name TEXT); CREATE TABLE VoiceInfo(chat_name_id INTEGER,svr_id INTEGER,local_id INTEGER,create_time INTEGER,voice_data BLOB);"
            )
            db.executemany("INSERT INTO Name2Id VALUES (?)", [("target",), ("other",)])
            db.executemany(
                "INSERT INTO VoiceInfo VALUES (?,?,?,?,?)",
                [(1, 123, 9, 100, b"target-audio"), (2, 123, 9, 100, b"other-audio")],
            )
            db.commit()
        self.record = {"server_id": "123", "local_id": 9, "timestamp": 100}

    def test_server_id_alone_cannot_select_another_chat(self):
        self.assertEqual(
            find_voice_blob([self.path], "target", self.record), b"target-audio"
        )

    def test_timestamp_mismatch_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "timestamp mismatch"):
            find_voice_blob([self.path], "target", dict(self.record, timestamp=101))

    def test_local_id_mismatch_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "local_id mismatch"):
            find_voice_blob([self.path], "target", dict(self.record, local_id=10))

    def test_missing_and_ambiguous_matches_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "found 0"):
            find_voice_blob([self.path], "missing", self.record)
        with self.assertRaisesRegex(ValueError, "found 2"):
            find_voice_blob([self.path, self.path], "target", self.record)

    def test_empty_audio_is_rejected(self):
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("UPDATE VoiceInfo SET voice_data=? WHERE chat_name_id=1", (b"",))
            db.commit()
        with self.assertRaisesRegex(ValueError, "empty"):
            find_voice_blob([self.path], "target", self.record)


@unittest.skipUnless(importlib.util.find_spec("pilk"), "install the audio extra")
class VoiceDecodeTests(unittest.TestCase):
    def setUp(self):
        import pilk

        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.folder = Path(self.directory.name)
        pcm = self.folder / "tone.pcm"
        silk = self.folder / "tone.silk"
        pcm.write_bytes(
            b"".join(
                struct.pack("<h", int(3000 * math.sin(2 * math.pi * 440 * i / 24000)))
                for i in range(24000)
            )
        )
        pilk.encode(str(pcm), str(silk), pcm_rate=24000, tencent=True)
        self.blob = silk.read_bytes()

    def test_real_codec_creates_complete_one_second_wav(self):
        output = self.folder / "voice.wav"
        self.assertEqual(decode_voice(self.blob, output, 1000), 1000)
        with wave.open(str(output), "rb") as stream:
            self.assertEqual(stream.getparams()[:3], (1, 2, 24000))
            self.assertEqual(len(stream.readframes(stream.getnframes())), 48000)
        self.assertEqual(output.stat().st_mode & 0o777, 0o600)

    def test_wrong_duration_is_rejected_without_publishing(self):
        output = self.folder / "voice.wav"
        with self.assertRaisesRegex(ValueError, "duration differs"):
            decode_voice(self.blob, output, 3000)
        self.assertFalse(output.exists())

    def test_existing_output_is_preserved(self):
        output = self.folder / "voice.wav"
        output.write_bytes(b"existing")
        with self.assertRaises(FileExistsError):
            decode_voice(self.blob, output, 1000)
        self.assertEqual(output.read_bytes(), b"existing")
