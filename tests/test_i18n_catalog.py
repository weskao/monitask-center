"""i18n guard rails: no CJK in code, every t("key") exists with matching placeholders,
no orphan catalog keys, language dirs merge cleanly, fallback chain, no control strings."""

from __future__ import annotations

import ast
import json
import re
import string
from pathlib import Path

import pytest

from monitask_center import i18n
from monitask_center.cli import COMMANDS
from monitask_center.tui import menu

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
PY_FILES = sorted(p for p in SRC.rglob("*.py") if "locales" not in p.parts)
SCAN_FILES = [*PY_FILES, ROOT / "Makefile"]

CJK_RE = re.compile("[一-鿿　-ヿ＀-￯]")
FORBIDDEN_RE = re.compile(
    r"press_play_pause|ensure_window|PAUSE_KEYWORDS|FORBID_KEYWORDS"
    r"|schedule\.json|auto_start|auto_pause_at_end")

# Keys built as f"<prefix>{x}": the prefix must exist in the code AND each suffix must be
# in the catalog. A new dynamic prefix fails the test until it is listed here on purpose.
DYNAMIC = {
    "cli.help.": {*COMMANDS, "config"},
    "launchd.sync.": {"not_installed", "unchanged", "reinstalled"},  # launchd.sync() states
    "tui.error.": {"hours", "interval", "lunch", "overtime", "activity_alert"},      # config.validate() ValueError texts
    "tui.error.vacation_": {"date", "range", "time"},                # config.parse_vacation() ValueError texts
    "tui.row.": {row.key for row in menu.ROWS},
    "tui.help.": {row.key for row in menu.ROWS},
}

# Catalog keys that no code references yet. Each needs a justification; remove when used.
ALLOWED_ORPHANS = {
    "check.start": "reserved check-start log line; monitask.py logs check.decision instead",
    "check.error": "reserved failure text for the engine entry point",
    "common.yes": "shared yes/no labels for future prompts; the TUI/installer parse y/n directly",
    "common.no": "shared yes/no labels for future prompts; the TUI/installer parse y/n directly",
}


def _catalog() -> dict[str, str]:
    return i18n.catalog(i18n.DEFAULT_LANGUAGE)


def _placeholders(text: str) -> set[str]:
    return {name.split(".")[0].split("[")[0] for _, name, _, _ in string.Formatter().parse(text) if name}


def _is_t_call(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call) or not node.args:
        return False
    f = node.func
    return (isinstance(f, ast.Name) and f.id == "t") or (
        isinstance(f, ast.Attribute) and f.attr == "t" and isinstance(f.value, ast.Name) and f.value.id == "i18n")


def _literals(arg: ast.AST) -> list[str] | None:
    """Possible literal keys of a t() first argument, or None when not statically known."""
    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
        return [arg.value]
    if isinstance(arg, ast.IfExp):
        a, b = _literals(arg.body), _literals(arg.orelse)
        return a + b if a is not None and b is not None else None
    return None


def _scan_calls():
    """-> (literal calls [(file, line, key, kwargs|None)], dynamic prefixes, unresolved calls)."""
    literal, prefixes, unresolved = [], [], []
    for path in PY_FILES:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not _is_t_call(node):
                continue
            kwargs = None if any(k.arg is None for k in node.keywords) else {k.arg for k in node.keywords}
            keys = _literals(node.args[0])
            where = f"{path.relative_to(ROOT)}:{node.lineno}"
            if keys is not None:
                literal += [(where, k, kwargs) for k in keys]
            elif isinstance(node.args[0], ast.JoinedStr):
                head = node.args[0].values[0] if node.args[0].values else None
                prefix = head.value if isinstance(head, ast.Constant) else ""
                prefixes.append((where, prefix))
            else:
                unresolved.append(where)  # t(variable): keys must appear as string constants elsewhere
    return literal, prefixes, unresolved


def _string_constants() -> set[str]:
    out: set[str] = set()
    for path in PY_FILES:
        out |= {n.value for n in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
                if isinstance(n, ast.Constant) and isinstance(n.value, str)}
    return out


def test_no_cjk_or_fullwidth_in_code():
    bad = []
    for path in SCAN_FILES:
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if CJK_RE.search(line):
                bad.append(f"{path.relative_to(ROOT)}:{n}: {line.strip()[:60]}")
    assert not bad, "CJK/fullwidth text belongs in locales/<lang>/*.json:\n" + "\n".join(bad)


def test_no_forbidden_control_strings():
    files = [p for p in SRC.rglob("*") if p.is_file() and p.suffix in {".py", ".json"}]
    bad = [f"{p.relative_to(ROOT)}:{n}: {line.strip()}" for p in files
           for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
           if FORBIDDEN_RE.search(line)]
    assert not bad, "read-only product: no start/stop/click controls or shift schedule:\n" + "\n".join(bad)


