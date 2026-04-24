"""
python-jobspy scraper — replacement for Adzuna.

Scrapes Indeed for jobs by company name, filtered to target states.
python-jobspy is a free, open-source library that scrapes job boards directly
(no API key, no quota):
    pip install python-jobspy

Key fields used from jobspy results:
    title           — job title
    company         — employer name (used for name-match filtering)
    location        — "City, ST, US" format
    job_url         — Indeed listing page (fallback)
    job_url_direct  — direct employer/ATS URL when available (preferred)
    date_posted     — date object

URL strategy (best → fallback):
    1. job_url_direct, if populated and not an Indeed URL → real employer ATS link
    2. job_url → clean Indeed listing page (reputable, no bot-detection)
    3. company["careers_url"] → company careers home page

scraper_config optional keys:
    search_name  — override the company name used in the Indeed query
                   (e.g. atos → "Eviden", esi-group → "Keysight")
    min_match    — minimum fraction of name tokens that must match (default 0.5)
"""

import re
import time
import unicodedata
import warnings

import pandas as pd

from .utils import detect_seniority, is_target_location

# Suppress jobspy/tls warnings that clutter output
warnings.filterwarnings("ignore")

# ── Constants ────────────────────────────────────────────────────────────────

_TARGET_STATES = {"DC", "MD", "VA", "PA", "WV", "DE"}

# Map our state codes → Indeed location strings
_STATE_LOCATION = {
    "DC": "Washington, DC",
    "MD": "Maryland",
    "VA": "Virginia",
    "PA": "Pennsylvania",
    "WV": "West Virginia",
    "DE": "Delaware",
}

RESULTS_PER_STATE = 50   # max results to fetch per (company, state) search
REQUEST_DELAY     = 2.0  # seconds between searches — be polite to Indeed

# Words to ignore when comparing company names
_NOISE = frozenset({
    "the", "inc", "incorporated", "ltd", "limited", "llc", "lp",
    "corp", "corporation", "co", "company", "group", "holding",
    "holdings", "international", "global", "north", "america",
    "american", "us", "usa", "and", "de", "sa", "sas", "se",
})


# ── Name-matching helpers ────────────────────────────────────────────────────

def _normalize(name: str) -> str:
    """Lowercase, strip accents, keep only alphanumeric + spaces."""
    nfkd = unicodedata.normalize("NFKD", name)
    ascii_name = nfkd.encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9 ]+", " ", ascii_name.lower()).strip()


def _tokens(name: str) -> list[str]:
    return [t for t in _normalize(name).split() if t not in _NOISE and len(t) > 1]


def _company_matches(result_name: str, our_name: str, min_match: float = 0.5) -> bool:
    """
    Return True if result_name plausibly refers to our_name.

    Strategy:
      1. Exact normalised match.
      2. The single longest significant token in our name (≥4 chars) must
         appear as a substring in result_name.
      3. Token overlap: at least min_match fraction of our tokens appear in theirs.
    """
    our_norm   = _normalize(our_name)
    their_norm = _normalize(result_name)

    if not their_norm:
        return False
    if our_norm == their_norm:
        return True

    our_toks = _tokens(our_name)
    if our_toks:
        primary = max(our_toks, key=len)
        if len(primary) >= 4 and primary in their_norm:
            return True

    their_toks = set(_tokens(result_name))
    if not our_toks:
        return False
    overlap = sum(1 for t in our_toks if t in their_toks)
    return overlap >= max(1, len(our_toks) * min_match)


# ── Location helpers ─────────────────────────────────────────────────────────

def _parse_location(loc_str: str) -> tuple[str | None, str]:
    """
    Parse a jobspy location string like "Ashburn, VA, US" or "Baltimore, MD, US".

    Returns (state_code, formatted_location) where formatted_location is "City, ST".
    Returns (None, loc_str) if no target state found.
    """
    if not loc_str:
        return None, ""

    state = is_target_location(loc_str)
    if not state:
        return None, loc_str

    # Build "City, ST" — strip the trailing ", US" if present
    parts = [p.strip() for p in loc_str.split(",")]
    # parts: ["Ashburn", "VA", "US"]  or  ["Baltimore", "MD", "US"]
    city = parts[0].title() if parts else ""
    return state, f"{city}, {state}" if city else state


