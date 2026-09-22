"""search command — search messages by keyword."""

from __future__ import annotations

import click

from ..models.message import MSG_TYPE_FILTERS
from ..output.formatter import output
from ..services.message_service import (
    collect_chat_search,
    parse_time_range,
    resolve_chat_context,
    search_all_messages,
    validate_pagination,
)

MSG_TYPE_NAMES = list(MSG_TYPE_FILTERS.keys())


def _candidate_page_size(limit: int, offset: int) -> int:
    """Internal helper: fetch more candidates than needed for ranking."""
    return max(limit + offset, 100) * 3


def _page_ranked_entries(
    entries: list[tuple[int, str]], limit: int, offset: int
) -> list[tuple[int, str]]:
    """Sort entries by timestamp descending and paginate."""
    entries.sort(key=lambda x: x[0], reverse=True)
    return entries[offset : offset + limit]


@click.command("search")
@click.argument("keyword")
@click.option("--chat", multiple=True, help="Limit to specific chat(s)")
@click.option("--start-time", default="", help="Start time")
@click.option("--end-time", default="", help="End time")
@click.option("--limit", default=20, help="Number of results (max 500)")
@click.option("--offset", default=0, help="Pagination offset")
@click.option("--format", "fmt", default="json", type=click.Choice(["json", "text"]), help="Output format")
@click.option("--type", "msg_type", default=None, type=click.Choice(MSG_TYPE_NAMES), help="Filter by message type")
@click.pass_context
def search(
    ctx: click.Context,
    keyword: str,
    chat: tuple[str, ...],
    start_time: str,
    end_time: str,
    limit: int,
    offset: int,
    fmt: str,
    msg_type: str | None,
) -> None:
    """Search messages by keyword.

    \b
    Examples:
      wxq search "Claude"                         # Global search
      wxq search "Claude" --chat "AI Group"        # Search in specific chat
      wxq search "meeting" --chat "A" --chat "B"   # Search across multiple chats
      wxq search "hello" --start-time "2026-04-01" --limit 50
    """
    app = ctx.obj

    try:
        validate_pagination(limit, offset)
        start_ts, end_ts = parse_time_range(start_time, end_time)
    except ValueError as e:
        click.echo(f"Error: {e}", err=True)
        ctx.exit(2)

    names = app.contacts.get_names()
    candidate_limit = _candidate_page_size(limit, offset)
    chat_names = list(chat)
    type_filter = MSG_TYPE_FILTERS[msg_type] if msg_type else None

    if len(chat_names) == 1:
        # Single chat search
        chat_ctx = resolve_chat_context(
            chat_names[0], app.msg_db_keys, app.cache, app.contacts
        )
        if not chat_ctx:
            click.echo(f"Chat not found: {chat_names[0]}", err=True)
            ctx.exit(1)
        if not chat_ctx.db_path:
            click.echo(f"No messages found for {chat_ctx.display_name}", err=True)
            ctx.exit(1)
        entries, failures = collect_chat_search(
            chat_ctx, names, keyword, app.display_name_fn,
            start_ts=start_ts, end_ts=end_ts, candidate_limit=candidate_limit,
            msg_type_filter=type_filter,
        )
        scope = chat_ctx.display_name

    elif len(chat_names) > 1:
        # Multi-chat search
        entries = []
        failures = []
        for name in chat_names:
            chat_ctx = resolve_chat_context(
                name, app.msg_db_keys, app.cache, app.contacts
            )
            if not chat_ctx or not chat_ctx.db_path:
                failures.append(f"Not found: {name}")
                continue
            chat_entries, chat_failures = collect_chat_search(
                chat_ctx, names, keyword, app.display_name_fn,
                start_ts=start_ts, end_ts=end_ts, candidate_limit=candidate_limit,
                msg_type_filter=type_filter,
            )
            entries.extend(chat_entries)
            failures.extend(chat_failures)
        scope = f"{len(chat_names)} chats"

    else:
        # Global search
        entries, failures = search_all_messages(
            app.msg_db_keys, app.cache, names, keyword, app.display_name_fn,
            start_ts=start_ts, end_ts=end_ts, candidate_limit=candidate_limit,
            msg_type_filter=type_filter,
        )
        scope = "all messages"

    paged = _page_ranked_entries(entries, limit, offset)

    if fmt == "json":
        output({
            "scope": scope,
            "keyword": keyword,
            "count": len(paged),
            "offset": offset,
            "limit": limit,
            "start_time": start_time or None,
            "end_time": end_time or None,
            "type": msg_type or None,
            "results": [item[1] for item in paged],
            "failures": failures if failures else None,
        }, "json")
    else:
        if not paged:
            output(f'No messages containing "{keyword}" found in {scope}', "text")
            return
        header = (
            f'Search "{keyword}" in {scope}: '
            f"{len(paged)} results (offset={offset}, limit={limit})"
        )
        if start_time or end_time:
            header += f"\nTime range: {start_time or 'earliest'} ~ {end_time or 'latest'}"
        if failures:
            header += "\nQuery failures: " + "; ".join(failures)
        output(header + ":\n\n" + "\n\n".join(item[1] for item in paged), "text")
