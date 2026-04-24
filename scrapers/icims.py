"""
iCIMS Career Site Builder scraper.

iCIMS portals render their job search page as a wrapper that loads an iframe.
That iframe URL (with in_iframe=1) returns server-rendered HTML — no headless
browser required.

URL pattern:
  https://{portal}.icims.com/jobs/search?ss=1&in_iframe=1&pageSize=50&startRow=N

DOM structure:
  ul.iCIMS_JobsTable > li.iCIMS_JobCardItem   (one per job)
    div.col-xs-6.header.left span:not(.sr-only)  → location ("US-XX-City" or free text)
    a.iCIMS_Anchor h3                            → job title
    a.iCIMS_Anchor[href]                         → detail URL (contains in_iframe param to strip)

State detection priority:
  1. Parse "US-XX-..." iCIMS format (unambiguous 2-letter code in position 2)
  2. Fall back to utils.is_target_location() for free-text locations
"""

import re
import time
import requests
from urllib.parse import urlparse, urlencode, parse_qs, urlunparse
from bs4 import BeautifulSoup
from .utils import normalize_location, detect_seniority, is_target_location

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

PAGE_SIZE = 50
MAX_PAGES = 100   # safety cap (50 × 100 = 5 000 jobs max per portal)

_TARGET_STATES = frozenset(("DC", "MD", "VA", "PA", "WV", "DE"))


_ICIMS_LOC_RE = re.compile(r"^US-([A-Z]{2})-(.+)$")


def _format_location(raw: str) -> str:
    """
    Convert iCIMS "US-XX-CityName" to "CityName, XX".
    For multi-location strings (e.g. "US-MD-Columbia | US-VA-..."), format the first match.
    Falls back to normalize_location for non-standard formats.
    """
    # Handle multi-location: take only the first segment
    first = raw.split("|")[0].strip()
    m = _ICIMS_LOC_RE.match(first)
    if m:
        state_code, city = m.group(1), m.group(2).replace("-", " ")
        # normalize_location calls .title() which would lowercase "VA" → "Va"
        # so we apply it to the city only and append the state code unchanged
        return f"{city.title()}, {state_code}"
    return normalize_location(raw)


def _clean_url(href: str) -> str:
    """Strip in_iframe=1 from a job detail URL."""
    parsed = urlparse(href)
    params = {k: v[0] for k, v in parse_qs(parsed.query).items() if k != "in_iframe"}
    return urlunparse(parsed._replace(query=urlencode(params) if params else ""))


def _state_from_location(loc: str) -> str | None:
    """
    Detect target state from an iCIMS location string.

    iCIMS locations are typically formatted as "US-XX-CityName" where XX is the
    2-letter state code.  We extract it directly before falling back to the
    general text matcher to avoid false positives from the abbreviation rules.
    """
    m = re.match(r"^US-([A-Z]{2})(?:-|$)", loc.strip())
    if m and m.group(1) in _TARGET_STATES:
        return m.group(1)
    return is_target_location(loc)


def _fetch_page(portal: str, start_row: int) -> BeautifulSoup | None:
    url = (
        f"https://{portal}.icims.com/jobs/search"
        f"?ss=1&in_iframe=1&pageSize={PAGE_SIZE}&startRow={start_row}"
    )
    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
        resp.raise_for_status()
        return BeautifulSoup(resp.text, "lxml")
    except Exception as exc:
        print(f"    iCIMS fetch error ({portal}, row {start_row}): {exc}")
        return None


def _parse_cards(
    soup: BeautifulSoup,
    portal: str,
    company: dict,
    seen_ids: set,
) -> list[dict]:
    jobs = []
    for card in soup.select("li.iCIMS_JobCardItem"):
        # Location (iCIMS wraps the sr-only label in a sibling span)
        loc_el = card.select_one(".header.left span:not(.sr-only)")
        location_raw = loc_el.get_text(strip=True) if loc_el else ""

        state = _state_from_location(location_raw)
        if not state:
            continue

        # Title (use h3 to skip the sr-only "Title" / "Job Title" prefix)
        anchor = card.select_one("a.iCIMS_Anchor")
        if not anchor:
            continue
        h3 = anchor.select_one("h3")
        title = h3.get_text(strip=True) if h3 else anchor.get_text(strip=True)
        if not title:
            continue

        href = anchor.get("href", "")
        job_url = _clean_url(href) if href else company["careers_url"]

        # Stable ID from URL path (e.g. /jobs/4943/...)
        path = urlparse(href).path
        job_id = f"ic-{portal}-{re.sub(r'[^a-z0-9]', '-', path.lower()).strip('-')}"
        if job_id in seen_ids:
            continue
        seen_ids.add(job_id)

        jobs.append({
            "id": job_id,
            "company": company["name"],
            "company_id": company["id"],
            "title": title,
            "location": _format_location(location_raw),
            "state": state,
            "sector": company["sector"],
            "seniority": detect_seniority(title),
            "url": job_url,
            "posted_date": "",
            "source": "icims",
        })
    return jobs


def scrape_icims(company: dict) -> list[dict]:
    portal = company["scraper_config"].get("portal", company["id"])

    all_jobs: list[dict] = []
    seen_ids: set[str] = set()
    start_row = 0

    for page_num in range(MAX_PAGES):
        soup = _fetch_page(portal, start_row)
        if soup is None:
            break

        cards = soup.select("li.iCIMS_JobCardItem")
        if not cards:
            break

        page_jobs = _parse_cards(soup, portal, company, seen_ids)
        all_jobs.extend(page_jobs)

        if len(cards) < PAGE_SIZE:
            break  # Returned fewer than a full page → we're done

        start_row += PAGE_SIZE
        if page_num < MAX_PAGES - 1:
            time.sleep(0.3)

    if not all_jobs:
        print(f"  [iCIMS] {company['name']}: 0 jobs in target states — showing link")
        return _fallback(company, portal)

    print(f"  [iCIMS] {company['name']}: {len(all_jobs)} jobs in target states")
    return all_jobs


def _fallback(company: dict, portal: str) -> list[dict]:
    url = company.get("careers_url") or f"https://{portal}.icims.com/jobs/search?ss=1"
    return [
        {
            "id": f"ic-{company['id']}-{s}",
            "company": company["name"],
            "company_id": company["id"],
            "title": "Voir les offres d'emploi \u2192",
            "location": s,
            "state": s,
            "sector": company["sector"],
            "seniority": "N/A",
            "url": url,
            "posted_date": "",
            "source": "manual_link",
        }
        for s in company["states"]
    ]
