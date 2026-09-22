"""Key metadata utilities — path normalization and lookup."""

from __future__ import annotations

import os
import re


def strip_key_metadata(raw_keys: dict[str, dict[str, object]]) -> dict[str, dict[str, object]]:
    """Remove non-essential metadata from keys dict, keeping enc_key and salt."""
    cleaned: dict[str, dict[str, object]] = {}
    for path, info in raw_keys.items():
        if isinstance(info, dict) and "enc_key" in info:
            cleaned[path] = info
    return cleaned


def key_path_variants(key_path: str) -> list[str]:
    """Generate normalized path variants for cross-platform key matching.

    A key stored on Windows may use backslashes; the DB dir on macOS uses
    forward slashes. This generates both variants for matching.
    """
    norm = key_path.replace("\\", "/")
    variants = [norm]
    if "/" in norm:
        variants.append(norm.replace("/", os.sep))
    if "\\" in key_path and key_path not in variants:
        variants.append(key_path)
    return variants


def get_key_info(
    all_keys: dict[str, dict[str, object]],
    rel_key: str,
) -> dict[str, object] | None:
    """Look up key info for a database by its relative path.

    Uses path variants to handle cross-platform separators.
    Includes a path traversal safety check.
    """
    # Safety: reject path traversal
    normalized = os.path.normpath(rel_key)
    if normalized.startswith("..") or os.path.isabs(normalized):
        return None

    # Direct match
    if rel_key in all_keys:
        return all_keys[rel_key]

    # Try variants
    for variant in key_path_variants(rel_key):
        if variant in all_keys:
            return all_keys[variant]

    # Try matching against stored key variants
    for stored_key, info in all_keys.items():
        stored_variants = key_path_variants(stored_key)
        for variant in key_path_variants(rel_key):
            if variant in stored_variants:
                return info

    return None
