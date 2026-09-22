"""Message query service — chat history, search, stats, table discovery.

This is the main service layer that CLI commands and MCP tools call.
It replaces the query/routing portions of the original messages.py.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
from contextlib import closing
from datetime import datetime
from typing import Any, Callable

from ..core.key_utils import key_path_variants
from ..exceptions import (
    ChatNotFoundError,
    InvalidTimeRangeError,
    PaginationError,
)
from ..models.message import (
    ChatContext,
    ChatStats,
    HourlyDistribution,
    Message,
    MessageTableInfo,
    MessageType,
    SenderStat,
)
from .message_parser import (
    decompress_content,
    format_message_text,
    format_msg_type,
    split_msg_type,
)

_QUERY_LIMIT_MAX = 500
_HISTORY_QUERY_BATCH_SIZE = 500
_TABLE_NAME_RE = re.compile(r"Msg_[0-9a-f]{32}")


# ---- DB key discovery ----


def find_msg_db_keys(all_keys: dict[str, dict[str, Any]]) -> list[str]:
    """Find keys for message_N.db files within all_keys."""
    return sorted(
        k
        for k in all_keys
        if any(v.startswith("message/") for v in key_path_variants(k))
        and any(re.search(r"message_\d+\.db$", v) for v in key_path_variants(k))
    )


# ---- Table name validation ----


def _is_safe_msg_table_name(table_name: str) -> bool:
    """Validate table name to prevent SQL injection."""
    return bool(_TABLE_NAME_RE.fullmatch(table_name))


# ---- Table discovery ----


def _find_msg_tables_for_user(
    username: str,
    msg_db_keys: list[str],
    cache: Any,
) -> list[MessageTableInfo]:
    """Find message tables for a specific user across all message DBs."""
    table_hash = hashlib.md5(username.encode()).hexdigest()
    table_name = f"Msg_{table_hash}"
    if not _is_safe_msg_table_name(table_name):
        return []

    matches: list[MessageTableInfo] = []
    for rel_key in msg_db_keys:
        path = cache.get(rel_key)
        if not path:
            continue
        try:
            with closing(sqlite3.connect(path)) as conn:
                exists = conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                    (table_name,),
                ).fetchone()
                if not exists:
                    continue
                max_ct = (
                    conn.execute(
                        f"SELECT MAX(create_time) FROM [{table_name}]"
                    ).fetchone()[0]
                    or 0
                )
                matches.append(
                    MessageTableInfo(
                        db_path=path,
                        table_name=table_name,
                        max_create_time=max_ct,
                    )
                )
        except Exception:
            pass

    matches.sort(key=lambda x: x.max_create_time, reverse=True)
    return matches


# ---- Time parsing ----


def parse_time_value(
    value: str, field_name: str, is_end: bool = False
) -> int | None:
    """Parse a time string into a Unix timestamp."""
    value = (value or "").strip()
    if not value:
        return None
    formats = [
        ("%Y-%m-%d %H:%M:%S", False),
        ("%Y-%m-%d %H:%M", False),
        ("%Y-%m-%d", True),
    ]
    for fmt, date_only in formats:
        try:
            dt = datetime.strptime(value, fmt)
            if date_only and is_end:
                dt = dt.replace(hour=23, minute=59, second=59)
            return int(dt.timestamp())
        except ValueError:
            continue
    raise InvalidTimeRangeError(
        f"{field_name} 格式无效: {value}。"
        "支持 YYYY-MM-DD / YYYY-MM-DD HH:MM / YYYY-MM-DD HH:MM:SS"
    )


def parse_time_range(
    start_time: str = "", end_time: str = ""
) -> tuple[int | None, int | None]:
    """Parse start/end time strings into timestamp range."""
    start_ts = parse_time_value(start_time, "start_time", is_end=False)
    end_ts = parse_time_value(end_time, "end_time", is_end=True)
    if start_ts is not None and end_ts is not None and start_ts > end_ts:
        raise InvalidTimeRangeError("start_time 不能晚于 end_time")
    return start_ts, end_ts


def validate_pagination(
    limit: int, offset: int = 0, limit_max: int | None = _QUERY_LIMIT_MAX
) -> None:
    """Validate pagination parameters."""
    if limit <= 0:
        raise PaginationError("limit 必须大于 0")
    if limit_max is not None and limit > limit_max:
        raise PaginationError(f"limit 不能大于 {limit_max}")
    if offset < 0:
        raise PaginationError("offset 不能小于 0")


# ---- Chat context resolution ----


def resolve_chat_context(
    chat_name: str,
    msg_db_keys: list[str],
    cache: Any,
    contacts: Any,
) -> ChatContext | None:
    """Resolve a chat name to a ChatContext with message tables."""
    username = contacts.resolve_username(chat_name)
    if not username:
        return None
    names = contacts.get_names()
    display_name = names.get(username, username)
    message_tables = _find_msg_tables_for_user(username, msg_db_keys, cache)
    return ChatContext(
        query=chat_name,
        username=username,
        display_name=display_name,
        is_group="@chatroom" in username,
        message_tables=message_tables,
    )


# ---- SQL query helpers ----


def _build_message_filters(
    start_ts: int | None = None,
    end_ts: int | None = None,
    keyword: str = "",
    msg_type_filter: tuple[int, ...] | None = None,
) -> tuple[list[str], list[Any]]:
    """Build WHERE clause parts for message queries."""
    clauses: list[str] = []
    params: list[Any] = []
    if start_ts is not None:
        clauses.append("create_time >= ?")
        params.append(start_ts)
    if end_ts is not None:
        clauses.append("create_time <= ?")
        params.append(end_ts)
    if keyword:
        clauses.append("message_content LIKE ?")
        params.append(f"%{keyword}%")
    if msg_type_filter is not None:
        base_type = msg_type_filter[0]
        clauses.append("(local_type & 0xFFFFFFFF) = ?")
        params.append(base_type)
        if len(msg_type_filter) > 1:
            clauses.append("((local_type >> 32) & 0xFFFFFFFF) = ?")
            params.append(msg_type_filter[1])
    return clauses, params


def _query_messages(
    conn: sqlite3.Connection,
    table_name: str,
    start_ts: int | None = None,
    end_ts: int | None = None,
    keyword: str = "",
    limit: int | None = 20,
    offset: int = 0,
    msg_type_filter: tuple[int, ...] | None = None,
) -> list[tuple[Any, ...]]:
    """Execute a paginated message query against a validated table."""
    if not _is_safe_msg_table_name(table_name):
        raise ValueError(f"非法消息表名: {table_name}")
    clauses, params = _build_message_filters(start_ts, end_ts, keyword, msg_type_filter)
    where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    sql = f"""
        SELECT local_id, local_type, create_time, real_sender_id,
               message_content, WCDB_CT_message_content
        FROM [{table_name}]
        {where_sql}
        ORDER BY create_time DESC
    """
    if limit is None:
        return conn.execute(sql, params).fetchall()
    sql += "\n        LIMIT ? OFFSET ?"
    return conn.execute(sql, (*params, limit, offset)).fetchall()


# ---- Name2Id mapping ----


def _load_name2id_maps(conn: sqlite3.Connection) -> dict[int, str]:
    """Load rowid→username mapping from Name2Id table."""
    id_to_username: dict[int, str] = {}
    try:
        rows = conn.execute("SELECT rowid, user_name FROM Name2Id").fetchall()
    except sqlite3.Error:
        return id_to_username
    for rowid, user_name in rows:
        if not user_name:
            continue
        id_to_username[rowid] = user_name
    return id_to_username


# ---- Sender resolution ----


def _resolve_sender_label(
    real_sender_id: int,
    sender_from_content: str,
    is_group: bool,
    chat_username: str,
    chat_display_name: str,
    names: dict[str, str],
    id_to_username: dict[int, str],
    display_name_fn: Callable[..., str],
) -> str:
    """Resolve the display label for a message sender."""
    sender_username = id_to_username.get(real_sender_id, "")
    if is_group:
        if sender_username and sender_username != chat_username:
            return display_name_fn(sender_username, names)
        if sender_from_content:
            return display_name_fn(sender_from_content, names)
        return ""
    if sender_username == chat_username:
        return chat_display_name
    if sender_username:
        return display_name_fn(sender_username, names)
    return ""


# ---- History line building ----


def _build_history_line(
    row: tuple[Any, ...],
    ctx: ChatContext,
    names: dict[str, str],
    id_to_username: dict[int, str],
    display_name_fn: Callable[..., str],
    resolve_media: bool = False,
    db_dir: str | None = None,
) -> tuple[int, str]:
    """Build a formatted history line from a raw DB row."""
    local_id, local_type, create_time, real_sender_id, content, ct = row
    time_str = datetime.fromtimestamp(create_time).strftime("%Y-%m-%d %H:%M")
    content = decompress_content(content, ct)
    if content is None:
        content = "(无法解压)"

    sender, text = format_message_text(
        local_id,
        local_type,
        content,
        ctx.is_group,
        ctx.username,
        ctx.display_name,
        names,
        display_name_fn,
        db_dir=db_dir,
        create_time_ts=create_time,
        resolve_media=resolve_media,
    )
    sender_label = _resolve_sender_label(
        real_sender_id,
        sender,
        ctx.is_group,
        ctx.username,
        ctx.display_name,
        names,
        id_to_username,
        display_name_fn,
    )
    if sender_label:
        return create_time, f"[{time_str}] {sender_label}: {text}"
    return create_time, f"[{time_str}] {text}"


# ---- Chat history collection ----


def collect_chat_history(
    ctx: ChatContext,
    names: dict[str, str],
    display_name_fn: Callable[..., str],
    start_ts: int | None = None,
    end_ts: int | None = None,
    limit: int = 20,
    offset: int = 0,
    msg_type_filter: tuple[int, ...] | None = None,
    resolve_media: bool = False,
    db_dir: str | None = None,
) -> tuple[list[str], list[str]]:
    """Collect formatted chat history lines across multiple message tables.

    Returns:
        (lines, failures) — lines sorted chronologically, failures as error strings.
    """
    collected: list[tuple[int, str]] = []
    failures: list[str] = []
    candidate_limit = limit + offset
    batch_size = min(candidate_limit, _HISTORY_QUERY_BATCH_SIZE)

    for table_info in ctx.message_tables:
        try:
            with closing(sqlite3.connect(table_info.db_path)) as conn:
                id_to_username = _load_name2id_maps(conn)
                fetch_offset = 0
                before = len(collected)
                while len(collected) - before < candidate_limit:
                    rows = _query_messages(
                        conn,
                        table_info.table_name,
                        start_ts=start_ts,
                        end_ts=end_ts,
                        limit=batch_size,
                        offset=fetch_offset,
                        msg_type_filter=msg_type_filter,
                    )
                    if not rows:
                        break
                    fetch_offset += len(rows)
                    for row in rows:
                        try:
                            collected.append(
                                _build_history_line(
                                    row,
                                    ctx,
                                    names,
                                    id_to_username,
                                    display_name_fn,
                                    resolve_media=resolve_media,
                                    db_dir=db_dir,
                                )
                            )
                        except Exception as e:
                            failures.append(f"local_id={row[0]}: {e}")
                        if len(collected) - before >= candidate_limit:
                            break
                    if len(rows) < batch_size:
                        break
        except Exception as e:
            failures.append(f"{table_info.db_path}: {e}")

    # Sort by time, apply pagination
    ordered = sorted(collected, key=lambda item: item[0], reverse=True)
    paged = ordered[offset : offset + limit]
    paged.sort(key=lambda item: item[0])
    return [line for _, line in paged], failures


# ---- Search ----


def _build_search_entry(
    row: tuple[Any, ...],
    ctx: ChatContext,
    names: dict[str, str],
    id_to_username: dict[int, str],
    display_name_fn: Callable[..., str],
    resolve_media: bool = False,
    db_dir: str | None = None,
) -> tuple[int, str] | None:
    """Build a formatted search result entry from a raw DB row."""
    local_id, local_type, create_time, real_sender_id, content, ct = row
    content = decompress_content(content, ct)
    if content is None:
        return None
    sender, text = format_message_text(
        local_id,
        local_type,
        content,
        ctx.is_group,
        ctx.username,
        ctx.display_name,
        names,
        display_name_fn,
        db_dir=db_dir,
        create_time_ts=create_time,
        resolve_media=resolve_media,
    )
    if text and len(text) > 300:
        text = text[:300] + "..."
    sender_label = _resolve_sender_label(
        real_sender_id,
        sender,
        ctx.is_group,
        ctx.username,
        ctx.display_name,
        names,
        id_to_username,
        display_name_fn,
    )
    time_str = datetime.fromtimestamp(create_time).strftime("%Y-%m-%d %H:%M")
    entry = f"[{time_str}] [{ctx.display_name}]"
    if sender_label:
        entry += f" {sender_label}:"
    entry += f" {text}"
    return create_time, entry


def collect_chat_search(
    ctx: ChatContext,
    names: dict[str, str],
    keyword: str,
    display_name_fn: Callable[..., str],
    start_ts: int | None = None,
    end_ts: int | None = None,
    candidate_limit: int = 20,
    msg_type_filter: tuple[int, ...] | None = None,
) -> tuple[list[tuple[int, str]], list[str]]:
    """Search within a specific chat's message tables."""
    collected: list[tuple[int, str]] = []
    failures: list[str] = []

    # Group tables by DB path to share connections
    tables_by_db: dict[str, list[MessageTableInfo]] = {}
    for t in ctx.message_tables:
        tables_by_db.setdefault(t.db_path, []).append(t)

    for db_path, tables in tables_by_db.items():
        try:
            with closing(sqlite3.connect(db_path)) as conn:
                id_to_username = _load_name2id_maps(conn)
                for table_info in tables:
                    try:
                        fetch_offset = 0
                        before = len(collected)
                        while len(collected) - before < candidate_limit:
                            rows = _query_messages(
                                conn,
                                table_info.table_name,
                                start_ts=start_ts,
                                end_ts=end_ts,
                                keyword=keyword,
                                limit=candidate_limit,
                                offset=fetch_offset,
                                msg_type_filter=msg_type_filter,
                            )
                            if not rows:
                                break
                            fetch_offset += len(rows)
                            for row in rows:
                                entry = _build_search_entry(
                                    row, ctx, names, id_to_username, display_name_fn
                                )
                                if entry:
                                    collected.append(entry)
                                    if len(collected) - before >= candidate_limit:
                                        break
                            if len(rows) < candidate_limit:
                                break
                    except Exception as e:
                        failures.append(f"{ctx.display_name}: {e}")
        except Exception as e:
            failures.append(f"{db_path}: {e}")

    return collected, failures


