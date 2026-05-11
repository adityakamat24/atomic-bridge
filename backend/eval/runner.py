"""Eval runner. Loads gold queries, runs each through a query callable, scores
the result, and produces a markdown report.

The "query callable" indirection lets tests inject a TestClient + scripted LLM,
and lets a CLI invocation drive the deployed FastAPI directly via httpx.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

QueryFn = Callable[[str, str | None], Awaitable[dict[str, Any]]]


@dataclass
class EvalResult:
    entry_id: str
    category: str
    query: str
    expected: dict[str, Any]
    actual_intent: str
    actual_answer: str
    actual_plan: dict[str, Any] | None
    intent_ok: bool
    ops_ok: bool
    answer_ok: bool
    guardrail_ok: bool
    reasons: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.intent_ok and self.ops_ok and self.answer_ok and self.guardrail_ok


# ---------- Per-entry execution -----------------------------------------


async def run_entry(entry: dict[str, Any], query_fn: QueryFn) -> EvalResult:
    expected = entry["expected"]
    session_id = await _session_for_prior(entry, query_fn)

    try:
        response = await query_fn(entry["query"], session_id)
    except Exception as exc:  # noqa: BLE001
        return EvalResult(
            entry_id=entry["id"],
            category=entry.get("category", ""),
            query=entry["query"],
            expected=expected,
            actual_intent="(error)",
            actual_answer=f"runner error: {exc}",
            actual_plan=None,
            intent_ok=False,
            ops_ok=False,
            answer_ok=False,
            guardrail_ok=False,
            reasons=[f"runner exception: {exc}"],
        )

    actual_intent = str(response.get("intent", ""))
    actual_answer = str(response.get("answer", ""))
    actual_plan = response.get("plan")
    proposal = response.get("write_proposal")

    intent_ok = actual_intent == expected.get("intent", "")
    ops_used = (
        [op["op"] for op in actual_plan["operations"]]
        if isinstance(actual_plan, dict) and actual_plan.get("operations")
        else []
    )
    ops_ok = check_operations(ops_used, expected.get("operations_required") or [])
    answer_ok, answer_reasons = check_answer(actual_answer, expected, proposal)
    guardrail_ok, guardrail_reasons = check_guardrails(response, entry)

    reasons = answer_reasons + guardrail_reasons
    if not intent_ok:
        reasons.append(
            f"intent expected={expected.get('intent')!r} actual={actual_intent!r}"
        )
    if not ops_ok:
        reasons.append(
            f"ops expected_supersets={expected.get('operations_required')!r} actual={ops_used!r}"
        )

    return EvalResult(
        entry_id=entry["id"],
        category=entry.get("category", ""),
        query=entry["query"],
        expected=expected,
        actual_intent=actual_intent,
        actual_answer=actual_answer,
        actual_plan=actual_plan,
        intent_ok=intent_ok,
        ops_ok=ops_ok,
        answer_ok=answer_ok,
        guardrail_ok=guardrail_ok,
        reasons=reasons,
    )


async def _session_for_prior(entry: dict[str, Any], query_fn: QueryFn) -> str | None:
    """If the entry has a prior_turn, run that query first to seed the session.
    The runner's QueryFn must accept session creation/use through the standard
    flow, but for simpler testing we just submit the prior query and reuse
    whatever session_id the response provided. If the QueryFn doesn't support
    sessions, prior context simply doesn't apply.
    """
    prior = entry.get("prior_turn")
    if not prior:
        return None
    try:
        prior_resp = await query_fn(prior["query"], None)
    except Exception:  # noqa: BLE001
        return None
    sid = prior_resp.get("session_id") or prior_resp.get("request_id")
    return str(sid) if sid else None


# ---------- Matchers ----------------------------------------------------


def check_operations(ops_used: list[str], required: list[str]) -> bool:
    """Required is a multiset that must appear (in any order) in `ops_used`."""
    if not required:
        return True
    used = list(ops_used)
    for op in required:
        if op not in used:
            return False
        used.remove(op)
    return True


def check_answer(
    answer: str,
    expected: dict[str, Any],
    proposal: dict[str, Any] | None = None,
) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    text = (answer or "").lower()

    for s in expected.get("answer_must_contain", []):
        if str(s).lower() not in text:
            reasons.append(f"answer missing {s!r}")

    for s in expected.get("answer_must_not_contain", []):
        if str(s).lower() in text:
            reasons.append(f"answer should not contain {s!r}")

    one_of = expected.get("answer_must_contain_one_of")
    if one_of and not any(str(s).lower() in text for s in one_of):
        reasons.append(f"answer must contain one of {one_of!r}")

    # Write-specific assertions consult the proposal payload directly.
    diff_req = expected.get("write_diff_must_contain")
    if diff_req:
        if not proposal or not isinstance(proposal.get("diff"), dict):
            reasons.append("expected write_proposal with a diff but none returned")
        else:
            for field_name, want in diff_req.items():
                got = proposal["diff"].get(field_name)
                if got is None:
                    reasons.append(f"diff missing field {field_name!r}")
                else:
                    for k, v in want.items():
                        if got.get(k) != v:
                            reasons.append(
                                f"diff[{field_name!r}].{k} expected={v!r} got={got.get(k)!r}"
                            )

    fields_req = expected.get("write_fields_must_contain")
    if fields_req:
        if not proposal or not isinstance(proposal.get("proposed_values"), dict):
            reasons.append("expected proposed_values but none returned")
        else:
            for k, v in fields_req.items():
                if proposal["proposed_values"].get(k) != v:
                    reasons.append(
                        f"proposed_values[{k!r}] expected={v!r} got={proposal['proposed_values'].get(k)!r}"
                    )

    if expected.get("must_have_clarification") and "?" not in answer:
        reasons.append("expected a clarification question (no '?' in answer)")

    return len(reasons) == 0, reasons


def check_guardrails(
    response: dict[str, Any], entry: dict[str, Any]
) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    expected = entry.get("expected", {})

    # The injection-related guardrails are encoded as `answer_must_not_contain`
    # entries and already handled above; this hook exists for future
    # guardrail-only assertions.
    if expected.get("answer_must_handle_dangling"):
        warnings = response.get("warnings", []) or []
        if not any("dangling" in str(w).lower() for w in warnings):
            reasons.append("expected a 'dangling' warning in response.warnings")

    return len(reasons) == 0, reasons


# ---------- Public entry points ----------------------------------------


def load_queries(path: Path | str) -> list[dict[str, Any]]:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or []
    return list(raw)


async def run_all(
    queries: list[dict[str, Any]], query_fn: QueryFn
) -> list[EvalResult]:
    return [await run_entry(e, query_fn) for e in queries]


# ---------- CLI -------------------------------------------------------


def _cli() -> None:  # pragma: no cover - exercised via integration manually
    import argparse
    from datetime import UTC, datetime

    from fastapi.testclient import TestClient

    from eval.reporter import write_json_report, write_markdown_report
    from src.api.deps import build_container
    from src.api.main import app
    from src.config import get_settings

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "queries", nargs="?", default="backend/eval/queries.yaml"
    )
    parser.add_argument(
        "out_dir", nargs="?", default="backend/eval/reports"
    )
    args = parser.parse_args()

    settings = get_settings()
    if not hasattr(app.state, "container") or app.state.container is None:
        app.state.container = build_container(settings)

    client = TestClient(app)

    async def query_fn(q: str, session_id: str | None) -> dict[str, Any]:
        body: dict[str, Any] = {"query": q}
        if session_id:
            body["session_id"] = session_id
        r = client.post("/v1/query", json=body)
        return dict(r.json())

    queries = load_queries(args.queries)
    results = asyncio.run(run_all(queries, query_fn))
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    write_markdown_report(results, out / f"eval-{stamp}.md")
    write_json_report(results, out / "latest.json")
    passed = sum(1 for r in results if r.passed)
    print(f"{passed}/{len(results)} passed; report: {out / f'eval-{stamp}.md'}")


if __name__ == "__main__":
    _cli()
