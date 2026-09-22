"""MCP server — expose WeChat query as Model Context Protocol tools.

Run with:
    wxq-mcp           (entry point)
    python -m wxq.mcp (module)

This server exposes WeChat data as read-only MCP tools, allowing
AI agents to query messages, contacts, sessions, and statistics.
"""

from __future__ import annotations

import json
import os
import traceback
from typing import Any

try:
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from mcp.types import TextContent, Tool
except ImportError:
    raise ImportError(
        "MCP server requires the 'mcp' package.\n"
        "Install with: pip install wxq[mcp]"
    )

from ..core.context import AppContext
from ..models.message import MSG_TYPE_FILTERS
from ..services.message_parser import decompress_content, format_msg_type
from ..services.message_service import (
    collect_chat_history,
    collect_chat_search,
    collect_chat_stats,
    parse_time_range,
    resolve_chat_context,
    search_all_messages,
    validate_pagination,
)

_server = Server("wxq")
_app: AppContext | None = None


def _get_app() -> AppContext:
    """Get or create the application context (singleton per server)."""
    global _app
    if _app is None:
        config_path = os.environ.get("WXQ_CONFIG") or os.environ.get(
            "WECHAT_QUERY_CONFIG"
        )
        _app = AppContext(config_path)
    return _app


def _json_response(data: Any) -> list[TextContent]:
    """Wrap data as a JSON text content block."""
    return [TextContent(type="text", text=json.dumps(data, ensure_ascii=False, indent=2))]


def _error_response(msg: str) -> list[TextContent]:
    """Wrap an error message."""
    return [TextContent(type="text", text=json.dumps({"error": msg}, ensure_ascii=False))]


# ---- Tool definitions ----

TOOLS = [
    Tool(
        name="get_sessions",
        description="List recent WeChat chat sessions with last message preview",
        inputSchema={
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "default": 20, "description": "Number of sessions"},
            },
        },
    ),
    Tool(
        name="get_unread",
        description="List sessions with unread messages",
        inputSchema={
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "default": 50},
            },
        },
    ),
    Tool(
        name="get_contacts",
        description="Search or list WeChat contacts",
        inputSchema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "default": "", "description": "Search keyword"},
                "limit": {"type": "integer", "default": 50},
            },
        },
    ),
    Tool(
        name="get_contact_detail",
        description="Get detailed info for a specific contact",
        inputSchema={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Nickname, remark, or wxid"},
            },
            "required": ["name"],
        },
    ),
    Tool(
        name="get_chat_history",
        description="Retrieve message history for a specific chat",
        inputSchema={
            "type": "object",
            "properties": {
                "chat_name": {"type": "string", "description": "Chat name or username"},
                "limit": {"type": "integer", "default": 50},
                "offset": {"type": "integer", "default": 0},
                "start_time": {"type": "string", "default": ""},
                "end_time": {"type": "string", "default": ""},
                "msg_type": {
                    "type": "string", "enum": list(MSG_TYPE_FILTERS.keys()),
                    "description": "Filter by message type",
                },
            },
            "required": ["chat_name"],
        },
    ),
    Tool(
        name="search_messages",
        description="Search messages by keyword, optionally within specific chats",
        inputSchema={
            "type": "object",
            "properties": {
                "keyword": {"type": "string", "description": "Search keyword"},
                "chat_name": {"type": "string", "description": "Limit to specific chat"},
                "limit": {"type": "integer", "default": 20},
                "offset": {"type": "integer", "default": 0},
                "start_time": {"type": "string", "default": ""},
                "end_time": {"type": "string", "default": ""},
            },
            "required": ["keyword"],
        },
    ),
    Tool(
        name="get_chat_stats",
        description="Get statistics for a specific chat (message counts, type breakdown, top senders, hourly activity)",
        inputSchema={
            "type": "object",
            "properties": {
                "chat_name": {"type": "string"},
                "start_time": {"type": "string", "default": ""},
                "end_time": {"type": "string", "default": ""},
            },
            "required": ["chat_name"],
        },
    ),
    Tool(
        name="get_group_members",
        description="List members of a group chat",
        inputSchema={
            "type": "object",
            "properties": {
                "group_name": {"type": "string", "description": "Group name or username"},
            },
            "required": ["group_name"],
        },
    ),
]


@_server.list_tools()  # type: ignore[no-untyped-call, untyped-decorator]
async def list_tools() -> list[Tool]:
    return TOOLS


