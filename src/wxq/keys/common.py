"""Cross-platform key scanning shared logic.

HMAC verification, DB file collection, hex pattern matching.
"""

from __future__ import annotations

import hashlib
import hmac as hmac_mod
import json
import os
import struct
from typing import Any, Callable

from ..models.keys import DBFileInfo

PAGE_SZ = 4096
KEY_SZ = 32
SALT_SZ = 16


def verify_enc_key(enc_key: bytes, db_page1: bytes) -> bool:
    """Verify an encryption key against page 1 using HMAC-SHA512.

    The key derivation is:
    1. XOR the 16-byte salt with 0x3A to get mac_salt
    2. PBKDF2-HMAC-SHA512(enc_key, mac_salt, iterations=2) → 32-byte mac_key
    3. HMAC-SHA512(mac_key, data + page_number) must match stored HMAC
    """
    if len(db_page1) < PAGE_SZ:
        return False

    salt = db_page1[:SALT_SZ]
    mac_salt = bytes(b ^ 0x3A for b in salt)
    mac_key = hashlib.pbkdf2_hmac("sha512", enc_key, mac_salt, 2, dklen=KEY_SZ)
    hmac_data = db_page1[SALT_SZ : PAGE_SZ - 80 + 16]
    stored_hmac = db_page1[PAGE_SZ - 64 : PAGE_SZ]
    hm = hmac_mod.new(mac_key, hmac_data, hashlib.sha512)
    hm.update(struct.pack("<I", 1))
    return hm.digest() == stored_hmac


def collect_db_files(db_dir: str) -> tuple[list[DBFileInfo], dict[str, list[str]]]:
    """Walk db_dir and collect all .db files with their salts.

    Returns:
        (db_files, salt_to_dbs):
            db_files: list of DBFileInfo
            salt_to_dbs: {salt_hex: [rel_path, ...]}
    """
    db_files: list[DBFileInfo] = []
    salt_to_dbs: dict[str, list[str]] = {}

    for root, dirs, files in os.walk(db_dir):
        for name in files:
            if not name.endswith(".db") or name.endswith("-wal") or name.endswith("-shm"):
                continue
            path = os.path.join(root, name)
            try:
                size = os.path.getsize(path)
            except OSError:
                continue
            if size < PAGE_SZ:
                continue
            try:
                with open(path, "rb") as f:
                    page1 = f.read(PAGE_SZ)
            except OSError:
                continue
            rel = os.path.relpath(path, db_dir)
            salt = page1[:SALT_SZ].hex()
            db_files.append(DBFileInfo(
                rel_path=rel,
                abs_path=path,
                size=size,
                salt_hex=salt,
                page1=page1,
            ))
            salt_to_dbs.setdefault(salt, []).append(rel)

    return db_files, salt_to_dbs


