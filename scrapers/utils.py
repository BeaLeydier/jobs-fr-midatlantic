"""
Shared utility functions for all scrapers.
"""

import re

# ---------------------------------------------------------------------------
# Target state definitions
# ---------------------------------------------------------------------------

# City/region names unambiguously in a given state — matched case-insensitively.
# Short 2-letter state codes are handled separately with strict regex below
# to avoid false positives (e.g. "de" in French city names, "pa" in "Japan").
_CITY_KEYWORDS: dict[str, list[str]] = {
    "DC": [
        "district of columbia", "washington, d.c.", "washington, dc",
        "washington dc", "d.c.", "dc metro", "greater washington", "national capital",
    ],
    "MD": [
        "maryland",
        "baltimore", "bethesda", "rockville", "silver spring",
        "gaithersburg", "annapolis", "frederick", "hagerstown",
        "college park", "greenbelt", "hyattsville", "north bethesda",
        "germantown", "bowie", "laurel", "chevy chase", "potomac",
    ],
    "VA": [
        "virginia",
        "arlington", "alexandria", "falls church", "mclean", "tysons",
        "richmond", "norfolk", "virginia beach", "charlottesville",
        "lynchburg", "reston", "herndon", "sterling, va", "fairfax",
        "manassas", "leesburg", "ashburn", "chantilly", "dulles",
        "annandale", "springfield",
        "northern virginia", "nova area",
    ],
    "PA": [
        "pennsylvania",
        "philadelphia", "pittsburgh", "harrisburg", "allentown",
        "scranton", "malvern", "king of prussia", "berwyn",
        "west chester", "conshohocken", "blue bell", "horsham",
        "doylestown", "exton", "montgomeryville",
        # Anchored to avoid substring matches:
        "lancaster, pa", "york, pa",
    ],
    "WV": [
        "west virginia",
        # All WV cities anchored with ", wv" to avoid matching same-named cities elsewhere
        "charleston, wv", "huntington, wv", "morgantown, wv",
        "martinsburg, wv", "ravenswood, wv", "fairmont, wv",
        "wheeling, wv", "parkersburg, wv",
    ],
    "DE": [
        "delaware",
        "wilmington", "dover, de", "newark, de", "middletown, de",
        "bear, de",
        # Avoid bare "dover" (many cities named Dover), "newark" (NJ, OH)
    ],
}

# Pre-compiled case-insensitive patterns for city/region names
_CITY_PATTERNS: dict[str, list[re.Pattern]] = {
    code: [re.compile(re.escape(kw), re.I) for kw in keywords]
    for code, keywords in _CITY_KEYWORDS.items()
}

# Strict patterns for 2-letter state abbreviations.
# These are case-SENSITIVE and require address-like context to avoid matching
# "de" in French city names, "pa" in "Japan", "va" in "evaluation", etc.
# Accepted contexts: ", PA" | "(PA)" | "PA " at start of string | " PA " surrounded by spaces
_ABBREV_PATTERNS: dict[str, re.Pattern] = {
    code: re.compile(
        r'(?:(?:,\s*|\()' + code + r'(?:\)|,|\s|$))'
        r'|(?:^' + code + r'(?:,|\s|$))',
        re.MULTILINE  # NOT re.I — must be uppercase
    )
    for code in ("DC", "MD", "VA", "PA", "WV", "DE")
}


def is_target_location(text: str) -> str | None:
    """
    Return the state code (e.g. 'VA') if text references one of our six target states.
    Returns None otherwise.

    Strategy:
    1. Check unambiguous city/region names (case-insensitive substring match).
    2. Check 2-letter state abbreviations with strict uppercase + address-context matching.
    """
    if not text:
        return None

    lower = text.lower()

    # WV before VA (Virginia is a substring of West Virginia)
    # DC before MD/VA ("Washington" context)
    for code in ("DC", "WV", "MD", "VA", "PA", "DE"):
        for pat in _CITY_PATTERNS[code]:
            if pat.search(lower):
                return code

    # State abbreviation check — case-sensitive, requires address context
    for code in ("DC", "WV", "MD", "VA", "PA", "DE"):
        if _ABBREV_PATTERNS[code].search(text):  # note: uses original text, not lowercased
            return code

    return None


# ---------------------------------------------------------------------------
# Seniority detection
# ---------------------------------------------------------------------------

SENIORITY_RULES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\b(intern|internship|co-?op|apprentice)\b", re.I), "Internship"),
    (re.compile(r"\b(entry[- ]level|entry level|junior|jr\.?|new grad|graduate|associate i\b)\b", re.I), "Entry Level"),
    (re.compile(r"\b(chief|c[tfs]o|ceo|coo|president|svp|evp|executive vice)\b", re.I), "Executive"),
    (re.compile(r"\b(vp|vice president|vice-president)\b", re.I), "Director/VP"),
    (re.compile(r"\b(director|head of|managing director)\b", re.I), "Director/VP"),
    (re.compile(r"\b(manager|management)\b", re.I), "Manager"),
    (re.compile(r"\b(principal|staff|distinguished|fellow|expert)\b", re.I), "Lead/Principal"),
    (re.compile(r"\b(lead|senior|sr\.?)\b", re.I), "Senior"),
]


def detect_seniority(title: str) -> str:
    """Return a seniority label inferred from a job title string."""
    for pattern, label in SENIORITY_RULES:
        if pattern.search(title):
            return label
    return "Mid-level"


# ---------------------------------------------------------------------------
# Location helpers
# ---------------------------------------------------------------------------

_LOCATION_CLEAN = re.compile(r"\s{2,}")


def normalize_location(raw: str) -> str:
    """Clean up a raw location string."""
    if not raw:
        return ""
    cleaned = _LOCATION_CLEAN.sub(" ", raw.strip())
    return cleaned.title()
