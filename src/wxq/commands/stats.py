"""stats command — chat statistics analysis."""

from __future__ import annotations

import click

from ..output.formatter import output
from ..services.message_service import (
    collect_chat_stats,
    parse_time_range,
    resolve_chat_context,
)


@click.command("stats")
@click.argument("chat_name")
@click.option("--start-time", default="", help="Start time YYYY-MM-DD [HH:MM[:SS]]")
@click.option("--end-time", default="", help="End time YYYY-MM-DD [HH:MM[:SS]]")
@click.option("--format", "fmt", default="json", type=click.Choice(["json", "text"]), help="Output format")
@click.pass_context
def stats(ctx: click.Context, chat_name: str, start_time: str, end_time: str, fmt: str) -> None:
    """Chat statistics analysis.

    \b
    Examples:
      wxq stats "AI Group"
      wxq stats "Alice" --start-time "2026-04-01" --end-time "2026-04-03"
      wxq stats "Group" --format text
    """
    app = ctx.obj

    try:
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
    result = collect_chat_stats(
        chat_ctx, names, app.display_name_fn,
        start_ts=start_ts, end_ts=end_ts,
    )

    if fmt == "json":
        output({
            "chat": chat_ctx.display_name,
            "username": chat_ctx.username,
            "is_group": chat_ctx.is_group,
            **result.to_dict(),
        }, "json")
    else:
        total = result.total
        lines = [f"{chat_ctx.display_name} Statistics"]
        if chat_ctx.is_group:
            lines[0] += " [Group]"
        lines.append(f"Total messages: {total}")
        if start_time or end_time:
            lines.append(f"Time range: {start_time or 'earliest'} ~ {end_time or 'latest'}")

        # Type breakdown
        lines.append("\nMessage type distribution:")
        for t, cnt in result.type_breakdown.items():
            pct = cnt / total * 100 if total > 0 else 0
            lines.append(f"  {t}: {cnt} ({pct:.1f}%)")

        # Top senders
        if result.top_senders:
            lines.append("\nTop senders:")
            for s in result.top_senders:
                lines.append(f"  {s.name}: {s.count}")

        # 24-hour distribution
        hourly = result.hourly
        lines.append("\n24-hour activity:")
        max_count = max(hourly.counts.values()) if hourly.counts else 0
        bar_max = 30
        for h in range(24):
            count = hourly.get(h)
            bar_len = int(count / max_count * bar_max) if max_count > 0 else 0
            bar = "█" * bar_len
            lines.append(f"  {h:02d}h |{bar} {count}")

        output("\n".join(lines), "text")
