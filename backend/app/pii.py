"""Rule-based PII scrubbing (regex + cue-word heuristics).

Limits (documented in the README): names are only detected when introduced by a cue such as
"my son Liam" / "my name is Sarah" or when they match the signed-in parent's registered name.
A bare, lowercase, or unusual name can slip through. This is a best-effort prototype control,
not a compliance guarantee.
"""
import re
from dataclasses import dataclass, field

EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
SSN = re.compile(r"(?<!\d)\d{3}-\d{2}-\d{4}(?!\d)")
PHONE = re.compile(
    r"(?<!\d)(?:\+?1[\s.-]?)?(?:\(\d{3}\)\s?|\d{3}[\s.-]?)\d{3}[\s.-]?\d{4}(?!\d)"
    r"|(?<!\d)\d{3}[-.]\d{4}(?!\d)"
)
_MONTH = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?"
DOB = re.compile(
    r"(?i:\b(?:born|dob|d\.o\.b\.?|birthday|birth\s?date|date of birth)\b)[^.?!\n]{0,25}?"
    r"(\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|(?i:" + _MONTH + r")\s+\d{1,2}(?:st|nd|rd|th)?,?(?:\s*\d{4})?)"
)
ADDRESS = re.compile(
    r"\b\d{1,5}\s+(?:[A-Za-z]+\s+){1,3}(?i:street|st|avenue|ave|road|rd|drive|dr|lane|ln|court|ct|"
    r"boulevard|blvd|way|circle|cir|place|pl)\b\.?"
)
SELF_CUE = re.compile(
    r"(?i:\b(?:my name is|my name's|i am|i'm|call me))\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)"
)
CHILD_CUE = re.compile(
    r"(?i:\b(?:my|our)\s+(?:son|daughter|child|kid|baby|toddler|infant|niece|nephew|grandson|"
    r"granddaughter|stepson|stepdaughter|twins?))(?:'s)?(?:\s+(?i:name is|named|called))?,?\s+"
    r"([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)"
)
NAME_STOP = {
    "The", "This", "That", "He", "She", "They", "It", "Is", "Was", "Has", "Had", "Will", "Can", "Does",
    "Did", "Goes", "Needs", "Would", "Could", "Should", "Also", "And", "But", "Who", "What", "When",
    "Where", "Why", "How", "Not", "Have", "Gets", "Got", "Loves", "Likes", "Wants", "Just", "Very",
    "Really", "Still", "Always", "Never", "Turns", "Turned", "Starts", "Attends", "Eats", "Sleeps",
    "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday", "January",
    "February", "March", "April", "May", "June", "July", "August", "September", "October",
    "November", "December", "Little", "Acorns", "Please", "If", "Do", "Are", "Am", "Im",
}


@dataclass
class ScrubResult:
    text: str
    types: set = field(default_factory=set)


def _clean_name(raw: str) -> str:
    words = []
    for w in raw.split():
        if w in NAME_STOP:
            break
        words.append(w)
    return " ".join(words)


def _replace_group(match: re.Match, group: int, replacement: str) -> str:
    s = match.start(0)
    return match.group(0)[: match.start(group) - s] + replacement + match.group(0)[match.end(group) - s :]


def scrub(text: str, known_names: list | None = None, known_emails: list | None = None) -> ScrubResult:
    types: set = set()

    def sub(pattern: re.Pattern, tag: str, s: str) -> str:
        def repl(m: re.Match) -> str:
            types.add(tag)
            return f"[{tag}]"

        return pattern.sub(repl, s)

    out = text
    for e in known_emails or []:
        if e and re.search(re.escape(e), out, re.I):
            types.add("EMAIL")
            out = re.sub(re.escape(e), "[EMAIL]", out, flags=re.I)
    out = sub(EMAIL, "EMAIL", out)
    out = sub(SSN, "SSN", out)

    def dob_repl(m: re.Match) -> str:
        types.add("DOB")
        return _replace_group(m, 1, "[DOB]")

    out = DOB.sub(dob_repl, out)
    out = sub(PHONE, "PHONE", out)
    out = sub(ADDRESS, "ADDRESS", out)

    names: set = set()
    for pattern in (SELF_CUE, CHILD_CUE):
        for m in pattern.finditer(out):
            name = _clean_name(m.group(1))
            if name:
                names.add(name)
    for full in known_names or []:
        full = (full or "").strip()
        if len(full) >= 2:
            names.add(full)
    # Replace the full names, then each individual part (len >= 3) everywhere in the text.
    parts: set = set()
    for n in names:
        parts.update(p for p in n.split() if len(p) >= 3)
    for n in sorted(names, key=len, reverse=True):
        pat = re.compile(r"\b" + re.escape(n) + r"\b", re.I)
        if pat.search(out):
            types.add("NAME")
            out = pat.sub("[NAME]", out)
    for p in sorted(parts, key=len, reverse=True):
        pat = re.compile(r"\b" + re.escape(p) + r"\b", re.I)
        if pat.search(out):
            types.add("NAME")
            out = pat.sub("[NAME]", out)
    return ScrubResult(out, types)


def detect(text: str) -> set:
    """Detect (without modifying) PII types in text, e.g. to warn an operator before they publish an answer."""
    return scrub(text).types