def scan_memory_for_keys(
    data: bytes,
    hex_re: Any,  # compiled regex
    db_files: list[DBFileInfo],
    salt_to_dbs: dict[str, list[str]],
    key_map: dict[str, str],
    remaining_salts: set[str],
    base_addr: int,
    pid: int,
    print_fn: Callable[[str], Any],
) -> int:
    """Scan a memory chunk for hex key patterns and validate matches.

    FIX: Defensively handles non-ASCII bytes in regex matches
    (original code assumed .decode() would always succeed).

    Returns:
        Number of hex pattern matches found.
    """
    matches = 0
    for m in hex_re.finditer(data):
        raw_group = m.group(1)
        # FIX: Defensively decode — skip non-ASCII matches
        try:
            hex_str = raw_group.decode("ascii")
        except (UnicodeDecodeError, AttributeError):
            continue

        addr = base_addr + m.start()
        matches += 1
        hex_len = len(hex_str)

        if hex_len == 96:
            # 64-char key + 32-char salt
            enc_key_hex = hex_str[:64]
            salt_hex = hex_str[64:]
            if salt_hex in remaining_salts:
                try:
                    enc_key = bytes.fromhex(enc_key_hex)
                except ValueError:
                    continue
                for db_file in db_files:
                    if db_file.salt_hex == salt_hex and verify_enc_key(enc_key, db_file.page1):
                        key_map[salt_hex] = enc_key_hex
                        remaining_salts.discard(salt_hex)
                        dbs = salt_to_dbs[salt_hex]
                        print_fn(f"\n  [FOUND] salt={salt_hex}")
                        print_fn(f"    enc_key={enc_key_hex}")
                        print_fn(f"    PID={pid} 地址: 0x{addr:016X}")
                        print_fn(f"    数据库: {', '.join(dbs)}")
                        break

        elif hex_len == 64:
            # Key only — try against all remaining salts
            if not remaining_salts:
                continue
            enc_key_hex = hex_str
            try:
                enc_key = bytes.fromhex(enc_key_hex)
            except ValueError:
                continue
            for db_file in db_files:
                if db_file.salt_hex in remaining_salts and verify_enc_key(enc_key, db_file.page1):
                    key_map[db_file.salt_hex] = enc_key_hex
                    remaining_salts.discard(db_file.salt_hex)
                    dbs = salt_to_dbs[db_file.salt_hex]
                    print_fn(f"\n  [FOUND] salt={db_file.salt_hex}")
                    print_fn(f"    enc_key={enc_key_hex}")
                    print_fn(f"    PID={pid} 地址: 0x{addr:016X}")
                    print_fn(f"    数据库: {', '.join(dbs)}")
                    break

        elif hex_len > 96 and hex_len % 2 == 0:
            # Long pattern — key at start, salt at end
            enc_key_hex = hex_str[:64]
            salt_hex = hex_str[-32:]
            if salt_hex in remaining_salts:
                try:
                    enc_key = bytes.fromhex(enc_key_hex)
                except ValueError:
                    continue
                for db_file in db_files:
                    if db_file.salt_hex == salt_hex and verify_enc_key(enc_key, db_file.page1):
                        key_map[salt_hex] = enc_key_hex
                        remaining_salts.discard(salt_hex)
                        dbs = salt_to_dbs[salt_hex]
                        print_fn(f"\n  [FOUND] salt={salt_hex} (long hex {hex_len})")
                        print_fn(f"    enc_key={enc_key_hex}")
                        print_fn(f"    PID={pid} 地址: 0x{addr:016X}")
                        print_fn(f"    数据库: {', '.join(dbs)}")
                        break

    return matches


def cross_verify_keys(
    db_files: list[DBFileInfo],
    salt_to_dbs: dict[str, list[str]],
    key_map: dict[str, str],
    print_fn: Callable[[str], Any],
) -> None:
    """Try known keys against unmatched salts (cross-verification)."""
    missing_salts = set(salt_to_dbs.keys()) - set(key_map.keys())
    if not missing_salts or not key_map:
        return
    print_fn(f"\n还有 {len(missing_salts)} 个 salt 未匹配，尝试交叉验证...")
    for salt_hex in list(missing_salts):
        for db_file in db_files:
            if db_file.salt_hex == salt_hex:
                for known_salt, known_key_hex in key_map.items():
                    try:
                        enc_key = bytes.fromhex(known_key_hex)
                    except ValueError:
                        continue
                    if verify_enc_key(enc_key, db_file.page1):
                        key_map[salt_hex] = known_key_hex
                        print_fn(
                            f"  [CROSS] salt={salt_hex} 可用 key from salt={known_salt}"
                        )
                        missing_salts.discard(salt_hex)
                break


def save_results(
    db_files: list[DBFileInfo],
    salt_to_dbs: dict[str, list[str]],
    key_map: dict[str, str],
    output_path: str,
    print_fn: Callable[[str], Any],
) -> dict[str, str]:
    """Save key extraction results to JSON.

    Returns:
        The key_map for further use.

    Raises:
        RuntimeError: If no keys were extracted.
    """
    print_fn(f"\n{'=' * 60}")
    print_fn(f"结果: {len(key_map)}/{len(salt_to_dbs)} salts 找到密钥")

    result: dict[str, dict[str, Any]] = {}
    for db_file in db_files:
        if db_file.salt_hex in key_map:
            result[db_file.rel_path] = {
                "enc_key": key_map[db_file.salt_hex],
                "salt": db_file.salt_hex,
                "size_mb": round(db_file.size / 1024 / 1024, 1),
            }
            print_fn(f"  OK: {db_file.rel_path} ({db_file.size / 1024 / 1024:.1f}MB)")
        else:
            print_fn(f"  MISSING: {db_file.rel_path} (salt={db_file.salt_hex})")

    if not result:
        print_fn("\n[!] 未提取到任何密钥")
        raise RuntimeError("未能从任何微信进程中提取到密钥")

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    print_fn(f"\n密钥保存到: {output_path}")

    missing = [
        db_file.rel_path
        for db_file in db_files
        if db_file.salt_hex not in key_map
    ]
    if missing:
        print_fn("\n未找到密钥的数据库:")
        for rel in missing:
            print_fn(f"  {rel}")

    return key_map
