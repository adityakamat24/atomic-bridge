from __future__ import annotations

import unicodedata

from pydantic import BaseModel


class InputRejected(Exception):  # noqa: N818  -- name fixed by spec
    """Hard-fail rejection of an incoming request. The API layer translates
    this to HTTP 400."""


class ValidatedQuery(BaseModel):
    text: str


class InputValidator:
    """Cheap pre-LLM checks. Intentionally narrow — does NOT try to detect
    prompt injection via content matching (that's the Dual LLM defense's
    job). Catches obvious abuse: oversized queries, control chars, JS/HTML
    injection from the frontend.
    """

    MAX_QUERY_LEN = 2000
    BANNED_SUBSTRINGS: tuple[str, ...] = (
        "javascript:",
        "data:text/html",
        "<script",
    )

    def validate(self, query: str) -> ValidatedQuery:
        if not isinstance(query, str):
            raise InputRejected("query must be a string")
        if len(query) > self.MAX_QUERY_LEN:
            raise InputRejected(
                f"query too long ({len(query)} > {self.MAX_QUERY_LEN})"
            )
        # Unicode normalisation + control-char strip (keep newline & tab).
        text = unicodedata.normalize("NFKC", query)
        text = "".join(
            c
            for c in text
            if c in "\n\t" or not unicodedata.category(c).startswith("C")
        )
        text_low = text.lower()
        for banned in self.BANNED_SUBSTRINGS:
            if banned in text_low:
                raise InputRejected(f"forbidden substring: {banned!r}")
        return ValidatedQuery(text=text)