@_server.call_tool()  # type: ignore[untyped-decorator]
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    try:
        app = _get_app()

        if name == "get_sessions":
            return _handle_sessions(app, arguments)
        elif name == "get_unread":
            return _handle_unread(app, arguments)
        elif name == "get_contacts":
            return _handle_contacts(app, arguments)
        elif name == "get_contact_detail":
            return _handle_contact_detail(app, arguments)
        elif name == "get_chat_history":
            return _handle_chat_history(app, arguments)
        elif name == "search_messages":
            return _handle_search(app, arguments)
        elif name == "get_chat_stats":
            return _handle_stats(app, arguments)
        elif name == "get_group_members":
            return _handle_members(app, arguments)
        else:
            return _error_response(f"Unknown tool: {name}")

    except Exception as e:
        return _error_response(f"{type(e).__name__}: {e}")


# ---- Tool handlers ----

def _handle_sessions(app: AppContext, args: dict[str, Any]) -> list[TextContent]:
    import sqlite3
    from contextlib import closing
    from datetime import datetime

    limit = args.get("limit", 20)
    path = app.cache.get(os.path.join("session", "session.db"))
    if not path:
        return _error_response("Cannot decrypt session.db")

    names = app.contacts.get_names()
    with closing(sqlite3.connect(path)) as conn:
        rows = conn.execute("""
            SELECT username, unread_count, summary, last_timestamp,
                   last_msg_type, last_msg_sender, last_sender_display_name
            FROM SessionTable WHERE last_timestamp > 0
            ORDER BY last_timestamp DESC LIMIT ?
        """, (limit,)).fetchall()

    results = []
    for r in rows:
        username, unread, summary, ts, msg_type, sender, sender_name = r
        display = names.get(username, username)
        is_group = "@chatroom" in username
        if isinstance(summary, bytes):
            summary = decompress_content(summary, 4) or ""
        if isinstance(summary, str) and ":\n" in summary:
            summary = summary.split(":\n", 1)[1]
        sender_display = ""
        if is_group and sender:
            sender_display = names.get(sender, sender_name or sender)
        results.append({
            "chat": display, "username": username, "is_group": is_group,
            "unread": unread or 0, "last_message": str(summary or ""),
            "msg_type": format_msg_type(msg_type), "sender": sender_display,
            "timestamp": ts, "time": datetime.fromtimestamp(ts).strftime("%m-%d %H:%M"),
        })
    return _json_response(results)


def _handle_unread(app: AppContext, args: dict[str, Any]) -> list[TextContent]:
    import sqlite3
    from contextlib import closing
    from datetime import datetime

    limit = args.get("limit", 50)
    path = app.cache.get(os.path.join("session", "session.db"))
    if not path:
        return _error_response("Cannot decrypt session.db")

    names = app.contacts.get_names()
    with closing(sqlite3.connect(path)) as conn:
        rows = conn.execute("""
            SELECT username, unread_count, summary, last_timestamp,
                   last_msg_type, last_msg_sender, last_sender_display_name
            FROM SessionTable WHERE unread_count > 0
            ORDER BY last_timestamp DESC LIMIT ?
        """, (limit,)).fetchall()

    results = []
    for r in rows:
        username, unread, summary, ts, msg_type, sender, sender_name = r
        display = names.get(username, username)
        is_group = "@chatroom" in username
        if isinstance(summary, bytes):
            summary = decompress_content(summary, 4) or ""
        if isinstance(summary, str) and ":\n" in summary:
            summary = summary.split(":\n", 1)[1]
        sender_display = ""
        if is_group and sender:
            sender_display = names.get(sender, sender_name or sender)
        results.append({
            "chat": display, "username": username, "is_group": is_group,
            "unread": unread or 0, "last_message": str(summary or ""),
            "msg_type": format_msg_type(msg_type), "sender": sender_display,
            "timestamp": ts, "time": datetime.fromtimestamp(ts).strftime("%m-%d %H:%M"),
        })
    return _json_response(results)


def _handle_contacts(app: AppContext, args: dict[str, Any]) -> list[TextContent]:
    query = args.get("query", "")
    limit = args.get("limit", 50)
    full = app.contacts.get_contacts()
    if query:
        q_lower = query.lower()
        full = [
            c for c in full
            if q_lower in (c.nick_name or "").lower()
            or q_lower in (c.remark or "").lower()
            or q_lower in (c.username or "").lower()
        ]
    serialized = [
        {"username": c.username, "nick_name": c.nick_name,
         "remark": c.remark, "display_name": c.display_name,
         "is_group": c.is_group}
        for c in full[:limit]
    ]
    return _json_response(serialized)


def _handle_contact_detail(app: AppContext, args: dict[str, Any]) -> list[TextContent]:
    name = args["name"]
    username = app.contacts.resolve_username(name) or name
    info = app.contacts.get_contact_detail(username)
    if not info:
        return _error_response(f"Contact not found: {name}")
    return _json_response(info.to_dict() if hasattr(info, "to_dict") else info)


