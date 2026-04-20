"""
iCIMS career portal scraper.

iCIMS portals expose a paginated search page at:
  https://{portal}.icims.com/jobs/search?ss=1&in_iframe=1&pageSize=100

The HTML is server-side rendered (no JS required for the listing).
"""

import re
import requests
from urllib.parse import urljoin
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

PAGE_SIZE = 100


def _fetch_page(portal: str, start_row: int = 0) -> BeautifulSoup | None:
    url = (
        f"https://{portal}.icims.com/jobs/search"
        f"?ss=1&pageSize={PAGE_SIZE}&startRow={start_row}"
        f"&searchLocation=&searchKeyword="
    )
    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
        resp.raise_for_status()
        return BeautifulSoup(resp.text, "lxml")
    except Exception as exc:
        print(f"    iCIMS fetch failed ({portal}, row {start_row}): {exc}")
        return None


def _parse_jobs(soup: BeautifulSoup, portal: str, company: dict) -> list[dict]:
    jobs = []
    base_url = f"https://{portal}.icims.com"

    # iCIMS uses consistent class names across versions
    # Try table-based layout first, then list-based
    rows = (
        soup.select("tr.iCIMS_Anchor")
        or soup.select("li.iCIMS_Anchor")
        or soup.select(".iCIMS_JobsTable tr[id]")
        or soup.select(".icims-job-listing")
        or soup.select("tr[class*='iCIMS']")
        or soup.select("li[class*='iCIMS']")
        or soup.select(".iCIMS_JobPositionTitle")
        or soup.select("div[class*='iCIMS_JobsTable'] tr")
    )

    for row in rows:
        # Title + URL
        title_el = row.select_one(".iCIMS_JobTitle a, h3 a, h4 a, .job-title a")
        if not title_el:
            title_el = row.find("a", href=re.compile(r"/jobs/\d+/"))
        if not title_el:
            continue

        title = title_el.get_text(strip=True)
        href = title_el.get("href", "")
        if not href.startswith("http"):
            href = urljoin(base_url, href)

        # Location
        loc_el = (
            row.select_one(".iCIMS_JobLocation, .job-location, .location, td:nth-child(2)")
        )
        location_raw = loc_el.get_text(strip=True) if loc_el else ""

        # Fall back to full row text for state detection
        state = is_target_location(location_raw) or is_target_location(row.get_text(" "))
        if not state:
            continue

        jobs.append({
            "id": f"ic-{portal}-{len(jobs)}-{re.sub(r'[^a-z0-9]', '', title.lower()[:20])}",
            "company": company["name"],
            "company_id": company["id"],
            "title": title,
            "location": normalize_location(location_raw or state),
            "state": state,
            "sector": company["sector"],
            "seniority": detect_seniority(title),
            "url": href,
            "posted_date": "",
            "source": "icims",
        })

    return jobs


def _get_total(soup: BeautifulSoup) -> int:
    """Try to read the total job count from the page."""
    for selector in [".iCIMS_InfoMsg", ".results-count", "span[class*='count']"]:
        el = soup.select_one(selector)
        if el:
            m = re.search(r"(\d[\d,]*)", el.get_text())
            if m:
                return int(m.group(1).replace(",", ""))
    return 0


def scrape_icims(company: dict) -> list[dict]:
    portal = company["scraper_config"].get("portal", company["id"])

    first_page = _fetch_page(portal, start_row=0)
    if first_page is None:
        print(f"  [iCIMS] {company['name']}: could not reach portal")
        return _fallback(company, portal)

    total = _get_total(first_page)
    all_jobs = _parse_jobs(first_page, portal, company)

    # Paginate if needed
    fetched = PAGE_SIZE
    while total > 0 and fetched < total:
        page = _fetch_page(portal, start_row=fetched)
        if page is None:
            break
        all_jobs.extend(_parse_jobs(page, portal, company))
        fetched += PAGE_SIZE

    if not all_jobs:
        print(f"  [iCIMS] {company['name']}: no matching jobs found — showing link")
        return _fallback(company, portal)

    print(f"  [iCIMS] {company['name']}: {len(all_jobs)} jobs in target states")
    return all_jobs


def _fallback(company: dict, portal: str) -> list[dict]:
    url = company.get("careers_url") or f"https://{portal}.icims.com/jobs/search?ss=1"
    return [{
        "id": f"ic-{company['id']}-{s}",
        "company": company["name"],
        "company_id": company["id"],
        "title": "Voir les offres d'emploi →",
        "location": s,
        "state": s,
        "sector": company["sector"],
        "seniority": "N/A",
        "url": url,
        "posted_date": "",
        "source": "manual_link",
    } for s in company["states"]]
