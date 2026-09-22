"""export command — export chat history to markdown or text."""

from __future__ import annotations

from datetime import datetime

import click

from ..models.message import MSG_TYPE_FILTERS
from ..output.formatter import output
from ..services.message_service import (
    collect_chat_history,
    parse_time_range,
    resolve_chat_context,
    validate_pagination,
)


@click.command("export")
@click.argument("chat_name")
@click.option("--format", "fmt", default="markdown", type=click.Choice(["markdown", "txt"]), help="Export format")
@click.option("--output", "output_path", default=None, help="Output file path (default: stdout)")
@click.option("--start-time", default="", help="Start time YYYY-MM-DD [HH:MM[:SS]]")
@click.option("--end-time", default="", help="End time YYYY-MM-DD [HH:MM[:SS]]")
@click.option("--limit", default=500, help="Number of messages to export")
@click.pass_context
def export(
    ctx: click.Context,
    chat_name: str,
    fmt: str,
    output_path: str | None,
    start_time: str,
    end_time: str,
    limit: int,
) -> None:
    """Export chat history to markdown or plain text.

    \b
    Examples:
      wxq export "Alice" --format markdown
      wxq export "Work Group" --format txt --output chat.txt
      wxq export "Alice" --start-time "2026-04-01" --limit 1000
    """
    app = ctx.obj

    try:
        validate_pagination(limit, 0, limit_max=None)
        start_ts, end_ts = parse_time_range(start_time, end_time)
    except ValueError as e:
        click.echo(f"Error: {e}", err=True)
        ctx.exit(2)

    chat_ctx = resolve_chat_context(
        chat_name, app.msg_db_keys, app.cache, app.contacts
    )
    if not chat_ctx:
        click.echo(f"Chat not found: {chat_name}", err=True)
        ctx.exit(1)
    if not chat_ctx.db_path:
        click.echo(f"No messages found for {chat_ctx.display_name}", err=True)
        ctx.exit(1)

    names = app.contacts.get_names()
    lines, failures = collect_chat_history(
        chat_ctx, names, app.display_name_fn,
        start_ts=start_ts, end_ts=end_ts, limit=limit, offset=0,
    )

    if not lines:
        click.echo(f"{chat_ctx.display_name}: no messages to export", err=True)
        ctx.exit(0)

    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    chat_type = "Group" if chat_ctx.is_group else "Private"
    time_range = f"{start_time or 'earliest'} ~ {end_time or 'latest'}"

    if fmt == "markdown":
        content = _format_markdown(chat_ctx.display_name, chat_type, time_range, now, lines)
    else:
        content = _format_txt(chat_ctx.display_name, chat_type, time_range, now, lines)

    if output_path:
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(content)
            if not content.endswith("\n"):
                f.write("\n")
        click.echo(f"Exported to: {output_path} ({len(lines)} messages)", err=True)
    else:
        output(content, "text")


def _format_markdown(
    display_name: str, chat_type: str, time_range: str, export_time: str, lines: list[str]
) -> str:
    header = (
        f"# Chat History: {display_name}\n\n"
        f"**Time range:** {time_range}\n\n"
        f"**Exported:** {export_time}\n\n"
        f"**Messages:** {len(lines)}\n\n"
        f"**Type:** {chat_type}\n\n---\n"
    )
    body = "\n".join(f"- {line}" for line in lines)
    return header + body


def _format_txt(
    display_name: str, chat_type: str, time_range: str, export_time: str, lines: list[str]
) -> str:
    header = (
        f"Chat History: {display_name}\n"
        f"Type: {chat_type}\n"
        f"Time range: {time_range}\n"
        f"Exported: {export_time}\n"
        f"Messages: {len(lines)}\n"
        f"{'=' * 60}"
    )
    body = "\n".join(lines)
    return header + "\n" + body
