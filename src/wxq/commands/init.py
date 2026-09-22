"""init command — extract encryption keys and generate configuration."""

from __future__ import annotations

import json
import os
import sys

import click

from ..core.config import STATE_DIR, CONFIG_FILE, KEYS_FILE, auto_detect_db_dir


@click.command()
@click.option("--db-dir", default=None, help="WeChat data directory path (auto-detect if omitted)")
@click.option("--force", is_flag=True, help="Force re-extraction of keys")
def init(db_dir: str | None, force: bool) -> None:
    """Initialize wxq: extract keys and generate config."""
    click.echo("WeChat Query Initialization")
    click.echo("=" * 40)

    # 1. Check if already initialized
    if os.path.exists(CONFIG_FILE) and os.path.exists(KEYS_FILE) and not force:
        click.echo(f"Already initialized (config: {CONFIG_FILE})")
        click.echo("Use --force to re-extract keys")
        return

    # 2. Create state directory
    os.makedirs(STATE_DIR, exist_ok=True)

    # 3. Determine db_dir
    if db_dir is None:
        db_dir = auto_detect_db_dir()
        if db_dir is None:
            click.echo("[!] Could not auto-detect WeChat data directory", err=True)
            click.echo("Please specify via --db-dir, e.g.:", err=True)
            click.echo("  wxq init --db-dir ~/path/to/db_storage", err=True)
            sys.exit(1)
        click.echo(f"[+] Detected WeChat data directory: {db_dir}")
    else:
        db_dir = os.path.abspath(db_dir)
        if not os.path.isdir(db_dir):
            click.echo(f"[!] Directory does not exist: {db_dir}", err=True)
            sys.exit(1)
        click.echo(f"[+] Using specified data directory: {db_dir}")

    # 4. Extract keys
    click.echo("\nStarting key extraction...")
    try:
        from ..keys import extract_keys
        key_map = extract_keys(db_dir, KEYS_FILE)
    except Exception as e:
        click.echo(f"\n[!] Key extraction failed: {e}", err=True)
        if "sudo" not in str(e).lower():
            click.echo("Hint: macOS/Linux may require sudo privileges", err=True)
        sys.exit(1)

    # 5. Write config
    cfg = {"db_dir": db_dir}
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)

    click.echo(f"\n[+] Initialization complete!")
    click.echo(f"    Config: {CONFIG_FILE}")
    click.echo(f"    Keys: {KEYS_FILE}")
    click.echo(f"    Extracted {len(key_map)} database keys")
    click.echo("\nYou can now use:")
    click.echo('  wxq sessions')
    click.echo('  wxq history "Contact Name"')
