"""Retrieval: selects and ranks the source-of-truth documents handed to the LLM.

The knowledge base for a single center is small, so we rank every active document by lexical
overlap with the question and include as many as fit the character budget (staff-approved Q&A
pairs first, newest first, because they take priority over handbook entries). Embeddings are not
needed for the MVP and would consume free-tier quota.
"""
import re
import sqlite3
from dataclasses import dataclass

from .config import get_settings

STOP = set(
    "a an and are as at be but by can could do does for from had has have how i if in is it its me my "
    "of on or our so that the their them there they this to was we what when where which who why will "
    "with would you your am about any some tell please know want need get got".split()
)


def _stem(word: str) -> str:
    for suffix in ("ing", "ed", "es", "s", "ly"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            word = word[: -len(suffix)]
            break
    return word[:7]


def tokens(text: str) -> set:
    return {_stem(w) for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in STOP and len(w) > 1}


@dataclass
class Doc:
    id: str       # "K12" = knowledge entry, "Q5" = staff-approved Q&A pair
    kind: str     # knowledge | staff_qa
    title: str
    body: str
    score: float


def normalize_question(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9\[\]]+", text.lower()))


def retrieve(db: sqlite3.Connection, question: str) -> list:
    budget = get_settings().context_char_budget
    qtokens = tokens(re.sub(r"\[[A-Z]+\]", " ", question))  # ignore PII placeholders like [NAME]

    def score(text: str) -> float:
        if not qtokens:
            return 0.0
        return len(qtokens & tokens(text)) / len(qtokens)

    docs = []
    for r in db.execute("SELECT * FROM qa_pairs WHERE active=1 ORDER BY id DESC"):
        docs.append(Doc(f"Q{r['id']}", "staff_qa", r["question"], r["answer"], score(r["question"] + " " + r["answer"])))
    for r in db.execute("SELECT * FROM knowledge_entries WHERE active=1"):
        docs.append(Doc(f"K{r['id']}", "knowledge", r["title"], r["body"], score(r["title"] + " " + r["body"])))

    qa = [d for d in docs if d.kind == "staff_qa"]
    kn = sorted((d for d in docs if d.kind == "knowledge"), key=lambda d: -d.score)
    # Staff-approved pairs that actually relate to the question go first (newest first), then handbook.
    qa_relevant = [d for d in qa if d.score > 0]
    ordered = qa_relevant + kn + [d for d in qa if d.score == 0]

    chosen, used = [], 0
    for d in ordered:
        size = len(d.title) + len(d.body)
        if used + size > budget:
            continue
        chosen.append(d)
        used += size
    return chosen


def exact_staff_match(db: sqlite3.Connection, question: str):
    """If a staff-approved Q&A pair has the same (normalized) question, return it. Saves quota and
    guarantees staff corrections win for repeat questions."""
    norm = normalize_question(question)
    for r in db.execute("SELECT * FROM qa_pairs WHERE active=1 ORDER BY id DESC"):
        if normalize_question(r["question"]) == norm:
            return r
    return None