def _handle_chat_history(app: AppContext, args: dict[str, Any]) -> list[TextContent]:
    chat_name = args["chat_name"]
    limit = args.get("limit", 50)
    offset = args.get("offset", 0)
    start_time = args.get("start_time", "")
    end_time = args.get("end_time", "")
    msg_type_name = args.get("msg_type")

    validate_pagination(limit, offset, limit_max=None)
    start_ts, end_ts = parse_time_range(start_time, end_time)

    chat_ctx = resolve_chat_context(
        chat_name, app.msg_db_keys, app.cache, app.contacts
    )
    if not chat_ctx or not chat_ctx.db_path:
        return _error_response(f"Chat not found or no messages: {chat_name}")

    names = app.contacts.get_names()
    type_filter = MSG_TYPE_FILTERS[msg_type_name] if msg_type_name else None
    lines, failures = collect_chat_history(
        chat_ctx, names, app.display_name_fn,
        start_ts=start_ts, end_ts=end_ts, limit=limit, offset=offset,
        msg_type_filter=type_filter,
    )

    return _json_response({
        "chat": chat_ctx.display_name,
        "username": chat_ctx.username,
        "is_group": chat_ctx.is_group,
        "count": len(lines),
        "offset": offset,
        "limit": limit,
        "messages": lines,
        "failures": failures if failures else None,
    })


def _handle_search(app: AppContext, args: dict[str, Any]) -> list[TextContent]:
    keyword = args["keyword"]
    chat_name = args.get("chat_name", "")
    limit = args.get("limit", 20)
    offset = args.get("offset", 0)
    start_time = args.get("start_time", "")
    end_time = args.get("end_time", "")

    validate_pagination(limit, offset)
    start_ts, end_ts = parse_time_range(start_time, end_time)
    names = app.contacts.get_names()
    candidate_limit = max(limit + offset, 100) * 3

    if chat_name:
        chat_ctx = resolve_chat_context(
            chat_name, app.msg_db_keys, app.cache, app.contacts
        )
        if not chat_ctx or not chat_ctx.db_path:
            return _error_response(f"Chat not found: {chat_name}")
        entries, failures = collect_chat_search(
            chat_ctx, names, keyword, app.display_name_fn,
            start_ts=start_ts, end_ts=end_ts, candidate_limit=candidate_limit,
        )
        scope = chat_ctx.display_name
    else:
        entries, failures = search_all_messages(
            app.msg_db_keys, app.cache, names, keyword, app.display_name_fn,
            start_ts=start_ts, end_ts=end_ts, candidate_limit=candidate_limit,
        )
        scope = "all messages"

    entries.sort(key=lambda x: x[0], reverse=True)
    paged = entries[offset : offset + limit]

    return _json_response({
        "scope": scope,
        "keyword": keyword,
        "count": len(paged),
        "offset": offset,
        "limit": limit,
        "results": [item[1] for item in paged],
        "failures": failures if failures else None,
    })


def _handle_stats(app: AppContext, args: dict[str, Any]) -> list[TextContent]:
    chat_name = args["chat_name"]
    start_time = args.get("start_time", "")
    end_time = args.get("end_time", "")

    start_ts, end_ts = parse_time_range(start_time, end_time)

    chat_ctx = resolve_chat_context(
        chat_name, app.msg_db_keys, app.cache, app.contacts
    )
    if not chat_ctx or not chat_ctx.db_path:
        return _error_response(f"Chat not found: {chat_name}")

    names = app.contacts.get_names()
    result = collect_chat_stats(
        chat_ctx, names, app.display_name_fn,
        start_ts=start_ts, end_ts=end_ts,
    )

    return _json_response({
        "chat": chat_ctx.display_name,
        "username": chat_ctx.username,
        "is_group": chat_ctx.is_group,
        **result.to_dict(),
    })


def _handle_members(app: AppContext, args: dict[str, Any]) -> list[TextContent]:
    group_name = args["group_name"]
    username = app.contacts.resolve_username(group_name)
    if not username:
        return _error_response(f"Group not found: {group_name}")
    if "@chatroom" not in username:
        return _error_response(f"{group_name} is not a group chat")

    names = app.contacts.get_names()
    display_name = names.get(username, username)
    result = app.contacts.get_group_members(username)

    return _json_response({
        "group": display_name,
        "username": username,
        "member_count": result.member_count,
        "owner": result.owner,
        "members": [
            {"username": m.username, "nick_name": m.nick_name,
             "remark": m.remark, "display_name": m.display_name}
            for m in result.members
        ],
    })


async def run_server() -> None:
    """Run the MCP server over stdio."""
    async with stdio_server() as (read_stream, write_stream):
        await _server.run(
            read_stream,
            write_stream,
            _server.create_initialization_options(),
        )


def main() -> None:
    """Entry point for wxq-mcp command."""
    import asyncio
    asyncio.run(run_server())
