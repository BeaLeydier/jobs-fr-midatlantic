"""
Engie SuccessFactors Job2Web React API scraper.

Engie uses a React-rendered variant of SAP SuccessFactors Job2Web.
Jobs are NOT server-rendered in HTML — they're loaded via a JSON REST API.

API endpoint:
  POST https://jobs.engie.com/services/recruiting/v1/jobs

Auth:
  Requires an X-CSRF-Token header obtained from the search page HTML.
  A requests.Session is used so the session cookie is kept alongside the token.

Request body:
  {"locale": "en_US", "pageNumber": N, "sortBy": "", "keywords": "",
   "location": "", "facetFilters": {}, "brand": "", "skills": [],
   "categoryId": 0, "alertId": "", "rcmCandidateId": ""}

Response:
  {"totalJobs": 584, "jobSearchResult": [{
      "response": {
          "id": 64382,
          "unifiedStandardTitle": "Sustainability Waste Advisor",
          "urlTitle": "Sustainability-Waste-Advisor",
          "jobLocationShort": [
              "Remote, United States, Virginia ",
              "Remote, United States, Maryland ",
              ...
          ],
          ...
      }
  }, ...]}

Location formats in jobLocationShort:
  "Remote, United States, {Full State Name} "  — remote roles listing eligible states
  "{City}, United States, {ZIP} "               — on-site roles

Pagination:  10 jobs per page; paginate via pageNumber=0,1,2,…
"""

import re
import time
from urllib.parse import quote
import requests
from .utils import detect_seniority

PAGE_SIZE = 10
MAX_PAGES = 100  # safety cap

SEARCH_URL = "https://jobs.engie.com/services/recruiting/v1/jobs"
PAGE_URL = "https://jobs.engie.com/search/?q="

HEADERS_BASE = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

# Maps full state name (as it appears in Engie's location strings) to 2-letter code.
# Order matters: "West Virginia" must come before "Virginia".
_STATE_NAMES = [
    ("Washington DC", "DC"),
    ("West Virginia", "WV"),
    ("Virginia", "VA"),
    ("Maryland", "MD"),
    ("Pennsylvania", "PA"),
    ("Delaware", "DE"),
]

# ZIP-code ranges for target states (for on-site roles with a ZIP instead of state name).
# Ranges are approximate; a small number of edge ZIPs may straddle borders.
_ZIP_RANGES = [
    (20001, 20599, "DC"),
    (20600, 21999, "MD"),
    (19700, 19999, "DE"),
    (15000, 19699, "PA"),
    (20100, 24699, "VA"),
    (24700, 26899, "WV"),
]

_ZIP_RE = re.compile(r"\b(\d{5})\b")
_CSRF_RE = re.compile(r'csrf[_-]?token[^\'"]*["\']([a-f0-9\-]{30,})', re.IGNORECASE)


def _get_session() -> tuple[requests.Session, str]:
    """Return (session, csrf_token) after loading the search page."""
    session = requests.Session()
    session.headers.update(HEADERS_BASE)
    resp = session.get(PAGE_URL, timeout=20)
    resp.raise_for_status()
    m = _CSRF_RE.search(resp.text)
    if not m:
        raise ValueError("Could not find CSRF token on Engie search page")
    return session, m.group(1)


def _state_from_locations(locs: list[str]) -> str | None:
    """
    Given a list of jobLocationShort strings, return the first matching
    target state code, or None.
    """
    for loc in locs:
        parts = [p.strip() for p in loc.split(",")]
        if len(parts) < 2 or "United States" not in loc:
            continue
        last = parts[-1]

        # State-name format ("Remote, United States, Virginia ")
        for state_name, code in _STATE_NAMES:
            if state_name.lower() == last.lower():
                return code

        # ZIP format ("Broomfield, United States, 80021 ")
        m = _ZIP_RE.match(last)
        if m:
            zip5 = int(m.group(1))
            for lo, hi, code in _ZIP_RANGES:
                if lo <= zip5 <= hi:
                    return code

    return None


def _all_states(locs: list[str]) -> set[str]:
    """Return all matching target state codes across a multi-location job."""
    found = set()
    for loc in locs:
        parts = [p.strip() for p in loc.split(",")]
        if len(parts) < 2 or "United States" not in loc:
            continue
        last = parts[-1]
        for state_name, code in _STATE_NAMES:
            if state_name.lower() == last.lower():
                found.add(code)
                break
        else:
            m = _ZIP_RE.match(last)
            if m:
                zip5 = int(m.group(1))
                for lo, hi, code in _ZIP_RANGES:
                    if lo <= zip5 <= hi:
                        found.add(code)
    return found


