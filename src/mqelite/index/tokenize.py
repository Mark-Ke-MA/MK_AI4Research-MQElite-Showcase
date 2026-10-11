"""Network-free deterministic Latin and CJK lexical tokenization."""

from __future__ import annotations

import re
import unicodedata

WORD = re.compile(r"[^\W_]+", re.UNICODE)


def normalize_text(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold()


def _is_cjk(character: str) -> bool:
    codepoint = ord(character)
    return (
        0x3400 <= codepoint <= 0x4DBF
        or 0x4E00 <= codepoint <= 0x9FFF
        or 0xF900 <= codepoint <= 0xFAFF
        or 0x3040 <= codepoint <= 0x30FF
        or 0xAC00 <= codepoint <= 0xD7AF
    )


def query_terms(value: str) -> tuple[str, ...]:
    normalized = normalize_text(value)
    terms: list[str] = []
    for word in WORD.findall(normalized):
        non_cjk = "".join(character for character in word if not _is_cjk(character))
        if non_cjk:
            terms.append(non_cjk)
    runs: list[str] = []
    current: list[str] = []
    for character in normalized:
        if _is_cjk(character):
            current.append(character)
        elif current:
            runs.append("".join(current))
            current = []
    if current:
        runs.append("".join(current))
    for run in runs:
        if len(run) == 1:
            terms.append(run)
        else:
            terms.extend(run[index : index + 2] for index in range(len(run) - 1))
            if len(run) >= 3:
                terms.extend(run[index : index + 3] for index in range(len(run) - 2))
    return tuple(dict.fromkeys(term for term in terms if term))


def index_terms(value: str) -> str:
    return " ".join(query_terms(value))
