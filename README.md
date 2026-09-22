# wxq — 微信本地聊天记录查询工具

[![CI](https://github.com/vpcoderli/wxq/actions/workflows/ci.yml/badge.svg)](https://github.com/vpcoderli/wxq/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.10%20|%203.11%20|%203.12%20|%203.13-blue)
[![License](https://img.shields.io/badge/license-MIT-green)](https://github.com/vpcoderli/wxq/blob/main/LICENSE)

> WeChat local chat history query — CLI + MCP server for AI agents
> 微信聊天记录 · 本地数据库解密 · 命令行查询 · MCP 服务

`wxq` reads the encrypted WeChat SQLite databases on your machine, decrypts them locally, and
exposes messages, contacts, sessions, and statistics through a clean CLI and a Model Context
Protocol (MCP) server. Nothing leaves your machine.

Python-native by design: `pip install`-able, `import`-able, and typed — so it can be embedded
directly into Python agent frameworks rather than shelled out to as a binary.

## Features

- **SQLCipher 4 decryption** — AES-256-CBC with HMAC-SHA512 verification, WAL support
- **Automatic key extraction** — scan WeChat process memory on macOS, Windows, and Linux
- **CLI with 11 subcommands** — sessions, history, search, contacts, stats, export, and more
- **MCP server mode** — expose WeChat data as 8 read-only tools for AI agents (Claude, etc.)
- **Incremental message tracking** — `new-messages` shows only what's arrived since last check
- **Time range filtering** — query by date/datetime across all commands
- **Message type filtering** — filter by text, image, video, voice, file, link, sticker, system
- **Group chat support** — member lists, per-sender stats, hourly activity breakdown
- **zstd decompression** — handles WCDB compressed content transparently
- **Mtime-based DB cache** — decrypted databases are cached and refreshed only when the source changes
- **Typed** — ships `py.typed`; `mypy --strict` passes clean

## Requirements

- Python 3.10+
- WeChat desktop app (macOS, Windows, or Linux)
- WeChat must have been logged in at least once (so the local databases exist)

## Installation

From source (works today):

```bash
git clone https://github.com/vpcoderli/wxq.git
cd wxq
pip install -e ".[mcp]"
```

Once published to PyPI:

```bash
pip install wxq          # core
pip install "wxq[mcp]"   # with MCP server support
```

For development, see [Testing](#testing) below.

## Quick Start

### 1. Extract encryption keys

```bash
wxq init
```

This scans the running WeChat process memory to extract database encryption keys and writes them
to `~/.wxq/`. On macOS and Linux this may require `sudo`.

### 2. Query your data

```bash
# Recent chat sessions
wxq sessions

# Unread messages
wxq unread

# Chat history with a contact
wxq history "Alice" --limit 50

# Search messages globally
wxq search "meeting notes"

# Search within a specific chat
wxq search "project" --chat "Work Group"

# Time-filtered history
wxq history "Alice" --start-time "2024-01-01" --end-time "2024-06-30"

# Contact list
wxq contacts --query "Li"

# Contact details
wxq contact-detail "Alice"

# Group members
wxq members "Work Group"

# Chat statistics
wxq stats "Work Group"

# Export chat to file
wxq export "Alice" -o alice_chat.txt

# Incremental new messages (since last check)
wxq new-messages

# Favorites
wxq favorites
```

### 3. Output format

All commands return JSON by default; pass `--format text` for human-readable output:

```bash
wxq sessions --format json
wxq history "Alice" --format text --limit 100
```

Exit codes: `1` = target not found / no data, `2` = invalid arguments, `3` = decryption failure.

## MCP Server

The MCP server exposes WeChat data as read-only tools, allowing AI agents to query your messages
and contacts.

### Setup with Claude Desktop

Add to your Claude Desktop `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "wxq": {
      "command": "wxq-mcp"
    }
  }
}
```

### Available MCP Tools

| Tool | Description |
|------|-------------|
| `get_sessions` | List recent chat sessions with last message preview |
| `get_unread` | List sessions with unread messages |
| `get_contacts` | Search or list contacts |
| `get_contact_detail` | Detailed info for a specific contact |
| `get_chat_history` | Retrieve message history with time/type filtering |
| `search_messages` | Search messages by keyword, optionally within a chat |
| `get_chat_stats` | Message count, type breakdown, top senders, hourly activity |
| `get_group_members` | List members of a group chat |

## Architecture

```
src/wxq/
  cli.py              # Click CLI entry point
  exceptions.py       # Exception hierarchy
  core/
    config.py          # Configuration loading
    context.py         # AppContext — shared application state
    crypto.py          # SQLCipher 4 decryption (AES-256-CBC + HMAC-SHA512)
    db_cache.py        # Mtime-based decrypted DB cache
    contacts.py        # ContactStore — name resolution and contact queries
    key_utils.py       # Key file parsing and path safety
  keys/
    common.py          # Cross-platform key scanning interface
    scanner_macos.py   # macOS: task_for_pid / mach_vm_read
    scanner_windows.py # Windows: kernel32.ReadProcessMemory
    scanner_linux.py   # Linux: /proc/pid/mem
  models/
    contact.py         # Contact, ContactDetail, GroupInfo dataclasses
    message.py         # Message, ChatContext, ChatStats, type enums
    config.py          # Configuration model
    session.py         # Session model
    keys.py            # Key metadata model
  services/
    message_service.py # Message querying, pagination, stats aggregation
    message_parser.py  # zstd decompression, type splitting, content parsing
  commands/            # CLI subcommand implementations
  mcp/
    server.py          # MCP server with 8 tools
  output/
    formatter.py       # JSON / text output formatting
```

The CLI and the MCP server are two thin front ends over the same service layer
(`services/message_service.py`), so both surfaces always expose identical behavior.

## How Decryption Works

WeChat stores its data in SQLCipher 4 encrypted SQLite databases. The decryption process:

1. **Key extraction** — The encryption key is stored in WeChat's process memory. `wxq init` scans
   the process to find and verify the 32-byte key using HMAC-SHA512 page authentication.

2. **Page-level decryption** — Each 4096-byte page is decrypted independently with AES-256-CBC.
   The first 16 bytes of the 80-byte reserve area are the IV; the remaining 64 bytes are the
   HMAC-SHA512 signature.

3. **WAL handling** — Write-Ahead Log frames are decrypted and patched back into the main database
   for a consistent view.

4. **Caching** — Decrypted databases are cached in a temp directory, keyed by MD5 of the relative
   path. The cache is invalidated when the source file's mtime changes.

## Configuration

State lives in `~/.wxq/` — `config.json`, `all_keys.json`, and `last_check.json`. It is created
automatically by `wxq init`. Set `WXQ_CONFIG` (or pass `--config`) to override the config path.

> **Upgrading from `wechat-query`?** If `~/.wechat-cli/config.json` exists and `~/.wxq/` does not,
> `wxq` keeps reading the old location, so existing installs work without re-running `init`.
> Nothing is moved or deleted. The `WECHAT_QUERY_CONFIG` environment variable is still honored as
> a fallback. To migrate for real, just `mv ~/.wechat-cli ~/.wxq`.

## Testing

This project uses a `src/` layout, so an editable install is required before the tests can import
the package:

```bash
pip install -e ".[dev,mcp]"     # required first — a bare pytest will fail to import wxq
pytest                          # run the suite
pytest tests/test_crypto.py     # a single file
pytest tests/test_crypto.py::TestFullDecrypt::test_single_page_roundtrip   # a single test
pytest --cov=wxq                # with coverage
mypy src/wxq --strict           # static type check (passes clean)
```

248 tests covering decryption (including corrupt/truncated/wrong-key cases), the SQLCipher
key-length guard, contacts, XML app-message and media parsing, the XXE safety guard,
SQL-injection-safe table handling, path-traversal rejection, the DB cache's mtime invalidation,
config loading, every CLI command end-to-end, and the MCP server handlers. The type checker runs
in `--strict` mode with no errors.

Uncovered code is concentrated in the platform-specific process-memory scanners (`keys/`), which
require a live WeChat process and OS-level memory access and so cannot run in CI.

CI runs the suite on Python 3.10–3.13 (Linux) plus one job each on macOS and Windows, then
`mypy --strict`, then a wheel build that asserts `py.typed` and the `bin/` scanner are packaged.

## License

MIT

---

<sub>Keywords: 微信 聊天记录 导出 查询 解密 · WeChat chat history export, WeChat database decrypt,
SQLCipher, MCP server, chatlog, wxq</sub>
