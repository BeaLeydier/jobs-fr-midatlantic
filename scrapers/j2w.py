"""
SuccessFactors Job2Web (j2w) scraper.

Several French companies host their careers site on SAP SuccessFactors' Job2Web
platform using custom company domains (e.g. jobs.arkema.com, jobs.cmacgm-group.com).
The search results page is server-rendered HTML — no headless browser needed.

Search URL:
  https://{domain}/search/?q=&locationsearch=United+States&pagesize=100&startrow=N

DOM structure:
  tr.data-row                  — one row per job
  a.jobTitle-link              — job title (text) + relative href (/job/City-Title/id/)
  td.colLocation .jobLocation  — location ("City, ST, US" or "City, ST, US, ZIP")
  span.paginationLabel         — "Results 1 – 20 of 88"  (used to track total)

Pagination:
  The site caps results per page differently per tenant (typically 20 or 25).
  We read the actual row count from the first response, then step startrow by that
  amount until startrow >= total extracted from paginationLabel.
"""

import re
import time
import requests
from urllib.parse import urljoin
from bs4 import BeautifulSoup
from .utils import normalize_location, detect_seniority, is_target_location

# j2w locations: "City, ST, US" or "City, ST, US, ZIP"
_J2W_LOC_RE = re.compile(
    r"^(.+?),\s*([A-Z]{2}),\s*US(?:,\s*[\d\-]+)?$", re.IGNORECASE
)


def _format_j2w_location(raw: str) -> str:
    """Convert 'King of Prussia, PA, US, 19406' → 'King Of Prussia, PA'."""
    m = _J2W_LOC_RE.match(raw.strip())
    if m:
        city, state = m.group(1).strip(), m.group(2).upper()
        return f"{city.title()}, {state}"
    return normalize_location(raw)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

MAX_PAGES = 50   # safety cap (~50 × 100 = 5 000 jobs max)

_TOTAL_RE = re.compile(r"of\s*[\xa0]*([\d,]+)", re.IGNORECASE)


def _fetch_page(domain: str, start_row: int) -> BeautifulSoup | None:
    url = (
        f"https://{domain}/search/"
        f"?q=&locationsearch=United+States&pagesize=100&startrow={start_row}"
    )
    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
        resp.raise_for_status()
        return BeautifulSoup(resp.text, "lxml")
    except Exception as exc:
        print(f"    [j2w] fetch error ({domain}, startrow={start_row}): {exc}")
        return None


def _parse_total(soup: BeautifulSoup) -> int:
    """Extract total job count from paginationLabel, e.g. 'Results 1 – 20 of 88'."""
    el = soup.select_one(".paginationLabel")
    if el:
        m = _TOTAL_RE.search(el.get_text())
        if m:
            return int(m.group(1).replace(",", ""))
    return 0


def _parse_rows(soup: BeautifulSoup, domain: str, company: dict) -> list[dict]:
    jobs = []
    base_url = f"https://{domain}"

    for row in soup.select("tr.data-row"):
        # Title + URL (hidden-phone version only to avoid duplicates)
        link = row.select_one("span.hidden-phone a.jobTitle-link, a.jobTitle-link")
        if not link:
            continue
        title = link.get_text(strip=True)
        # Strip trailing " Job" suffix that j2w appends to many titles
        title = re.sub(r"\s+Job$", "", title).strip()
        if not title:
            continue

        href = link.get("href", "")
        job_url = urljoin(base_url, href) if href else company["careers_url"]

        # Location — prefer the hidden-phone (desktop) column, fall back to inline
        loc_el = row.select_one("td.colLocation .jobLocation, .jobLocation")
        location_raw = loc_el.get_text(strip=True) if loc_el else ""

        state = is_target_location(location_raw)
        if not state:
            continue

        # Stable ID from the job URL path (contains numeric job ID)
        job_id_match = re.search(r"/(\d+)/?$", href)
        job_id = f"j2w-{domain.split('.')[0]}-{job_id_match.group(1) if job_id_match else len(jobs)}"

        jobs.append({
            "id": job_id,
            "company": company["name"],
            "company_id": company["id"],
            "title": title,
            "location": _format_j2w_location(location_raw),
            "state": state,
            "sector": company["sector"],
            "seniority": detect_seniority(title),
            "url": job_url,
            "posted_date": "",
            "source": "j2w",
        })

    return jobs


def scrape_j2w(company: dict) -> list[dict]:
    domain = company["scraper_config"].get("domain", "")
    if not domain:
        print(f"  [j2w] {company['name']}: missing domain in scraper_config")
        return _fallback(company)

    # ── First page ──────────────────────────────────────────────────────────
    soup = _fetch_page(domain, start_row=0)
    if soup is None:
        print(f"  [j2w] {company['name']}: could not reach portal")
        return _fallback(company)

    first_rows = soup.select("tr.data-row")
    if not first_rows:
        print(f"  [j2w] {company['name']}: no job rows found — showing link")
        return _fallback(company)

    page_size = len(first_rows)      # actual rows per page for this tenant
    total = _parse_total(soup)       # total US jobs advertised

    all_jobs = _parse_rows(soup, domain, company)

    # ── Subsequent pages ────────────────────────────────────────────────────
    start_row = page_size
    for _ in range(MAX_PAGES - 1):
        if total > 0 and start_row >= total:
            break

        time.sleep(0.3)
        soup = _fetch_page(domain, start_row)
        if soup is None:
            break

        rows = soup.select("tr.data-row")
        if not rows:
            break

        all_jobs.extend(_parse_rows(soup, domain, company))
        start_row += len(rows)

        if len(rows) < page_size:
            break   # last partial page

    if not all_jobs:
        print(f"  [j2w] {company['name']}: 0 jobs in target states — showing link")
        return _fallback(company)

    print(f"  [j2w] {company['name']}: {len(all_jobs)} jobs in target states "
          f"(from {total} US total)")
    return all_jobs


def _fallback(company: dict) -> list[dict]:
    return [
        {
            "id": f"j2w-{company['id']}-{s}",
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