def test_every_literal_key_exists_with_matching_placeholders():
    catalog = _catalog()
    literal, _, _ = _scan_calls()
    assert literal, "scanner found no t() calls: the AST walk is broken"
    problems = []
    for where, key, kwargs in literal:
        if key not in catalog:
            problems.append(f"{where}: missing key {key!r}")
        elif kwargs is not None:
            need, given = _placeholders(catalog[key]), kwargs - {"language"}
            if need != given:
                problems.append(f"{where}: {key!r} placeholders {sorted(need)} != kwargs {sorted(given)}")
    assert not problems, "\n".join(problems)


def test_dynamic_keys_are_registered_and_complete():
    catalog = _catalog()
    _, prefixes, _ = _scan_calls()
    assert {p for _, p in prefixes} <= DYNAMIC.keys(), (
        f"new dynamic t(f'...') prefix: register it in DYNAMIC: {sorted({p for _, p in prefixes} - DYNAMIC.keys())}")
    for prefix, suffixes in DYNAMIC.items():
        missing = [f"{prefix}{s}" for s in suffixes if f"{prefix}{s}" not in catalog]
        assert not missing, f"missing dynamic keys: {missing}"
        assert any(p == prefix for _, p in prefixes), f"DYNAMIC prefix {prefix!r} is no longer used in code"


def test_variable_keys_are_string_constants_in_the_catalog():
    """t(variable) calls: every candidate key is a plain string constant somewhere in the code."""
    _, _, unresolved = _scan_calls()
    constants = _string_constants()
    keyish = {c for c in constants if c in _catalog()}
    assert unresolved, "expected t(variable) call sites (engine.py key, install._say, _commit done)"
    assert {"check.remind_paused", "check.remind_not_running", "install.step.runtime", "tui.saved",
            "tui.reset_done"} <= keyish


def test_no_orphan_catalog_keys():
    catalog = _catalog()
    literal, _, _ = _scan_calls()
    used = {k for _, k, _ in literal} | (_string_constants() & catalog.keys())
    used |= {f"{p}{s}" for p, suffixes in DYNAMIC.items() for s in suffixes}
    orphans = sorted(catalog.keys() - used - ALLOWED_ORPHANS.keys())
    assert not orphans, f"catalog keys no code uses (delete them or justify in ALLOWED_ORPHANS): {orphans}"
    stale = sorted(k for k in ALLOWED_ORPHANS if k not in catalog or k in used)
    assert not stale, f"ALLOWED_ORPHANS entries that are gone or now used: {stale}"
    assert all(ALLOWED_ORPHANS.values()), "every allowed orphan needs a justification"


@pytest.mark.parametrize("language", i18n.available())
def test_language_dir_merges_without_duplicates(language):
    seen: dict[str, str] = {}
    for path in sorted((i18n.LOCALES_DIR / language).glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        assert isinstance(data, dict) and all(isinstance(k, str) and isinstance(v, str) for k, v in data.items()), path
        for key in data:
            assert key not in seen, f"{key!r} defined in both {seen[key]} and {path.name}"
            seen[key] = path.name
    assert i18n.catalog(language) == {k: json.loads((i18n.LOCALES_DIR / language / f).read_text("utf-8"))[k]
                                      for k, f in seen.items()}


@pytest.mark.parametrize("language", [lang for lang in i18n.available() if lang != i18n.DEFAULT_LANGUAGE])
def test_other_languages_follow_the_default_catalog(language):
    base, other = _catalog(), i18n.catalog(language)
    assert other.keys() <= base.keys(), f"keys not in {i18n.DEFAULT_LANGUAGE}: {sorted(other.keys() - base.keys())}"
    assert {k for k in other if _placeholders(other[k]) != _placeholders(base[k])} == set()


def test_duplicate_key_across_files_raises(tmp_path, monkeypatch):
    (tmp_path / "xx").mkdir()
    (tmp_path / "xx/a.json").write_text('{"k": "1"}', encoding="utf-8")
    (tmp_path / "xx/b.json").write_text('{"k": "2"}', encoding="utf-8")
    monkeypatch.setattr(i18n, "LOCALES_DIR", tmp_path)
    i18n.catalog.cache_clear()
    try:
        with pytest.raises(ValueError, match="duplicate"):
            i18n.catalog("xx")
    finally:
        i18n.catalog.cache_clear()


def test_fallback_chain_language_then_default_then_key(tmp_path, monkeypatch):
    for lang, data in (("zh-TW", {"k.a": "A-default", "k.b": "B-default"}), ("xx", {"k.a": "A-xx"})):
        (tmp_path / lang).mkdir()
        (tmp_path / lang / "m.json").write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(i18n, "LOCALES_DIR", tmp_path)
    i18n.catalog.cache_clear()
    try:
        assert i18n.t("k.a", "xx") == "A-xx"              # own language first
        assert i18n.t("k.b", "xx") == "B-default"         # then zh-TW
        assert i18n.t("k.c", "xx") == "k.c"               # then the key itself
        assert i18n.t("k.a", "no-such-lang") == "A-default"
    finally:
        i18n.catalog.cache_clear()
