"""sessions command — list recent chat sessions."""

from __future__ import annotations

import os
import sqlite3
from contextlib import closing
from datetime import datetime

import click

from ..output.formatter import output
from ..services.message_parser import decompress_content, format_msg_type


@click.command("sessions")
@click.option("--limit", default=20, help="Number of sessions to return")
@click.option("--format", "fmt", default="json", type=click.Choice(["json", "text"]), help="Output format")
@click.pass_context
def sessions(ctx: click.Context, limit: int, fmt: str) -> None:
    """List recent chat sessions.

    \b
    Examples:
      wxq sessions                # Last 20 sessions (JSON)
      wxq sessions --limit 10     # Last 10 sessions
      wxq sessions --format text  # Plain text output
    """
    app = ctx.obj

    path = app.cache.get(os.path.join("session", "session.db"))
    if not path:
        click.echo("Error: cannot decrypt session.db", err=True)
        ctx.exit(3)

    names = app.contacts.get_names()
    with closing(sqlite3.connect(path)) as conn:
        rows = conn.execute("""
            SELECT username, unread_count, summary, last_timestamp,
                   last_msg_type, last_msg_sender, last_sender_display_name
            FROM SessionTable
            WHERE last_timestamp > 0
            ORDER BY last_timestamp DESC
            LIMIT ?
        """, (limit,)).fetchall()

    results = []
    for r in rows:
        username, unread, summary, ts, msg_type, sender, sender_name = r
        display = names.get(username, username)
        is_group = "@chatroom" in username

        if isinstance(summary, bytes):
            summary = decompress_content(summary, 4) or "(compressed content)"
        if isinstance(summary, str) and ":\n" in summary:
            summary = summary.split(":\n", 1)[1]

        sender_display = ""
        if is_group and sender:
            sender_display = names.get(sender, sender_name or sender)

        results.append({
            "chat": display,
            "username": username,
            "is_group": is_group,
            "unread": unread or 0,
            "last_message": str(summary or ""),
            "msg_type": format_msg_type(msg_type),
            "sender": sender_display,
            "timestamp": ts,
            "time": datetime.fromtimestamp(ts).strftime("%m-%d %H:%M"),
        })

    if fmt == "json":
        output(results, "json")
    else:
        lines = []
        for r in results:
            entry = f"[{r['time']}] {r['chat']}"
            if r["is_group"]:
                entry += " [group]"
            if r["unread"] > 0:
                entry += f" ({r['unread']} unread)"
            entry += f"\n  {r['msg_type']}: "
            if r["sender"]:
                entry += f"{r['sender']}: "
            entry += r["last_message"]
            lines.append(entry)
        output(f"Recent {len(results)} sessions:\n\n" + "\n\n".join(lines), "text")
