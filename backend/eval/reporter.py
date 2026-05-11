from __future__ import annotations

import json
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

from eval.runner import EvalResult


def write_markdown_report(results: list[EvalResult], path: Path | str) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(_render_markdown(results), encoding="utf-8")


def write_json_report(results: list[EvalResult], path: Path | str) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "total": len(results),
        "passed": sum(1 for r in results if r.passed),
        "results": [
            {
                "id": r.entry_id,
                "category": r.category,
                "query": r.query,
                "passed": r.passed,
                "intent_ok": r.intent_ok,
                "ops_ok": r.ops_ok,
                "answer_ok": r.answer_ok,
                "guardrail_ok": r.guardrail_ok,
                "actual_intent": r.actual_intent,
                "actual_answer": r.actual_answer,
                "reasons": r.reasons,
            }
            for r in results
        ],
    }
    p.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _render_markdown(results: list[EvalResult]) -> str:
    by_cat: dict[str, list[EvalResult]] = defaultdict(list)
    for r in results:
        by_cat[r.category].append(r)

    lines: list[str] = []
    lines.append(f"# Eval Report — {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')}")
    lines.append("")

    # Summary table
    lines.append("## Summary")
    lines.append("")
    lines.append("| Category | Pass | Fail | Pass Rate |")
    lines.append("|----------|------|------|-----------|")
    total_pass = 0
    total_fail = 0
    for cat in sorted(by_cat.keys()):
        items = by_cat[cat]
        passed = sum(1 for r in items if r.passed)
        failed = len(items) - passed
        total_pass += passed
        total_fail += failed
        rate = f"{(passed / len(items) * 100):.0f}%" if items else "—"
        lines.append(f"| {cat} | {passed} | {failed} | {rate} |")
    total = total_pass + total_fail
    overall = f"{(total_pass / total * 100):.1f}%" if total else "0%"
    lines.append(f"| **Total** | **{total_pass}** | **{total_fail}** | **{overall}** |")
    lines.append("")

    # Failure detail
    failures = [r for r in results if not r.passed]
    if failures:
        lines.append("## Failures")
        lines.append("")
        for r in failures:
            lines.append(f"### {r.entry_id} — {r.query}")
            lines.append("")
            lines.append(f"**Expected intent:** `{r.expected.get('intent')}`  ")
            lines.append(f"**Actual intent:** `{r.actual_intent}`")
            lines.append("")
            lines.append("**Reasons:**")
            for reason in r.reasons:
                lines.append(f"- {reason}")
            lines.append("")
            lines.append("**Actual answer:**")
            lines.append("")
            lines.append("```")
            lines.append(r.actual_answer)
            lines.append("```")
            lines.append("")
    else:
        lines.append("## Failures")
        lines.append("")
        lines.append("_None — all queries passed._")
        lines.append("")

    return "\n".join(lines) + "\n"