def _format_location(locs: list[str], state: str) -> str:
    """
    Build a display location string. For remote multi-state jobs, just use
    "Remote ({state})". For on-site jobs, use "City, State".

    Only uses a city/ZIP entry if the ZIP actually falls within the ranges
    for the requested state — avoids "Boston, MD" when the ZIP is in MA.
    """
    for loc in locs:
        parts = [p.strip() for p in loc.split(",")]
        if "United States" not in loc:
            continue
        city = parts[0]
        last = parts[-1]
        # State-name format: last token equals the full state name for `state`
        for state_name, code in _STATE_NAMES:
            if code == state and state_name.lower() == last.lower():
                if city.lower() in ("remote", ""):
                    return f"Remote ({state})"
                return f"{city.title()}, {state}"
        # ZIP format: only use this city if the ZIP maps to `state`
        m = _ZIP_RE.match(last)
        if m:
            zip5 = int(m.group(1))
            for lo, hi, code in _ZIP_RANGES:
                if lo <= zip5 <= hi and code == state:
                    if city.lower() not in ("remote", ""):
                        return f"{city.title()}, {state}"
                    return f"Remote ({state})"
    return f"Remote ({state})"


def scrape_engie(company: dict) -> list[dict]:
    try:
        session, csrf = _get_session()
    except Exception as exc:
        print(f"  [engie] Could not get CSRF token: {exc}")
        return _fallback(company)

    api_headers = {
        "Content-Type": "application/json",
        "x-csrf-token": csrf,
        "Referer": PAGE_URL,
    }
    base_body = {
        "locale": "en_US",
        "pageNumber": 0,
        "sortBy": "",
        "keywords": "",
        "location": "",
        "facetFilters": {},
        "brand": "",
        "skills": [],
        "categoryId": 0,
        "alertId": "",
        "rcmCandidateId": "",
    }

    all_jobs: list[dict] = []
    total = None

    for page in range(MAX_PAGES):
        body = {**base_body, "pageNumber": page}
        try:
            resp = session.post(SEARCH_URL, headers=api_headers, json=body, timeout=20)
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            print(f"  [engie] fetch error (page {page}): {exc}")
            break

        if total is None:
            total = data.get("totalJobs", 0)

        results = data.get("jobSearchResult", [])
        if not results:
            break

        for item in results:
            job_data = item.get("response", item)
            locs = job_data.get("jobLocationShort", [])
            if not isinstance(locs, list):
                locs = [locs] if locs else []

            states = _all_states(locs)
            if not states:
                continue

            job_id_raw = job_data.get("id", "")
            url_title = job_data.get("urlTitle") or job_data.get("unifiedUrlTitle") or ""
            title = job_data.get("unifiedStandardTitle") or job_data.get("title") or url_title.replace("-", " ").title()
            # Link to a title-prefilled search rather than /jobs/{id}/.
            # The site is a React SPA — ID-based deep links silently redirect to
            # the homepage when a job expires, which happens between daily scrapes.
            # A search URL always shows the live job (or similar roles if it just closed).
            job_url = f"https://jobs.engie.com/search/?q={quote(title)}"

            for state in states:
                location = _format_location(locs, state)
                all_jobs.append({
                    "id": f"engie-{job_id_raw}-{state}",
                    "company": company["name"],
                    "company_id": company["id"],
                    "title": title,
                    "location": location,
                    "state": state,
                    "sector": company["sector"],
                    "seniority": detect_seniority(title),
                    "url": job_url,
                    "posted_date": "",
                    "source": "engie",
                })

        if total and (page + 1) * PAGE_SIZE >= total:
            break

        time.sleep(0.3)

    if not all_jobs:
        print(f"  [engie] {company['name']}: 0 jobs in target states — showing link")
        return _fallback(company)

    print(f"  [engie] {company['name']}: {len(all_jobs)} entries across target states "
          f"(from {total} total)")
    return all_jobs


def _fallback(company: dict) -> list[dict]:
    return [
        {
            "id": f"engie-{company['id']}-{s}",
            "company": company["name"],
            "company_id": company["id"],
            "title": "Voir les offres d'emploi \u2192",
            "location": s,
            "state": s,
            "sector": company["sector"],
            "seniority": "N/A",
            "url": company["careers_url"],
            "posted_date": "",
            "source": "manual_link",
        }
        for s in company["states"]
    ]