def search_all_messages(
    msg_db_keys: list[str],
    cache: Any,
    names: dict[str, str],
    keyword: str,
    display_name_fn: Callable[..., str],
    start_ts: int | None = None,
    end_ts: int | None = None,
    candidate_limit: int = 20,
    msg_type_filter: tuple[int, ...] | None = None,
) -> tuple[list[tuple[int, str]], list[str]]:
    """Search across ALL message databases (global search)."""
    collected: list[tuple[int, str]] = []
    failures: list[str] = []
    for rel_key in msg_db_keys:
        path = cache.get(rel_key)
        if not path:
            continue
        try:
            with closing(sqlite3.connect(path)) as conn:
                contexts = _load_search_contexts_from_db(conn, path, names)
                id_to_username = _load_name2id_maps(conn)
                for ctx in contexts:
                    try:
                        fetch_offset = 0
                        before = len(collected)
                        while len(collected) - before < candidate_limit:
                            rows = _query_messages(
                                conn,
                                ctx.table_name or "",
                                start_ts=start_ts,
                                end_ts=end_ts,
                                keyword=keyword,
                                limit=candidate_limit,
                                offset=fetch_offset,
                                msg_type_filter=msg_type_filter,
                            )
                            if not rows:
                                break
                            fetch_offset += len(rows)
                            for row in rows:
                                entry = _build_search_entry(
                                    row, ctx, names, id_to_username, display_name_fn
                                )
                                if entry:
                                    collected.append(entry)
                                    if len(collected) - before >= candidate_limit:
                                        break
                            if len(rows) < candidate_limit:
                                break
                    except Exception as e:
                        failures.append(f"{ctx.display_name}: {e}")
        except Exception as e:
            failures.append(f"{rel_key}: {e}")
    return collected, failures


