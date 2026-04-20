"""
Generic HTML careers page scraper — fallback for companies without a structured API.

Falls back to a direct careers page link if no real job listings are found.
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

# Short state codes are prone to false positives (e.g. "PA" in "Japan", "VA" in "evaluation").
# Require them to appear in address-like context: "City, PA" or ", PA " or "(PA)" or "PA " at start.
_SHORT_STATE_PATTERN = re.compile(
    r'(?:,\s*|\(|\bIN\s+)(DC|MD|VA|PA|WV|DE)\b', re.I
)

# Long names are safe to match anywhere
_LONG_STATE_PATTERN = re.compile(
    r'\b(District of Columbia|Maryland|Virginia|Pennsylvania|West Virginia|Delaware)\b', re.I
)

_LONG_TO_CODE = {
    "district of columbia": "DC",
    "maryland": "MD",
    "virginia": "VA",
    "pennsylvania": "PA",
    "west virginia": "WV",
    "delaware": "DE",
}

# Selectors for job listing containers — ordered most-specific to least
JOB_CONTAINER_SELECTORS = [
    "li[class*='job']", "li[class*='position']", "li[class*='opening']",
    "article[class*='job']", "article[class*='career']",
    "div[class*='job-item']", "div[class*='job-card']", "div[class*='job-listing']",
    "tr[class*='job']",
    # Broad fallbacks — only used if nothing else matched
    "li.result", "li.opening",
]


def _strict_location_match(text: str) -> str | None:
    """
    Return a state code only when the state appears in a genuine location context.
    Long names match anywhere; short codes require address-like context.
    """
    m = _LONG_STATE_PATTERN.search(text)
    if m:
        return _LONG_TO_CODE.get(m.group(1).lower())
    m = _SHORT_STATE_PATTERN.search(text)
    if m:
        return m.group(1).upper()
    return None


def _find_location_element(container) -> str:
    """Try common location element patterns within a job container."""
    for selector in [
        "[class*='location']", "[class*='city']", "[class*='office']",
        "span[class*='loc']", "td:nth-child(2)", "td:nth-child(3)",
        "p", "small",
    ]:
        el = container.select_one(selector)
        if el:
            txt = el.get_text(strip=True)
            if txt and len(txt) < 100:
                return txt
    return ""


def scrape_generic(company: dict) -> list[dict]:
    careers_url = company["careers_url"]
    jobs = []

    try:
        resp = requests.get(careers_url, headers=HEADERS, timeout=15, allow_redirects=True)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")

        # Try specific job container selectors first
        containers = []
        for sel in JOB_CONTAINER_SELECTORS:
            containers = soup.select(sel)
            if len(containers) >= 2:
                break

        for container in containers[:100]:
            # Find the job title link
            link = container.find("a", href=True)
            if not link:
                continue
            title = link.get_text(strip=True)
            if not title or len(title) < 5 or len(title) > 160:
                continue

            # Try dedicated location element first, then full container text
            location_raw = _find_location_element(container)
            state = (
                _strict_location_match(location_raw)
                or _strict_location_match(container.get_text(" ", strip=True))
            )
            if not state:
                continue

            href = link["href"]
            if not href.startswith("http"):
                href = urljoin(careers_url, href)

            # Skip if href is just the careers page itself (nav links, etc.)
            if href.rstrip("/") == careers_url.rstrip("/"):
                continue

            jobs.append({
                "id": f"gen-{company['id']}-{len(jobs)}",
                "company": company["name"],
                "company_id": company["id"],
                "title": title,
                "location": normalize_location(location_raw or state),
                "state": state,
                "sector": company["sector"],
                "seniority": detect_seniority(title),
                "url": href,
                "posted_date": "",
                "source": "generic",
            })

    except Exception as exc:
        print(f"  [Generic] {company['name']}: scraping failed — {exc}")

    if not jobs:
        print(f"  [Generic] {company['name']}: no jobs parsed — link to careers page shown")
        return [{
            "id": f"gen-{company['id']}-{s}",
            "company": company["name"],
            "company_id": company["id"],
            "title": "Voir les offres d'emploi →",
            "location": s,
            "state": s,
            "sector": company["sector"],
            "seniority": "N/A",
            "url": careers_url,
            "posted_date": "",
            "source": "manual_link",
        } for s in company["states"]]

    print(f"  [Generic] {company['name']}: {len(jobs)} jobs in target states")
    return jobs
