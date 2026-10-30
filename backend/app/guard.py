"""Pre-LLM safety checks: prompt-injection attempts, emergencies, and sensitive topics.
These run before the model so the worst cases never depend on the model behaving."""
import re
from dataclasses import dataclass

from .config import CENTER_NAME, CENTER_PHONE, DIRECTOR_NAME

INJECTION_PATTERNS = [
    r"ignore (?:all |any |your |the |my )?(?:previous |prior |above |earlier )?(?:instructions|rules|prompt|guidelines)",
    r"disregard (?:all |any |your |the )?(?:previous |prior )?(?:instructions|rules|guidelines)",
    r"forget (?:all |everything|your |the |previous )",
    r"(?:reveal|show|print|repeat|display|leak|tell me) (?:me )?(?:your |the )?(?:system |initial |hidden |secret )?(?:prompt|instructions)",
    r"system prompt",
    r"you are now\b",
    r"pretend (?:to be|you(?:'re| are))",
    r"\bact as (?:a|an|if|though)\b",
    r"developer mode",
    r"jailbreak",
    r"\bDAN\b",
    r"do anything now",
    r"override (?:your|the) (?:rules|instructions|safety|restrictions)",
    r"new instructions",
    r"from now on,? you",
    r"</?\s*(?:system|user_question|context|instructions?)\s*>",
]
_INJECTION = re.compile("|".join(f"(?:{p})" for p in INJECTION_PATTERNS), re.I)

EMERGENCY_PATTERNS = [
    r"\bchok(?:ing|es|ed)\b",
    r"(?:not|stopped|isn'?t|can'?t|cannot|won'?t) breath",
    r"unconscious",
    r"unresponsive",
    r"having an? (?:seizure|allergic reaction|asthma attack)",
    r"(?:bleeding (?:a lot|heavily|badly)|won'?t stop bleeding)",
    r"\b(?:swallowed|ingested) (?:a |some )?(?:battery|bleach|pills?|poison|medicine)",
    r"\bpoison(?:ed|ing)\b",
    r"overdos",
    r"\b911\b",
    r"need an ambulance",
]
_EMERGENCY = re.compile("|".join(f"(?:{p})" for p in EMERGENCY_PATTERNS), re.I)

SENSITIVE_PATTERNS = [
    r"\babus(?:e|ed|ing|ive)\b",
    r"\bneglect",
    r"\bmolest",
    r"\bcustody\b",
    r"restraining order",
    r"court order",
    r"\b(?:lawyer|attorney|lawsuit)\b",
    r"\bsue (?:you|the|them|your)\b",
    r"legal action",
    r"\bdivorce",
    r"\bbruis",
    r"(?:another|other|someone else'?s|their) (?:child|kid)(?:'s)? (?:name|info|address|photo|medical|records?)",
    r"\bdiagnos",
    r"\bdosage\b",
    r"should i give (?:my|him|her) (?:child |son |daughter |kid )?(?:any )?(?:medicine|medication|tylenol|ibuprofen|motrin)",
]
_SENSITIVE = re.compile("|".join(f"(?:{p})" for p in SENSITIVE_PATTERNS), re.I)

TEMPLATE_REFUSAL = (
    f"I can only help with questions about {CENTER_NAME}, such as hours, tuition, illness policies, "
    "meals, and tours. What would you like to know about the center?"
)
TEMPLATE_EMERGENCY = (
    "If your child needs urgent medical help, please call 911 right away. You can also call "
    f"{CENTER_NAME} directly at {CENTER_PHONE}. I've flagged your message for the center's administrator."
)
TEMPLATE_SENSITIVE = (
    "Thank you for reaching out. This is a sensitive topic that needs a personal response, so I've "
    f"flagged it for the center director, {DIRECTOR_NAME}. If it's urgent, please call the center at {CENTER_PHONE}."
)
TEMPLATE_ESCALATED = (
    "I don't have a confirmed answer to that yet. I've sent your question to the center's administrator, "
    "and the answer will appear in your history once it's ready."
)
TEMPLATE_LLM_DOWN = (
    "I can't answer right now. I've forwarded your question to the center's administrator, and the answer "
    "will appear in your history once it's ready."
)
TEMPLATE_DISMISSED = (
    "The center's staff reviewed your question but couldn't answer it here. "
    f"Please call us at {CENTER_PHONE}."
)


@dataclass
class GuardResult:
    injection: bool = False
    emergency: bool = False
    sensitive: bool = False


def check(text: str) -> GuardResult:
    return GuardResult(
        injection=bool(_INJECTION.search(text)),
        emergency=bool(_EMERGENCY.search(text)),
        sensitive=bool(_SENSITIVE.search(text)),
    )
