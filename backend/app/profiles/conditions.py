"""Evaluator for `when` expressions on conditional requirements.

A deliberately tiny grammar rather than `eval`. Profiles are data, and data
that arrives over an API and gets executed is a remote-code-execution hole —
this is a client-editable file in a system that also holds banking details.
The grammar below covers every condition the six category profiles need and
nothing else. If a future profile needs more, extend the grammar explicitly.

Supported clauses::

    country == 'IN'                 field equals a literal
    entity_type != 'individual'     field does not equal a literal
    category in ['goods', 'other']  field is one of a list
    tax_id is present               field has a non-empty value
    website is absent               field is missing or blank
    contract_value > 100000         numeric comparison (> >= < <=)

Clauses combine with ``and`` / ``or`` (no parentheses, ``and`` binds tighter,
which matches how people read these left to right).

Field lookup walks the submission by dotted path (``bank.account_name``) and
falls back to ``custom_fields``, so a profile can branch on its own fields.

An expression that cannot be parsed or references an unknown field returns
False rather than raising: a requirement whose applicability we cannot
establish is one we must not chase the vendor for.
"""

from __future__ import annotations

import logging
import re
from typing import Any

log = logging.getLogger("vo.conditions")

_CLAUSE = re.compile(
    r"""^\s*
        (?P<field>[A-Za-z_][A-Za-z0-9_.]*)\s*
        (?P<op>==|!=|>=|<=|>|<|\bnot\ in\b|\bin\b|\bis\b)\s*
        (?P<value>.+?)
    \s*$""",
    re.VERBOSE,
)


def field_value(data: dict[str, Any], path: str) -> Any:
    """Walk a dotted path, falling back to custom_fields."""
    cur: Any = data
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            custom = data.get("custom_fields") or {}
            if isinstance(custom, dict) and path in custom:
                return custom[path]
            return None
    return cur


def _literal(raw: str) -> Any:
    raw = raw.strip()
    if raw.startswith("[") and raw.endswith("]"):
        inner = raw[1:-1].strip()
        if not inner:
            return []
        return [_literal(p) for p in _split_list(inner)]
    if (raw.startswith("'") and raw.endswith("'")) or \
       (raw.startswith('"') and raw.endswith('"')):
        return raw[1:-1]
    low = raw.lower()
    if low in ("true", "false"):
        return low == "true"
    if low in ("none", "null"):
        return None
    try:
        return float(raw) if "." in raw else int(raw)
    except ValueError:
        return raw


def _split_list(inner: str) -> list[str]:
    """Split on commas that are not inside quotes."""
    out, buf, quote = [], [], ""
    for ch in inner:
        if quote:
            if ch == quote:
                quote = ""
            buf.append(ch)
        elif ch in "'\"":
            quote = ch
            buf.append(ch)
        elif ch == ",":
            out.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    if buf:
        out.append("".join(buf))
    return [p for p in (s.strip() for s in out) if p]


def _blank(v: Any) -> bool:
    return v is None or (isinstance(v, str) and not v.strip()) or v == []


def _eval_clause(clause: str, data: dict[str, Any]) -> bool:
    m = _CLAUSE.match(clause)
    if not m:
        log.debug("unparseable condition clause: %r", clause)
        return False

    field, op, raw = m.group("field"), m.group("op").strip(), m.group("value")
    actual = field_value(data, field)

    if op == "is":
        want = raw.strip().lower()
        if want == "present":
            return not _blank(actual)
        if want == "absent":
            return _blank(actual)
        return _norm(actual) == _norm(_literal(raw))

    expected = _literal(raw)

    if op in ("in", "not in"):
        seq = expected if isinstance(expected, list) else [expected]
        hit = any(_norm(actual) == _norm(x) for x in seq)
        return hit if op == "in" else not hit

    if op == "==":
        return _norm(actual) == _norm(expected)
    if op == "!=":
        return _norm(actual) != _norm(expected)

    # Numeric comparisons. A non-numeric value can never satisfy one.
    try:
        a, b = float(actual), float(expected)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False
    return {">": a > b, ">=": a >= b, "<": a < b, "<=": a <= b}[op]


def _norm(v: Any) -> Any:
    return v.strip().lower() if isinstance(v, str) else v


def evaluate(expression: str | None, data: dict[str, Any]) -> bool:
    """True if `expression` holds for `data`. Unparseable/empty -> False."""
    if not expression or not expression.strip():
        return False
    try:
        # `and` binds tighter than `or`: split on or, all-of within each group.
        for or_group in re.split(r"\bor\b", expression):
            clauses = re.split(r"\band\b", or_group)
            if clauses and all(_eval_clause(c, data) for c in clauses):
                return True
        return False
    except Exception as exc:                      # never let a profile crash a run
        log.warning("condition %r failed to evaluate: %s", expression, exc)
        return False


def explain(expression: str | None) -> str:
    """Human-readable rendering of a condition, for the ops report."""
    if not expression:
        return ""
    out = expression
    for pat, rep in (
        (r"\bis present\b", "is provided"),
        (r"\bis absent\b", "is not provided"),
        (r"==", "is"),
        (r"!=", "is not"),
        (r"\bin\b", "is one of"),
        (r"_", " "),
    ):
        out = re.sub(pat, rep, out)
    return out.replace("'", "")
