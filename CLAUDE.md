# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

`wxq` reads WeChat's local SQLCipher-encrypted SQLite databases, decrypts them in
place-independent temp copies, and exposes messages/contacts/sessions/stats through both a Click
CLI (`wxq`) and an MCP stdio server (`wxq-mcp`).

## Commands

The package uses a `src/` layout with no `pythonpath` set in `pyproject.toml`, so **tests only run
after an editable install** — a bare `pytest` in a fresh environment fails at `conftest.py` with
`ModuleNotFoundError: No module named 'wxq'`.

```bash
pip install -e ".[dev,mcp]"        # required before running anything
pytest                             # full suite (248 tests)
pytest tests/test_crypto.py        # one file
pytest tests/test_crypto.py::TestFullDecrypt::test_single_page_roundtrip  # one test
pytest -k "wal or cache"           # by name
pytest --cov=wxq          # with coverage
mypy src/wxq --strict     # must stay clean — strict mode is enforced in pyproject
python -m build                    # wheel + sdist into dist/ (needs `pip install build`)
```

`pytest` defaults to `-v --tb=short` via `[tool.pytest.ini_options]`.

The `keys/` scanners are deliberately uncovered: they need a live WeChat process and OS-level
memory access, so they cannot run in CI. Don't chase coverage there.

## Architecture

### Dependency spine

Everything flows through one object:

```
AppContext (core/context.py)
  ├─ AppConfig      (core/config.py)   — per-platform db_dir auto-detection
  ├─ all_keys       (core/key_utils.py) — {rel_db_path: {enc_key, salt}}
  ├─ DBCache        (core/db_cache.py)  — rel path → decrypted temp file
  ├─ ContactStore   (core/contacts.py)  — username ↔ display name
  └─ msg_db_keys                        — lazily discovered message_N.db keys
```

`cli.py` builds an `AppContext` and stashes it on `ctx.obj`; every subcommand reads it via
`@click.pass_context`. `mcp/server.py` builds the same object as a module-level singleton
(`_get_app()`). **Both front ends call the identical functions in `services/message_service.py`** —
never put query logic in a command or an MCP handler; it belongs in the service layer so both
surfaces stay in sync. `init` and `version` are the only commands that skip `AppContext`.

### How a query reaches bytes

1. `ContactStore.resolve_username()` maps a human name to a `wxid_*` / `*@chatroom` username
   (exact username → exact display name, case-insensitive → substring).
2. Message tables are named `Msg_<md5(username)>`, sharded across `message/message_N.db` files.
   `_find_msg_tables_for_user()` probes every message DB and sorts hits by `MAX(create_time)`.
   `ChatContext.db_path` / `.table_name` return the most recent shard.
3. `DBCache.get(rel_key)` decrypts on demand: `core/crypto.py` does SQLCipher 4 page decryption
   (4096-byte pages, 80-byte reserve = 16-byte IV + 64-byte HMAC-SHA512, AES-256-CBC), then
   `decrypt_wal()` patches WAL frames back into the decrypted file for a consistent read.
4. Sender IDs in message rows are rowids into the per-DB `Name2Id` table, not usernames.
   `_resolve_sender_label()` joins that with `ContactStore`; in group chats the content itself is
   prefixed with `sender:\n`, which `parse_message_content()` splits off.
5. `services/message_parser.py` decompresses zstd (`compress_type == 4`) and renders type-49
   app-messages (links, files, quotes, mini-programs) from their XML payload.

### Caching

`DBCache` writes decrypted DBs to `$TMPDIR/wxq_cache/<md5(rel_key)[:12]>.db` and records
source mtimes in `_mtimes.json` in the same directory. That metadata is reloaded on startup, so
**the cache persists across CLI invocations** — a decrypted DB is reused until the source `.db` or
`.db-wal` mtime changes. Tests that touch the cache must point at a temp dir rather than relying on
the class-level `CACHE_DIR`.

### State on disk

`STATE_DIR` is `~/.wxq`, holding `config.json`, `all_keys.json`, and `last_check.json` (the
`new-messages` watermark). `core/config.py` resolves it at import time with a **read-only fallback**:
if `~/.wxq/config.json` is absent but `~/.wechat-cli/config.json` exists (a pre-rename install), the
old directory is used instead. Nothing is moved or deleted — do not turn this into a migration that
writes.

`WXQ_CONFIG` overrides the config path, with `WECHAT_QUERY_CONFIG` still honored as a fallback in
both `cli.py` (Click's `envvar` list) and `mcp/server.py`. The CLI additionally accepts `--config`;
the MCP server reads only the env vars. Sibling paths for keys/decrypted dirs derive from that
file's directory.

## Conventions that matter

- **Security guards are load-bearing; don't remove them when refactoring.** Message table names are
  interpolated into SQL, so `_is_safe_msg_table_name()` gates them against `Msg_[0-9a-f]{32}`;
  `get_key_info()` rejects absolute paths and `..` traversal; `_parse_xml_root()` refuses
  `<!DOCTYPE`/`<!ENTITY` and inputs over 20 KB (XXE); `_validate_key()` rejects any key that isn't
  exactly 32 bytes, so a short key fails loudly instead of decrypting to garbage as AES-128.
- **Exceptions:** everything derives from `WechatQueryError` in `exceptions.py`, grouped by
  Config / Decrypt / KeyScan / Query. Raise the specific subclass. Note `PermissionError_` has a
  trailing underscore to avoid shadowing the builtin.
- **CLI exit codes:** `1` = target not found / no data, `2` = bad arguments (time range,
  pagination), `3` = database decryption failure.
- **Output:** commands return JSON by default and accept `--format text`; go through
  `output/formatter.py` rather than `click.echo`-ing structured data yourself.
- **Language:** user-facing strings in `core/` and `services/` are Chinese; `commands/`, `mcp/`, and
  `exceptions.py` are English. Match whichever file you're editing.
- **Cross-platform key paths:** keys extracted on Windows use `\` separators. Always look keys up
  through `get_key_info()` / `key_path_variants()`, never by raw dict indexing.
- `ContactStore` caches per instance on purpose (replacing module-level globals in an earlier
  version) — don't reintroduce module-level contact caches.
- Broad `except Exception` in `ContactStore` and the table-discovery loops is intentional: a single
  corrupt or partially-written shard should degrade to empty results, not crash the query.

## Adding a command or MCP tool

New CLI command: add `commands/<name>.py` with a Click command, then import and `cli.add_command()`
it in `cli.py` (the imports are at the bottom with `# noqa: E402`). New MCP tool: append a `Tool` to
`TOOLS` and add its `_handle_*` branch in `mcp/server.py`. If the feature involves querying
messages, implement it in `services/message_service.py` first and have both call it.
