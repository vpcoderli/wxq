"""history command — retrieve chat message history."""

from __future__ import annotations

import click

from ..models.message import MSG_TYPE_FILTERS
from ..output.formatter import output
from ..services.message_service import (
    collect_chat_history,
    parse_time_range,
    resolve_chat_context,
    validate_pagination,
)

MSG_TYPE_NAMES = list(MSG_TYPE_FILTERS.keys())


@click.command("history")
@click.argument("chat_name")
@click.option("--limit", default=50, help="Number of messages to return")
@click.option("--offset", default=0, help="Pagination offset")
@click.option("--start-time", default="", help="Start time YYYY-MM-DD [HH:MM[:SS]]")
@click.option("--end-time", default="", help="End time YYYY-MM-DD [HH:MM[:SS]]")
@click.option("--format", "fmt", default="json", type=click.Choice(["json", "text"]), help="Output format")
@click.option("--type", "msg_type", default=None, type=click.Choice(MSG_TYPE_NAMES), help="Filter by message type")
@click.option("--media", is_flag=True, help="Resolve media file paths (images/files/videos/voice)")
@click.pass_context
def history(
    ctx: click.Context,
    chat_name: str,
    limit: int,
    offset: int,
    start_time: str,
    end_time: str,
    fmt: str,
    msg_type: str | None,
    media: bool,
) -> None:
    """Retrieve chat message history.

    \b
    Examples:
      wxq history "Alice"                          # Last 50 messages
      wxq history "Alice" --limit 100 --offset 50  # Pagination
      wxq history "Work Group" --start-time "2026-04-01" --end-time "2026-04-02"
      wxq history "Alice" --format text             # Plain text output
    """
    app = ctx.obj

    try:
        validate_pagination(limit, offset, limit_max=None)
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
    type_filter = MSG_TYPE_FILTERS[msg_type] if msg_type else None
    lines, failures = collect_chat_history(
        chat_ctx, names, app.display_name_fn,
        start_ts=start_ts, end_ts=end_ts, limit=limit, offset=offset,
        msg_type_filter=type_filter, resolve_media=media, db_dir=app.db_dir,
    )

    if fmt == "json":
        output({
            "chat": chat_ctx.display_name,
            "username": chat_ctx.username,
            "is_group": chat_ctx.is_group,
            "count": len(lines),
            "offset": offset,
            "limit": limit,
            "start_time": start_time or None,
            "end_time": end_time or None,
            "type": msg_type or None,
            "messages": lines,
            "failures": failures if failures else None,
        }, "json")
    else:
        header = (
            f"{chat_ctx.display_name} message history "
            f"({len(lines)} messages, offset={offset}, limit={limit})"
        )
        if chat_ctx.is_group:
            header += " [group]"
        if start_time or end_time:
            header += f"\nTime range: {start_time or 'earliest'} ~ {end_time or 'latest'}"
        if failures:
            header += "\nQuery failures: " + "; ".join(failures)
        if lines:
            output(header + ":\n\n" + "\n".join(lines), "text")
        else:
            output(f"{chat_ctx.display_name}: no messages found", "text")
