import hashlib
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad
from click.testing import CliRunner
from wechat_cli.core.image_decrypt import (ImageRecoveryError, account_id, cache_codes,
                                           decrypt_v2, recover_image, wxgf_hevc)
from wechat_cli.main import cli


def encrypted(plain, aes_size=16, xor_size=7, code=1234, account='wxid_example'):
    key = hashlib.md5((str(code)+account).encode()).hexdigest()[:16].encode()
    rest = plain[aes_size:]
    body = AES.new(key, AES.MODE_ECB).encrypt(pad(plain[:aes_size], 16))
    body += rest[:-xor_size] if xor_size else rest
    if xor_size:
        body += bytes(v ^ (code & 255) for v in rest[-xor_size:])
    return b'\x07\x08V2\x08\x07' + struct.pack('<II', aes_size, xor_size) + b'\0' + body


class ImageTests(unittest.TestCase):
    def test_roundtrip_aligned_unaligned_and_zero_xor(self):
        plain = b'\x89PNG' + bytes(range(256)) * 8
        for aes_size in (15, 16, 1024):
            for xor_size in (0, 7, len(plain)-aes_size):
                with self.subTest(aes_size=aes_size, xor_size=xor_size):
                    self.assertEqual(decrypt_v2(encrypted(plain, aes_size, xor_size), 'wxid_example', [1, 1234]), plain)

    def test_wrong_key_and_truncation(self):
        data = encrypted(b'\x89PNG' + b'a'*64)
        for account, codes in [('wrong', [1234]), ('wxid_example', [99]), ('wxid_example', [])]:
            with self.assertRaisesRegex(ImageRecoveryError, 'key validation'):
                decrypt_v2(data, account, codes)
        for broken in (b'', data[:14], data[:30]):
            with self.assertRaisesRegex(ImageRecoveryError, 'file decryption'):
                decrypt_v2(broken, 'wxid_example', [1234])

    def test_account_and_cache_names(self):
        self.assertEqual(account_id('/tmp/wxid_example_ab12'), 'wxid_example')
        self.assertEqual(account_id('/tmp/example_ab12'), 'example')
        with tempfile.TemporaryDirectory() as d:
            for name in ('key_1234_test.statistic', 'key_0_test.statistic', 'key_4294967296_x.statistic', 'other'):
                (Path(d)/name).touch()
            self.assertEqual(cache_codes([d]), [1234])

    def test_wxgf(self):
        part = b'\0\0\0\1' + b'x'*100
        image = b'wxgf\x09' + len(part).to_bytes(4, 'big') + part
        self.assertEqual(wxgf_hevc(image), part)
        for bad in (image[:-1], b'wxgf', image + len(part).to_bytes(4, 'big') + part):
            with self.assertRaisesRegex(ImageRecoveryError, 'format conversion'):
                wxgf_hevc(bad)

    def test_decoder_rejects_header_only_and_preserves_output(self):
        import shutil
        if not shutil.which('ffmpeg'):
            self.skipTest('ffmpeg not installed')
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root/'key_1234_test.statistic').touch()
            source, output = root/'source.dat', root/'output.png'
            source.write_bytes(encrypted(b'\x89PNG'+b'x'*100))
            original = source.read_bytes()
            with self.assertRaisesRegex(ImageRecoveryError, 'format conversion'):
                recover_image(source, output, 'wxid_example', [root])
            self.assertFalse(output.exists())
            self.assertEqual(source.read_bytes(), original)
            output.write_bytes(b'existing')
            with self.assertRaisesRegex(ImageRecoveryError, 'already exists'):
                recover_image(source, output, 'wxid_example', [root])
            self.assertEqual(output.read_bytes(), b'existing')

    def test_complete_png_roundtrip(self):
        import shutil
        import zlib
        if not shutil.which('ffmpeg'):
            self.skipTest('ffmpeg not installed')
        def chunk(kind, body):
            return struct.pack('>I', len(body)) + kind + body + struct.pack('>I', zlib.crc32(kind + body))
        png = (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 1, 1, 8, 2, 0, 0, 0))
               + chunk(b'IDAT', zlib.compress(b'\0\xff\0\0')) + chunk(b'IEND', b''))
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root/'key_1234_test.statistic').touch()
            source, output = root/'input.dat', root/'image.png'
            source.write_bytes(encrypted(png))
            recover_image(source, output, 'wxid_example', [root])
            self.assertTrue(output.read_bytes().startswith(b'\x89PNG'))
            self.assertEqual(output.stat().st_mode & 0o777, 0o600)
            self.assertFalse(list(root.glob('.wechat-image-*')))

    def test_no_database_context_for_image_command(self):
        with patch('wechat_cli.main.AppContext', side_effect=AssertionError('database accessed')):
            result = CliRunner().invoke(cli, ['image-decrypt', '--help'])
            self.assertEqual(result.exit_code, 0)


if __name__ == '__main__':
    unittest.main()