def _load_search_contexts_from_db(
    conn: sqlite3.Connection,
    db_path: str,
    names: dict[str, str],
) -> list[ChatContext]:
    """Load all chat contexts from a single message DB for search."""
    tables = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'Msg_%'"
    ).fetchall()
    table_to_username: dict[str, str] = {}
    try:
        for (user_name,) in conn.execute("SELECT user_name FROM Name2Id").fetchall():
            if not user_name:
                continue
            table_hash = hashlib.md5(user_name.encode()).hexdigest()
            table_to_username[f"Msg_{table_hash}"] = user_name
    except sqlite3.Error:
        pass

    contexts: list[ChatContext] = []
    for (table_name,) in tables:
        username = table_to_username.get(table_name, "")
        display_name = names.get(username, username) if username else table_name
        contexts.append(
            ChatContext(
                query=display_name,
                username=username,
                display_name=display_name,
                is_group="@chatroom" in username,
                message_tables=[
                    MessageTableInfo(db_path=db_path, table_name=table_name)
                ],
            )
        )
    return contexts


# ---- Stats ----


def collect_chat_stats(
    ctx: ChatContext,
    names: dict[str, str],
    display_name_fn: Callable[..., str],
    start_ts: int | None = None,
    end_ts: int | None = None,
) -> ChatStats:
    """Aggregate statistics for a chat."""
    type_map = {
        1: "文本", 3: "图片", 34: "语音", 42: "名片",
        43: "视频", 47: "表情", 48: "位置", 49: "链接/文件",
        50: "通话", 10000: "系统", 10002: "撤回",
    }

    total = 0
    type_counts: dict[str, int] = {}
    sender_counts: dict[str, int] = {}
    hourly_counts: dict[int, int] = {}

    for table_info in ctx.message_tables:
        try:
            with closing(sqlite3.connect(table_info.db_path)) as conn:
                id_to_username = _load_name2id_maps(conn)
                tbl = table_info.table_name
                if not _is_safe_msg_table_name(tbl):
                    continue

                where_parts: list[str] = []
                params: list[Any] = []
                if start_ts is not None:
                    where_parts.append("create_time >= ?")
                    params.append(start_ts)
                if end_ts is not None:
                    where_parts.append("create_time <= ?")
                    params.append(end_ts)
                where_sql = (
                    f"WHERE {' AND '.join(where_parts)}" if where_parts else ""
                )

                # Type breakdown
                for bt, cnt in conn.execute(
                    f"SELECT (local_type & 0xFFFFFFFF), COUNT(*) "
                    f"FROM [{tbl}] {where_sql} "
                    f"GROUP BY (local_type & 0xFFFFFFFF)",
                    params,
                ).fetchall():
                    label = type_map.get(bt, f"type={bt}")
                    type_counts[label] = type_counts.get(label, 0) + cnt
                    total += cnt

                # Sender ranking
                for sid, cnt in conn.execute(
                    f"SELECT real_sender_id, COUNT(*) "
                    f"FROM [{tbl}] {where_sql} "
                    f"GROUP BY real_sender_id ORDER BY COUNT(*) DESC LIMIT 20",
                    params,
                ).fetchall():
                    uname = id_to_username.get(sid, str(sid))
                    if uname:
                        sender_counts[uname] = sender_counts.get(uname, 0) + cnt

                # Hourly distribution
                for h, cnt in conn.execute(
                    f"SELECT cast(strftime('%H', create_time, 'unixepoch', 'localtime') as integer), "
                    f"COUNT(*) FROM [{tbl}] {where_sql} "
                    f"GROUP BY cast(strftime('%H', create_time, 'unixepoch', 'localtime') as integer)",
                    params,
                ).fetchall():
                    if h is not None:
                        hourly_counts[h] = hourly_counts.get(h, 0) + cnt
        except Exception:
            pass

    top_senders_raw = sorted(sender_counts.items(), key=lambda x: x[1], reverse=True)[
        :10
    ]
    top_senders = [
        SenderStat(name=display_name_fn(u, names), count=c)
        for u, c in top_senders_raw
    ]

    return ChatStats(
        total=total,
        type_breakdown=dict(
            sorted(type_counts.items(), key=lambda x: x[1], reverse=True)
        ),
        top_senders=top_senders,
        hourly=HourlyDistribution(counts=hourly_counts),
    )
