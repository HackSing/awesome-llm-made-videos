"""CLI action 参数边界：拒绝当前 action 不使用的 flag。"""

from __future__ import annotations

import argparse
from typing import Sequence


_UNIVERSAL_FLAGS = frozenset({"--target", "--json"})
_ACTION_FLAGS: dict[tuple[str, str | None], frozenset[str]] = {
    ("knowledge", "create"): frozenset({"--input", "--output"}),
    ("knowledge", "update"): frozenset({"--input", "--knowledge"}),
    ("knowledge", "query"): frozenset({"--query", "--scope", "--limit", "--max-chars"}),
    ("knowledge", "show"): frozenset({"--knowledge"}),
    ("knowledge", "settle"): frozenset({"--knowledge", "--status", "--replacement"}),
    ("knowledge", "check"): frozenset(),
    ("plan", "select"): frozenset({
        "--level", "--profile", "--secondary-profile", "--complexity", "--surface",
        "--cross-module", "--high-risk", "--user-requested-plan", "--task", "--query",
    }),
    ("plan", "create"): frozenset({
        "--selection", "--content", "--output", "--dry-run", "--level", "--profile",
        "--secondary-profile", "--complexity", "--surface", "--cross-module",
        "--high-risk", "--user-requested-plan",
    }),
    ("plan", "show"): frozenset({"--plan"}),
    ("plan", "list"): frozenset(),
    ("plan", "amend"): frozenset({"--plan", "--content", "--patch", "--reason", "--dry-run"}),
    ("plan", "settle"): frozenset({"--plan", "--status", "--replacement", "--governance-input"}),
    ("plan", "check"): frozenset({"--strict", "--fast", "--verbose"}),
    ("acceptance", "create"): frozenset({"--input", "--output", "--dry-run"}),
    ("acceptance", "amend"): frozenset({"--acceptance", "--input", "--reason", "--dry-run"}),
    ("acceptance", "record"): frozenset({"--input", "--acceptance", "--reaccept", "--user-confirmed", "--dry-run"}),
    ("acceptance", "show"): frozenset({"--acceptance"}),
    ("acceptance", "list"): frozenset(),
    ("acceptance", "settle"): frozenset({
        "--acceptance", "--status", "--replacement", "--input", "--user-confirmed", "--dry-run",
    }),
    ("acceptance", "check"): frozenset(),
    ("adr", "create"): frozenset({"--input", "--output"}),
    ("adr", "amend"): frozenset({"--adr", "--input", "--reason"}),
    ("adr", "show"): frozenset({"--adr"}),
    ("adr", "settle"): frozenset({"--adr", "--status", "--replacement"}),
    ("adr", "check"): frozenset(),
    ("project", "init"): frozenset({"--apply", "--source"}),
    ("project", "upgrade"): frozenset({"--apply", "--source"}),
    ("project", "check"): frozenset({"--source"}),
    ("project", "diff"): frozenset({"--source"}),
    ("project", "uninstall"): frozenset({"--apply", "--purge-runtime"}),
    ("release", "sync"): frozenset({"--apply", "--target-version", "--strict"}),
    ("structure", "check"): frozenset({"--base"}),
    ("structure", "report"): frozenset(),
    ("usage", "report"): frozenset({"--days"}),
    ("view", None): frozenset({"--open"}),
    ("report", "check"): frozenset({"--path"}),
    ("assets-check", None): frozenset({"--strict", "--fast", "--verbose"}),
    ("self-test", None): frozenset(),
}


def validate_action_flags(args: argparse.Namespace, argv: Sequence[str], error_type) -> None:
    """拒绝 argparse 接受但当前 action 不使用的 flag，避免静默 no-op。"""
    key = (args.command, getattr(args, "action", None))
    allowed = _ACTION_FLAGS.get(key)
    if allowed is None:
        return
    allowed = allowed | _UNIVERSAL_FLAGS
    passed = {
        token.split("=", 1)[0]
        for token in argv
        if token.startswith("--") and token != "--"
    }
    for flag in sorted(passed - allowed):
        raise error_type(
            f"{args.command} {key[1] or ''} 不支持 {flag}",
            code="unsupported_flag",
            expected=f"当前 action 可用参数：{', '.join(sorted(allowed))}",
            next_action="去掉该参数，或改用支持它的 action",
        )
