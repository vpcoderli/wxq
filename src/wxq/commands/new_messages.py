"""new-messages command — incremental message detection with persistent state."""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import closing
from datetime import datetime
from typing import Any

import click

from ..core.config import STATE_DIR
from ..output.formatter import output
from ..services.message_parser import decompress_content, format_msg_type

STATE_FILE = os.path.join(STATE_DIR, "last_check.json")


def _load_last_state() -> dict[str, int]:
    """Load the last-check state from disk."""
    if not os.path.exists(STATE_FILE):
        return {}
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            state: dict[str, int] = json.load(f)
            return state
    except (json.JSONDecodeError, OSError):
        return {}


def _save_last_state(state: dict[str, int]) -> None:
    """Persist the current check state."""
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f)


@click.command("new-messages")
@click.option("--format", "fmt", default="json", type=click.Choice(["json", "text"]), help="Output format")
@click.pass_context
def new_messages(ctx: click.Context, fmt: str) -> None:
    """Get new messages since last check.

    \b
    Examples:
      wxq new-messages               # First call: returns unread, saves state
      wxq new-messages               # Subsequent: only new messages
      wxq new-messages --format text  # Plain text output
    \b
    State file: ~/.wxq/last_check.json (delete to reset)
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
        """).fetchall()

    curr_state: dict[str, dict[str, Any]] = {}
    for r in rows:
        username, unread, summary, ts, msg_type, sender, sender_name = r
        curr_state[username] = {
            "unread": unread,
            "summary": summary,
            "timestamp": ts,
            "msg_type": msg_type,
            "sender": sender or "",
            "sender_name": sender_name or "",
        }

    last_state = _load_last_state()

    if not last_state:
        # First call: save state, return unread
        _save_last_state({u: s["timestamp"] for u, s in curr_state.items()})

        unread_msgs = []
        for username, s in curr_state.items():
            if s["unread"] and s["unread"] > 0:
                display = names.get(username, username)
                is_group = "@chatroom" in username
                summary = s["summary"]
                if isinstance(summary, bytes):
                    summary = decompress_content(summary, 4) or "(compressed)"
                if isinstance(summary, str) and ":\n" in summary:
                    summary = summary.split(":\n", 1)[1]
                time_str = datetime.fromtimestamp(s["timestamp"]).strftime("%H:%M")
                unread_msgs.append({
                    "chat": display,
                    "username": username,
                    "is_group": is_group,
                    "unread": s["unread"],
                    "last_message": str(summary or ""),
                    "msg_type": format_msg_type(s["msg_type"]),
                    "time": time_str,
                    "timestamp": s["timestamp"],
                })

        if fmt == "json":
            output({"first_call": True, "unread_count": len(unread_msgs), "messages": unread_msgs}, "json")
        else:
            if unread_msgs:
                lines = []
                for m in unread_msgs:
                    tag = " [group]" if m["is_group"] else ""
                    lines.append(
                        f"[{m['time']}] {m['chat']}{tag} ({m['unread']} unread): {m['last_message']}"
                    )
                output(f"Currently {len(unread_msgs)} unread chats:\n\n" + "\n".join(lines), "text")
            else:
                output("No unread messages (state recorded, next call will return new messages)", "text")
        return

    # Subsequent calls: diff against saved state
    new_msgs = []
    for username, s in curr_state.items():
        prev_ts = last_state.get(username, 0)
        if s["timestamp"] > prev_ts:
            display = names.get(username, username)
            is_group = "@chatroom" in username
            summary = s["summary"]
            if isinstance(summary, bytes):
                summary = decompress_content(summary, 4) or "(compressed)"
            if isinstance(summary, str) and ":\n" in summary:
                summary = summary.split(":\n", 1)[1]

            sender_display = ""
            if is_group and s["sender"]:
                sender_display = names.get(s["sender"], s["sender_name"] or s["sender"])

            new_msgs.append({
                "chat": display,
                "username": username,
                "is_group": is_group,
                "last_message": str(summary or ""),
                "msg_type": format_msg_type(s["msg_type"]),
                "sender": sender_display,
                "time": datetime.fromtimestamp(s["timestamp"]).strftime("%H:%M:%S"),
                "timestamp": s["timestamp"],
            })

    _save_last_state({u: s["timestamp"] for u, s in curr_state.items()})
    new_msgs.sort(key=lambda m: m["timestamp"])

    if fmt == "json":
        output({"first_call": False, "new_count": len(new_msgs), "messages": new_msgs}, "json")
    else:
        if not new_msgs:
            output("No new messages", "text")
        else:
            lines = []
            for m in new_msgs:
                entry = f"[{m['time']}] {m['chat']}"
                if m["is_group"]:
                    entry += " [group]"
                entry += f": {m['msg_type']}"
                if m["sender"]:
                    entry += f" ({m['sender']})"
                entry += f" - {m['last_message']}"
                lines.append(entry)
            output(f"{len(new_msgs)} new messages:\n\n" + "\n".join(lines), "text")
