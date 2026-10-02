"""Key-based translations: every user-facing string lives in locales/<lang>/*.json.

A language is a directory; every json file in it is a flat str->str catalog and they
are merged (a key defined in two files raises ValueError, so owners cannot clash).

Lookup order: active language -> DEFAULT_LANGUAGE -> the key itself (never blank,
never a crash). Placeholders use str.format names: t("x.y", hour=10).
Adding a language = dropping another locales/<lang>/ directory with the same keys.
"""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path

LOCALES_DIR = Path(__file__).resolve().parent / "locales"
DEFAULT_LANGUAGE = "zh-TW"

_language = DEFAULT_LANGUAGE


def available() -> list[str]:
    return sorted(p.name for p in LOCALES_DIR.iterdir() if p.is_dir() and not p.name.startswith((".", "_")))


@cache
def catalog(language: str) -> dict[str, str]:
    merged: dict[str, str] = {}
    for path in sorted((LOCALES_DIR / language).glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        if dup := merged.keys() & data.keys():
            raise ValueError(f"duplicate i18n key(s) {sorted(dup)} in {path}")
        merged |= data
    return merged


def set_language(language: str) -> None:
    global _language
    _language = language if language in available() else DEFAULT_LANGUAGE


def current() -> str:
    return _language


def t(key: str, language: str | None = None, **kwargs: object) -> str:
    text = catalog(language or _language).get(key) or catalog(DEFAULT_LANGUAGE).get(key) or key
    if not kwargs:
        return text
    try:
        return text.format(**kwargs)
    except (KeyError, IndexError, ValueError):
        return text
