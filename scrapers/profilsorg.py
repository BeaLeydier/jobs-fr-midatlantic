"""
profils.org ATS scraper.

Several French companies host their US career pages on profils.org
(a French HR platform by Taleo/Oracle spin-off). The site is server-rendered
ASP.NET HTML — no headless browser needed.

Search URL (US jobs):
  https://{subdomain}.profils.org/job/list-of-all-jobs.aspx?changefacet=1&facet_JobCountry=76

  facet_JobCountry=76 is the US (North America → United States).
  The filter is set by a session cookie on the first request; subsequent pages
  can be fetched with just ?page=N&LCID=1033 (English) while reusing the session.

DOM structure:
  li.ts-offer-list-item              — one item per job
  a.ts-offer-list-item__title-link   — title (text) + relative href
  ul.ts-offer-list-item__description — description items:
    li[0]  Ref number ("Réf. : 2025-166003")
    li[1]  Date
    li[2]  Contract type ("CDI", "Stage", "VIE", …)
    li[-1] Address — e.g. "2641 Airpark Drive CA 93455 Santa Maria"
                         "415 Riverside Rd. WA 98201 Everett"
                         "Gainesville"         ← city only (rare)

State detection:
  1. Regex r'\\b([A-Z]{2})\\s+\\d{5}' in address → 2-letter state code before ZIP
  2. Fallback: is_target_location(address) for city-only addresses

Pagination:
  20 items per page; total shown in <title>.
  Page 1 URL sets the country filter (session cookie).
  Pages 2+: ?page=N&LCID=1033

scraper_config keys:
  subdomain   — e.g. "safran"  → safran.profils.org
  (optional) country_code  — defaults to 76 (US)
"""

import re
import time
import requests
from urllib.parse import urljoin
from bs4 import BeautifulSoup
from .utils import detect_seniority, is_target_location

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

PAGE_SIZE = 20
MAX_PAGES = 100

_TOTAL_RE = re.compile(r"\((\d+)\s*(?:offres|job|vacanc)", re.IGNORECASE)
_STATE_ZIP_RE = re.compile(r"\b([A-Z]{2})\s+\d{5}")


def _parse_total(soup: BeautifulSoup) -> int:
    title = soup.title.string if soup.title else ""
    m = _TOTAL_RE.search(title)
    if m:
        return int(m.group(1))
    return 0


def _state_from_address(address: str) -> str | None:
    """Extract target state code from a profils.org address string."""
    # First try: look for 2-letter state code followed by 5-digit ZIP
    m = _STATE_ZIP_RE.search(address)
    if m:
        code = m.group(1)
        return is_target_location(code) or (code if code in _TARGET_STATES else None)
    # Fallback: city-name detection
    return is_target_location(address)


_TARGET_STATES = frozenset(("DC", "MD", "VA", "PA", "WV", "DE"))


def _format_address(address: str, state: str) -> str:
    """Build a 'City, STATE' string from the raw profils.org address."""
    # Try to extract city from the address
    # Formats: "2641 Airpark Drive CA 93455 Santa Maria" → "Santa Maria, CA"
    #          "1620 Sunflower Avenue CA 92626" → just use state
    #          "Gainesville" → "Gainesville, VA"
    m = _STATE_ZIP_RE.search(address)
    if m:
        # Text after the ZIP might be the city
        after_zip = address[m.end():].strip().strip(",").strip()
        if after_zip:
            return f"{after_zip.title()}, {state}"
        # Text before the state code might contain the city
        before = address[:m.start()].strip()
        # Try to get last word/phrase as city
        parts = re.split(r"\d+", before)
        city_guess = parts[-1].strip().strip(",").strip()
        if city_guess and len(city_guess) > 1:
            return f"{city_guess.title()}, {state}"
    # City-only or unrecognized format
    words = address.strip().split()
    if len(words) <= 3:
        return f"{address.title()}, {state}"
    return state


def _parse_items(soup: BeautifulSoup, base_url: str, company: dict) -> list[dict]:
    jobs = []
    for item in soup.select("li.ts-offer-list-item"):
        title_el = item.select_one("a.ts-offer-list-item__title-link")
        if not title_el:
            continue
        title = title_el.get_text(strip=True)
        # Strip "M/F", "M/W/D", "F/H" gender-neutral suffixes from title
        title = re.sub(r"\s+[MFW]/[MFW](?:/[DH])?\s*$", "", title).strip()
        if not title:
            continue

        href = title_el.get("href", "")
        job_url = urljoin(base_url, href)

        # Extract ref ID from href for stable job ID
        id_match = re.search(r"_(\d+)\.aspx$", href)
        job_raw_id = id_match.group(1) if id_match else href[-20:]

        # Location from last description item
        desc = item.select_one("ul.ts-offer-list-item__description")
        address = ""
        if desc:
            desc_items = [li.get_text(strip=True) for li in desc.find_all("li")]
            if desc_items:
                address = desc_items[-1]

        state = _state_from_address(address)
        if not state:
            continue

        location = _format_address(address, state)

        jobs.append({
            "id": f"profils-{company['id']}-{job_raw_id}",
            "company": company["name"],
            "company_id": company["id"],
            "title": title,
            "location": location,
            "state": state,
            "sector": company["sector"],
            "seniority": detect_seniority(title),
            "url": job_url,
            "posted_date": "",
            "source": "profilsorg",
        })
    return jobs


def scrape_profilsorg(company: dict) -> list[dict]:
    cfg = company.get("scraper_config", {})
    subdomain = cfg.get("subdomain", "")
    country_code = cfg.get("country_code", "76")

    if not subdomain:
        print(f"  [profilsorg] {company['name']}: missing subdomain in scraper_config")
        return _fallback(company)

    base_url = f"https://{subdomain}.profils.org"
    first_url = (
        f"{base_url}/job/list-of-all-jobs.aspx"
        f"?changefacet=1&facet_JobCountry={country_code}"
    )

    session = requests.Session()
    session.headers.update(HEADERS)

    # ── Page 1 (sets session cookie with country filter) ─────────────────────
    try:
        r = session.get(first_url, timeout=20)
        r.raise_for_status()
    except Exception as exc:
        print(f"  [profilsorg] {company['name']}: could not reach portal: {exc}")
        return _fallback(company)

    soup = BeautifulSoup(r.text, "lxml")
    total = _parse_total(soup)
    all_jobs = _parse_items(soup, base_url, company)

    n_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE) if total else MAX_PAGES

    # ── Subsequent pages ─────────────────────────────────────────────────────
    for page in range(2, min(n_pages + 1, MAX_PAGES + 1)):
        time.sleep(0.4)
        url = f"{base_url}/job/list-of-all-jobs.aspx?page={page}&LCID=1033"
        try:
            r = session.get(url, timeout=20)
            r.raise_for_status()
        except Exception as exc:
            print(f"  [profilsorg] {company['name']}: page {page} error: {exc}")
            break

        soup = BeautifulSoup(r.text, "lxml")
        items = soup.select("li.ts-offer-list-item")
        if not items:
            break

        all_jobs.extend(_parse_items(soup, base_url, company))

    if not all_jobs:
        print(f"  [profilsorg] {company['name']}: 0 jobs in target states — showing link")
        return _fallback(company)

    print(f"  [profilsorg] {company['name']}: {len(all_jobs)} jobs in target states "
          f"(from {total} US total)")
    return all_jobs


def _fallback(company: dict) -> list[dict]:
    return [
        {
            "id": f"profils-{company['id']}-{s}",
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
