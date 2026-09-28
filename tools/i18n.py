"""Find the text the code asks to be translated, and what each catalog lacks or gets wrong.

    python tools/i18n.py                  a report for every catalog
    python tools/i18n.py missing <code>   the English still to translate, as JSON

Text is collected from ``tr("...")``, ``N_("...")`` and ``ntr(n, "...", "...")``
calls with plain strings in them (never f-strings: the values go in by name).
A translation must keep the text's ``{names}`` and its markup (``<b>``,
``<br>``...), and counted text needs the language's number of forms.
"""
from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CODE = ROOT / "langmod"
sys.path.insert(0, str(ROOT))

from langmod import i18n  # noqa: E402

FIELD = re.compile(r"\{(\w*)[^{}]*\}")
TAG = re.compile(r"</?([a-zA-Z]+)")


def wanted() -> tuple[dict[str, list[str]], dict[str, list[str]], list[str]]:
    """(text -> where it is asked for, "one|many" -> where, problems in the code)."""
    strings: dict[str, list[str]] = {}
    plurals: dict[str, list[str]] = {}
    problems: list[str] = []
    for path in sorted(CODE.rglob("*.py")):
        tree = ast.parse(path.read_text("utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            f = node.func
            name = f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else ""
            where = f"{path.relative_to(ROOT).as_posix()}:{node.lineno}"
            if name in ("tr", "N_") and node.args:
                a = node.args[0]
                if isinstance(a, ast.Constant) and isinstance(a.value, str):
                    strings.setdefault(a.value, []).append(where)
                elif isinstance(a, ast.JoinedStr):
                    problems.append(f"{where}: an f-string in {name}(): put the values in by name")
            elif name == "ntr" and len(node.args) >= 3:
                one, many = node.args[1], node.args[2]
                if all(isinstance(x, ast.Constant) and isinstance(x.value, str) for x in (one, many)):
                    if "|" in one.value or "|" in many.value:
                        problems.append(f"{where}: counted text cannot have | in it")
                    plurals.setdefault(f"{one.value}|{many.value}", []).append(where)
                else:
                    problems.append(f"{where}: ntr() needs plain strings")
    return strings, plurals, problems


def fields(text: str) -> set[str]:
    return set(FIELD.findall(text))


def tags(text: str) -> list[str]:
    return sorted(t.lower() for t in TAG.findall(text))


def check(code: str) -> dict[str, list]:
    """What one catalog lacks, has spare, or has wrong."""
    strings, plurals, _ = wanted()
    raw = json.loads((i18n.LOCALE / f"{code}.json").read_text("utf-8"))
    have, have_n = raw.get("strings", {}), raw.get("plurals", {})
    out: dict[str, list] = {"missing": [], "spare": [], "wrong": []}
    for en in strings:
        t = have.get(en)
        if not isinstance(t, str) or not t.strip():
            out["missing"].append(en)
            continue
        if fields(t) != fields(en):
            out["wrong"].append((en, f"names {sorted(fields(t))} for {sorted(fields(en))}"))
        if tags(t) != tags(en):
            out["wrong"].append((en, "markup differs"))
        try:
            t.format(**{k: "x" for k in fields(en)}) if fields(en) else None
        except (KeyError, ValueError, IndexError) as exc:
            out["wrong"].append((en, f"braces: {exc}"))
    for key in plurals:
        t = have_n.get(key)
        if not isinstance(t, list) or not all(isinstance(x, str) and x.strip() for x in t):
            out["missing"].append(key)
            continue
        one, many = key.split("|")
        if len(t) != i18n.forms(code):
            out["wrong"].append((key, f"{len(t)} forms, {code} has {i18n.forms(code)}"))
        for form in t:
            if not fields(form) <= fields(one) | fields(many) or "n" not in fields(form) and "n" in fields(many):
                out["wrong"].append((key, f"names in {form!r}"))
            if tags(form) != tags(many):
                out["wrong"].append((key, "markup differs"))
    out["spare"] = [k for k in have if k not in strings] + [k for k in have_n if k not in plurals]
    return out


def main(argv: list[str]) -> int:
    strings, plurals, problems = wanted()
    if argv[:1] == ["missing"]:
        code = argv[1]
        missing = check(code)["missing"]
        print(json.dumps({"strings": {k: "" for k in missing if k in strings},
                          "plurals": {k: [""] * i18n.forms(code) for k in missing if k in plurals}},
                         indent=1, ensure_ascii=False))
        return 0
    print(f"{len(strings)} texts and {len(plurals)} counted texts asked for")
    for p in problems:
        print("  " + p)
    for code in i18n.LANGUAGES:
        if code == "en":
            continue
        if not (i18n.LOCALE / f"{code}.json").is_file():
            print(f"{code}: no catalog")
            continue
        r = check(code)
        print(f"{code}: {len(r['missing'])} missing, {len(r['spare'])} spare, {len(r['wrong'])} wrong")
        for en, why in r["wrong"][:20]:
            print(f"    {why}: {en[:70]!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
