"""Bounded media DB key capture; attaches only to a user-started debug app."""

import json
import os
import platform
import shlex
import sqlite3
import subprocess
import tempfile
from contextlib import closing
from pathlib import Path

import click

from ..core.crypto import decrypt_page, full_decrypt
from ..keys.macho_arm64 import locate_codec_capture_offset


def verify_entry(path, entry):
    enc = bytes.fromhex(entry["derived_encryption_key"])
    mac = bytes.fromhex(entry["derived_hmac_key"])
    with path.open("rb") as stream:
        page = stream.read(4096)
    decoded = decrypt_page(enc, page, 1, mac)
    if (
        decoded[16:18] != b"\x10\x00"
        or decoded[18] not in (1, 2)
        or decoded[19] not in (1, 2)
        or decoded[20:24] != b"\x50\x40\x20\x20"
    ):
        raise ValueError("SQLite page structure mismatch")
    with tempfile.TemporaryDirectory(prefix="verify-media-") as temporary:
        target = Path(temporary) / "media.db"
        full_decrypt(str(path), str(target), enc, mac)
        with closing(sqlite3.connect(f"file:{target}?mode=ro", uri=True)) as db:
            result = db.execute("PRAGMA quick_check").fetchall()
            if result != [("ok",)]:
                raise ValueError("SQLite quick_check failed")


@click.command("media-keys")
@click.option("--pid", required=True, type=int, help="用户手动启动的调试微信 PID")
@click.option(
    "--app", "app_path", required=True, type=click.Path(exists=True, file_okay=False)
)
@click.option("--seconds", default=180, type=click.IntRange(10, 300))
@click.pass_context
def media_keys(ctx, pid, app_path, seconds):
    """捕获媒体库密钥。不会启动、退出、重签微信或操作微信界面。"""
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        raise click.ClickException("媒体库取钥目前仅支持 macOS ARM64")
    app = ctx.obj
    app_path = Path(app_path).resolve()
    actual = subprocess.run(
        ["ps", "-p", str(pid), "-o", "comm="],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    expected = app_path / "Contents/MacOS/WeChat"
    if actual != str(expected):
        raise click.ClickException("PID 与指定微信副本不一致，停止")
    dylib = app_path / "Contents/Resources/wechat.dylib"
    offset = locate_codec_capture_offset(str(dylib))
    state = Path(app.keys_file).parent
    run = Path(tempfile.mkdtemp(prefix="media-key-capture-", dir=state))
    os.chmod(run, 0o700)
    paths = sorted(Path(app.db_dir).glob("message/media_*.db"))
    relative = [p.relative_to(app.db_dir).as_posix() for p in paths]
    if not relative:
        raise click.ClickException("没有媒体数据库")
    targets = run / "targets.json"
    targets.write_text(
        json.dumps({"db_dir": app.db_dir, "files": relative, "required": relative})
    )
    output, report = run / "keys.json", run / "report.json"
    # Precreate private output; the callback only updates this isolated run file.
    fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump({}, stream)
    callback = Path(__file__).parents[1] / "keys/media_capture_lldb.py"
    script = run / "capture.lldb"
    script.write_text(
        "\n".join(
            [
                "settings set auto-confirm true",
                f"command script import {shlex.quote(str(callback))}",
                "wx-capture-all "
                + " ".join(
                    shlex.quote(str(v))
                    for v in (hex(offset), targets, output, report, seconds)
                ),
                'script print("CAPTURE_ARMED", flush=True)',
                "continue",
                "wx-capture-report",
                "breakpoint delete",
                "process detach",
                "quit",
            ]
        )
        + "\n"
    )
    click.echo(f"捕获目录: {run}", err=True)
    click.echo(
        f"即将连接 PID {pid}；看到 CAPTURE_ARMED 后请播放目标语音。限时 {seconds} 秒。",
        err=True,
    )
    env = dict(os.environ, PYTHONUNBUFFERED="1")
    result = subprocess.run(
        ["/usr/bin/lldb", "-b", "-p", str(pid), "-s", str(script)], env=env, check=False
    )
    captured = json.loads(output.read_text())
    verified = {}
    failures = []
    for rel, entry in captured.items():
        if rel not in relative:
            continue
        try:
            verify_entry(Path(app.db_dir) / rel, entry)
            verified[rel] = entry
        except (OSError, ValueError, KeyError, sqlite3.Error) as error:
            failures.append({"database": rel, "error": type(error).__name__})
    if verified:
        key_path = Path(app.keys_file)
        current = json.loads(key_path.read_text())
        backup = run / "previous-keys.json"
        descriptor = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w") as stream:
            json.dump(current, stream)
        current.update(verified)
        descriptor, temporary = tempfile.mkstemp(prefix=".verified-media-", dir=state)
        with os.fdopen(descriptor, "w") as stream:
            json.dump(current, stream, indent=2)
        os.replace(temporary, key_path)
    click.echo(
        json.dumps(
            {
                "verified_databases": sorted(verified),
                "missing": sorted(set(relative) - verified.keys()),
                "failures": failures,
                "report": str(report),
                "debugger_exit": result.returncode,
            },
            ensure_ascii=False,
        )
    )
    if not verified:
        ctx.exit(1)
