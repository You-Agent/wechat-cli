"""Recover one explicitly selected encrypted image, without opening message databases."""
from pathlib import Path
import click
from ..core.config import load_config
from ..core.image_decrypt import cache_directories, recover_image


@click.command("image-decrypt")
@click.argument("source", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--output", required=True, type=click.Path(path_type=Path), help="New PNG destination; never overwritten.")
@click.option("--account-dir", type=click.Path(file_okay=False, path_type=Path), help="Your own account directory (parent of db_storage).")
@click.option("--kvcomm-dir", multiple=True, type=click.Path(file_okay=False, path_type=Path), help="Cache directory; repeat to try multiple locations.")
@click.pass_context
def image_decrypt(ctx, source, output, account_dir, kvcomm_dir):
    """Decrypt a selected macOS V2 DAT image using local cache filenames.

    Requires FFmpeg. Does not attach to WeChat or require database keys.
    Animated sources are not preserved; output is a single PNG.
    """
    try:
        if account_dir is None:
            cfg = load_config(ctx.parent.params.get("config_path"))
            account_dir = Path(cfg["wechat_base_dir"])
        directories = kvcomm_dir or cache_directories(account_dir)
        result = recover_image(source, output, account_dir, directories)
    except (ValueError, OSError) as error:
        raise click.ClickException(str(error)) from None
    click.echo(f"Validated PNG: {result}")