# ── URL picker ───────────────────────────────────────────────────────────────

def _best_url(row: pd.Series, fallback: str) -> str:
    """
    Return the best available URL for a job result.
    Prefers the direct employer/ATS URL over the Indeed listing page.
    """
    direct = row.get("job_url_direct")
    if direct and pd.notna(direct):
        direct = str(direct).strip()
        if direct and "indeed.com" not in direct:
            return direct  # real employer ATS URL — best option

    indeed = row.get("job_url")
    if indeed and pd.notna(indeed):
        indeed = str(indeed).strip()
        if indeed:
            return indeed  # clean Indeed listing page — still reputable

    return fallback


# ── Main scraper ─────────────────────────────────────────────────────────────

def scrape_jobspy(company: dict) -> list[dict]:
    # Import here so the module loads even if python-jobspy isn't installed
    try:
        from jobspy import scrape_jobs
    except ImportError:
        print(f"  [jobspy] python-jobspy not installed — run: pip install python-jobspy")
        return _fallback(company)

    cfg         = company.get("scraper_config", {})
    search_name = cfg.get("search_name", company["name"])
    min_match   = float(cfg.get("min_match", 0.5))

    all_jobs: list[dict] = []
    seen_ids: set[str]   = set()
    states_to_search     = [s for s in company["states"] if s in _TARGET_STATES]

    for state_code in states_to_search:
        location = _STATE_LOCATION[state_code]
        time.sleep(REQUEST_DELAY)

        try:
            df = scrape_jobs(
                site_name=["indeed"],
                search_term=search_name,
                location=location,
                results_wanted=RESULTS_PER_STATE,
                country_indeed="USA",
                verbose=0,
            )
        except Exception as exc:
            print(f"    [jobspy] fetch error ({company['name']}, {state_code}): {exc}")
            continue

        if df is None or df.empty:
            continue

        for _, row in df.iterrows():
            # ── Company name filter ──────────────────────────────────────────
            result_company = str(row.get("company") or "")
            if not _company_matches(result_company, search_name, min_match):
                continue

            # ── Location filter ──────────────────────────────────────────────
            loc_str = str(row.get("location") or "")
            state, formatted_loc = _parse_location(loc_str)
            if not state or state != state_code:
                continue

            # ── Dedup ────────────────────────────────────────────────────────
            raw_id = str(row.get("id") or len(all_jobs))
            job_id = f"jsp-{company['id']}-{raw_id}"
            if job_id in seen_ids:
                continue
            seen_ids.add(job_id)

            # ── Title ────────────────────────────────────────────────────────
            title = str(row.get("title") or "").strip()
            if not title:
                continue

            # ── Date ─────────────────────────────────────────────────────────
            posted_date = ""
            dp = row.get("date_posted")
            if dp is not None and pd.notna(dp):
                try:
                    posted_date = str(dp)[:10]
                except Exception:
                    pass

            all_jobs.append({
                "id":          job_id,
                "company":     company["name"],
                "company_id":  company["id"],
                "title":       title,
                "location":    formatted_loc,
                "state":       state,
                "sector":      company["sector"],
                "seniority":   detect_seniority(title),
                "url":         _best_url(row, company["careers_url"]),
                "posted_date": posted_date,
                "source":      "indeed",
            })

    if not all_jobs:
        print(f"  [jobspy] {company['name']}: 0 jobs found — showing link")
        return _fallback(company)

    print(f"  [jobspy] {company['name']}: {len(all_jobs)} jobs in target states")
    return all_jobs


# ── Fallback ─────────────────────────────────────────────────────────────────

def _fallback(company: dict) -> list[dict]:
    return [
        {
            "id":          f"jsp-{company['id']}-{s}",
            "company":     company["name"],
            "company_id":  company["id"],
            "title":       "Voir les offres d'emploi \u2192",
            "location":    s,
            "state":       s,
            "sector":      company["sector"],
            "seniority":   "N/A",
            "url":         company["careers_url"],
            "posted_date": "",
            "source":      "manual_link",
        }
        for s in company["states"]
    ]
