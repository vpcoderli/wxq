"""Message content parsing — decompression, XML extraction, type formatting.

Split out from the original monolithic messages.py for maintainability.
"""

from __future__ import annotations

import hashlib
import os
import re
import xml.etree.ElementTree as ET
from typing import Any, Callable

import zstandard as zstd

from ..models.message import MessageType

_zstd_dctx = zstd.ZstdDecompressor()
_XML_UNSAFE_RE = re.compile(r"<!DOCTYPE|<!ENTITY", re.IGNORECASE)
_XML_PARSE_MAX_LEN = 20000


def decompress_content(content: bytes | str | None, ct: int | None) -> str | None:
    """Decompress message content (zstd type-4 or raw bytes)."""
    if ct and ct == 4 and isinstance(content, bytes):
        try:
            return _zstd_dctx.decompress(content).decode("utf-8", errors="replace")
        except Exception:
            return None
    if isinstance(content, bytes):
        try:
            return content.decode("utf-8", errors="replace")
        except Exception:
            return None
    return content


def format_msg_type(raw_type: int) -> str:
    """Human-readable label for a raw message type value."""
    base_type, _ = split_msg_type(raw_type)
    return MessageType.label(base_type)


def split_msg_type(t: int) -> tuple[int, int]:
    """Split a combined message type into (base_type, sub_type)."""
    try:
        t = int(t)
    except (TypeError, ValueError):
        return 0, 0
    if t > 0xFFFFFFFF:
        return t & 0xFFFFFFFF, t >> 32
    return t, 0


def parse_message_content(
    content: str | bytes | None,
    local_type: int,
    is_group: bool,
) -> tuple[str, str]:
    """Parse raw message content into (sender_from_content, text).

    In group chats, the content is prefixed with "sender:\\n".
    """
    if content is None:
        return "", ""
    if isinstance(content, bytes):
        return "", "(二进制内容)"
    sender = ""
    text = content
    if is_group and ":\n" in content:
        sender, text = content.split(":\n", 1)
    return sender, text


def _collapse_text(text: str) -> str:
    """Collapse whitespace in text."""
    if not text:
        return ""
    return re.sub(r"\s+", " ", text).strip()


def _parse_xml_root(content: str) -> ET.Element | None:
    """Safely parse XML content with size and entity limits."""
    if (
        not content
        or len(content) > _XML_PARSE_MAX_LEN
        or _XML_UNSAFE_RE.search(content)
    ):
        return None
    try:
        return ET.fromstring(content)
    except ET.ParseError:
        return None


def _parse_int(value: str | None, fallback: int = 0) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return fallback


def format_app_message(
    content: str,
    local_type: int,
    is_group: bool,
    chat_username: str,
    chat_display_name: str,
    names: dict[str, str],
    display_name_fn: Callable[..., str],
    resolve_media: bool = False,
    db_dir: str | None = None,
    create_time_ts: int = 0,
) -> str | None:
    """Format app messages (type 49) — links, files, quotes, mini-programs."""
    if not content or "<appmsg" not in content:
        return None
    _, sub_type = split_msg_type(local_type)
    root = _parse_xml_root(content)
    if root is None:
        return None
    appmsg = root.find(".//appmsg")
    if appmsg is None:
        return None
    title = _collapse_text(appmsg.findtext("title") or "")
    app_type = _parse_int(
        (appmsg.findtext("type") or "").strip(), _parse_int(str(sub_type), 0)
    )

    # Quote message (type 57)
    if app_type == 57:
        ref = appmsg.find(".//refermsg")
        ref_content = ""
        ref_display_name = ""
        if ref is not None:
            ref_display_name = (ref.findtext("displayname") or "").strip()
            ref_content = _collapse_text(ref.findtext("content") or "")
        if len(ref_content) > 160:
            ref_content = ref_content[:160] + "..."
        quote_text = title or "[引用消息]"
        if ref_content:
            prefix = f"回复 {ref_display_name}: " if ref_display_name else "回复: "
            quote_text += f"\n  ↳ {prefix}{ref_content}"
        return quote_text

    # File (type 6)
    if app_type == 6:
        if resolve_media and db_dir and title:
            msg_dir = os.path.join(os.path.dirname(db_dir), "msg", "file")
            if os.path.isdir(msg_dir):
                from datetime import datetime

                dt = (
                    datetime.fromtimestamp(create_time_ts) if create_time_ts else None
                )
                if dt:
                    file_dir = os.path.join(msg_dir, dt.strftime("%Y-%m"))
                    if os.path.isdir(file_dir):
                        target = os.path.join(file_dir, title)
                        if os.path.isfile(target):
                            return f"[文件] {title}\n  {target}"
                        for f in os.listdir(file_dir):
                            if title in f or f in title:
                                return f"[文件] {title}\n  {os.path.join(file_dir, f)}"
        return f"[文件] {title}" if title else "[文件]"

    # Link (type 5)
    if app_type == 5:
        return f"[链接] {title}" if title else "[链接]"

    # Mini-program (types 33, 36, 44)
    if app_type in (33, 36, 44):
        return f"[小程序] {title}" if title else "[小程序]"

    if title:
        return f"[链接/文件] {title}"
    return "[链接/文件]"


