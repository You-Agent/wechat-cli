"""Export a bounded chat voice range using only existing verified media keys."""

import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path

import click

from ..core.key_utils import get_key_info
from ..core.messages import parse_time_range, resolve_chat_context
from ..core.voice_export import collect_voices, decode_voice, find_voice_blob


@click.command("voice-export")
@click.argument("chat_name")
@click.option("--start-time", required=True)
@click.option("--end-time", required=True)
@click.option("--output-dir", required=True, type=click.Path(file_okay=False))
@click.pass_context
def voice_export(ctx, chat_name, start_time, end_time, output_dir):
    """按聊天对象和消息 ID 精确匹配语音，导出 WAV；不识别语音。"""
    app = ctx.obj
    start, end = parse_time_range(start_time, end_time)
    chat = resolve_chat_context(
        chat_name, app.msg_db_keys, app.cache, app.decrypted_dir
    )
    if not chat or not chat.get("message_tables"):
        raise click.ClickException("找不到聊天记录")
    source = sorted(Path(app.db_dir).glob("message/media_*.db"))
    missing = [
        p.relative_to(app.db_dir).as_posix()
        for p in source
        if not get_key_info(app.all_keys, p.relative_to(app.db_dir).as_posix())
    ]
    paths = [
        app.cache.get(p.relative_to(app.db_dir).as_posix())
        for p in source
        if p.relative_to(app.db_dir).as_posix() not in missing
    ]
    if not paths or not all(paths):
        raise click.ClickException(
            "没有可读的媒体数据库；缺少密钥: " + ", ".join(missing)
        )
    records = collect_voices(chat, start, end)
    destination = Path(output_dir).resolve()
    destination.mkdir(parents=True, exist_ok=False, mode=0o700)
    results = []
    for record in records:
        timestamp = datetime.fromtimestamp(record["timestamp"])
        target = destination / f"{timestamp:%Y%m%d-%H%M%S}-{record['local_id']}.wav"
        try:
            blob = find_voice_blob(paths, chat["username"], record)
            duration = decode_voice(blob, target, record["duration_ms"])
            results.append(
                {
                    "time": timestamp.isoformat(),
                    "local_id": record["local_id"],
                    "path": str(target),
                    "duration_ms": duration,
                }
            )
        except (OSError, ValueError, RuntimeError, ImportError, sqlite3.Error) as error:
            results.append(
                {
                    "time": timestamp.isoformat(),
                    "local_id": record["local_id"],
                    "error": str(error),
                }
            )
    manifest = destination / "manifest.json"
    descriptor = os.open(manifest, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        json.dump(
            {
                "chat": chat["display_name"],
                "voices": results,
                "unavailable_media_databases": missing,
            },
            stream,
            ensure_ascii=False,
            indent=2,
        )
    click.echo(
        json.dumps(
            {
                "chat": chat["display_name"],
                "voices": results,
                "manifest": str(manifest),
                "unavailable_media_databases": missing,
            },
            ensure_ascii=False,
        )
    )
    if not records or any("error" in record for record in results):
        ctx.exit(1)