def format_voip_message(content: str) -> str:
    """Format VoIP call messages (type 50)."""
    if not content or "<voip" not in content:
        return "[通话]"
    root = _parse_xml_root(content)
    if root is None:
        return "[通话]"
    raw_text = _collapse_text(root.findtext(".//msg") or "")
    if not raw_text:
        return "[通话]"
    status_map = {
        "Canceled": "已取消",
        "Line busy": "对方忙线",
        "Call not answered": "未接听",
        "Call wasn't answered": "未接听",
    }
    if raw_text.startswith("Duration:"):
        duration = raw_text.split(":", 1)[1].strip()
        return f"[通话] 通话时长 {duration}" if duration else "[通话]"
    return f"[通话] {status_map.get(raw_text, raw_text)}"


def resolve_media_path(
    db_dir: str,
    content: str | None,
    local_type: int,
    create_time_ts: int,
    chat_username: str | None = None,
) -> tuple[str | None, bool]:
    """Try to resolve a media file's path on disk.

    Returns:
        (path, exists) — path is None if resolution fails.
    """
    if not content:
        return None, False

    base_type = local_type & 0xFFFFFFFF
    wechat_base = os.path.dirname(db_dir)
    msg_dir = os.path.join(wechat_base, "msg")
    if not os.path.isdir(msg_dir):
        return None, False

    from datetime import datetime

    dt = datetime.fromtimestamp(create_time_ts)
    date_prefix = dt.strftime("%Y-%m")

    # File messages (type 49, subtype 6)
    if base_type == 49:
        root = _parse_xml_root(content)
        if root is not None:
            appmsg = root.find(".//appmsg")
            if appmsg is not None:
                app_type = _parse_int((appmsg.findtext("type") or "").strip())
                if app_type == 6:
                    title = (appmsg.findtext("title") or "").strip()
                    if title:
                        file_dir = os.path.join(msg_dir, "file", date_prefix)
                        if os.path.isdir(file_dir):
                            target = os.path.join(file_dir, title)
                            if os.path.isfile(target):
                                return target, True
                            for f in os.listdir(file_dir):
                                if title in f or f in title:
                                    return os.path.join(file_dir, f), True
        return None, False

    # Image / Voice / Video (types 3, 34, 43)
    if base_type in (3, 34, 43):
        attach_dir = os.path.join(msg_dir, "attach")
        if not os.path.isdir(attach_dir):
            return None, False

        target_hash: str | None = None
        if chat_username:
            h = hashlib.md5(chat_username.encode()).hexdigest()
            candidate = os.path.join(attach_dir, h)
            if os.path.isdir(candidate):
                target_hash = h

        search_dirs = (
            [target_hash]
            if target_hash
            else [
                d
                for d in os.listdir(attach_dir)
                if os.path.isdir(os.path.join(attach_dir, d))
            ]
        )

        sub_dir_name = (
            "Img" if base_type == 3 else ("Video" if base_type == 43 else "Voice")
        )

        for d in search_dirs:
            if d is None:
                continue
            sub = os.path.join(attach_dir, d, date_prefix, sub_dir_name)
            if os.path.isdir(sub):
                files = [f for f in os.listdir(sub) if not f.endswith("_h.dat")]
                if files:
                    sample = files[0]
                    return os.path.join(sub, sample), True

        if base_type == 43:
            video_dir = os.path.join(msg_dir, "video", date_prefix)
            if os.path.isdir(video_dir):
                thumbs = [f for f in os.listdir(video_dir) if f.endswith("_thumb.jpg")]
                if thumbs:
                    return os.path.join(video_dir, thumbs[0]), True

    return None, False


def format_message_text(
    local_id: int,
    local_type: int,
    content: str | None,
    is_group: bool,
    chat_username: str,
    chat_display_name: str,
    names: dict[str, str],
    display_name_fn: Callable[..., str],
    db_dir: str | None = None,
    create_time_ts: int = 0,
    resolve_media: bool = False,
) -> tuple[str, str]:
    """Format a message into (sender_from_content, display_text)."""
    sender, text = parse_message_content(content, local_type, is_group)
    base_type, _ = split_msg_type(local_type)

    media_path: str | None = None
    media_exists = False
    if resolve_media and db_dir and content:
        try:
            media_path, media_exists = resolve_media_path(
                db_dir, content, local_type, create_time_ts, chat_username
            )
        except Exception:
            pass

    if base_type == 3:
        if media_path:
            tag = f"[图片] {media_path}"
            if not media_exists:
                tag += " (文件不存在)"
        else:
            tag = f"[图片] (local_id={local_id})"
        text = tag
    elif base_type == 47:
        text = "[表情]"
    elif base_type == 50:
        text = format_voip_message(text) or "[通话]"
    elif base_type == 49:
        text = (
            format_app_message(
                text,
                local_type,
                is_group,
                chat_username,
                chat_display_name,
                names,
                display_name_fn,
                resolve_media=resolve_media,
                db_dir=db_dir,
                create_time_ts=create_time_ts,
            )
            or "[链接/文件]"
        )
    elif base_type != 1:
        type_label = format_msg_type(local_type)
        text = f"[{type_label}] {text}" if text else f"[{type_label}]"

    return sender, text
