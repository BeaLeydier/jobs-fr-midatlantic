"""
Teamtailor career-site scraper.

Several companies host their careers on Teamtailor at a custom domain
(e.g. jobs.free2move.com, jobs.digital.orange-business.com, talent.arianespace.com).
The job listing page is *server-rendered* HTML — no headless browser needed.

URL structure:
  https://{domain}/jobs                       — page 1 (full HTML)
  https://{domain}/jobs/show_more?page=N      — pages 2+ (Turbo Stream HTML fragment)

DOM structure (both full page and show_more):
  ul#jobs_list_container > li > div > a[href]     — job title + full URL
  div.mt-1 span                                    — Department · Location [· Remote]
    Separator spans have class "mx-[2px]" or text "·"
    Remote-status spans contain one of: Remote, Hybrid, On-site, Hybride, En présentiel

Location detection:
  We run is_target_location() on every non-separator, non-remote-status span text.
  If a span matches a target state, that span text becomes the display location.

scraper_config keys:
  domain  — e.g. "jobs.free2move.com"
"""

import re
import time
import requests
from bs4 import BeautifulSoup
from .utils import detect_seniority, is_target_location, normalize_location

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

MAX_PAGES = 100

# Teamtailor remote-status strings (won't be locations)
_REMOTE_STATUS = frozenset({
    "remote", "hybrid", "on-site", "on site", "hybride", "en présentiel",
    "distanciel", "no preference",
})


def _is_remote_status(text: str) -> bool:
    return text.lower().strip() in _REMOTE_STATUS


def _parse_jobs_from_html(html: str, domain: str, company: dict) -> list[dict]:
    """Extract jobs from a Teamtailor HTML page or Turbo Stream fragment."""
    soup = BeautifulSoup(html, "lxml")
    jobs = []
    base_url = f"https://{domain}"

    # Find all job list items
    items = soup.select("ul#jobs_list_container li, li.job-list-item")
    if not items:
        # Fallback: find any anchor that looks like a job link
        items = []
        for a in soup.find_all("a", href=True):
            href = a.get("href", "")
            if "/jobs/" in href and href.count("/") >= 3:
                items.append(a.find_parent("li") or a)

    for item in items:
        # Job anchor
        link = item.select_one("a[href]") if hasattr(item, "select_one") else item
        if not link:
            continue

        href = link.get("href", "")
        if not href or "/jobs/" not in href:
            continue

        # Build full URL
        if href.startswith("http"):
            job_url = href
        else:
            job_url = base_url + href

        title = link.get_text(strip=True)
        if not title:
            continue

        # Extract ID from URL: /jobs/{id}-{slug}
        id_match = re.search(r"/jobs/(\d+)", href)
        job_id = f"tt-{domain.split('.')[0]}-{id_match.group(1) if id_match else title[:20]}"

        # Location spans: div.mt-1 text-md > span (excluding separators and remote status)
        mt1 = item.select_one("div.mt-1") if hasattr(item, "select_one") else None
        location_raw = ""
        state = None

        if mt1:
            spans = [
                s.get_text(strip=True)
                for s in mt1.find_all("span")
                if s.get_text(strip=True) and s.get_text(strip=True) != "·"
                and "mx-[2px]" not in " ".join(s.get("class", []))
            ]
            # Filter out remote-status tokens
            data_spans = [s for s in spans if not _is_remote_status(s)]
            # Try each span as a location
            for span_text in data_spans:
                detected = is_target_location(span_text)
                if detected:
                    state = detected
                    location_raw = span_text
                    break

        if not state:
            continue

        jobs.append({
            "id": job_id,
            "company": company["name"],
            "company_id": company["id"],
            "title": title,
            "location": normalize_location(location_raw) if location_raw else state,
            "state": state,
            "sector": company["sector"],
            "seniority": detect_seniority(title),
            "url": job_url,
            "posted_date": "",
            "source": "teamtailor",
        })

    return jobs


def scrape_teamtailor(company: dict) -> list[dict]:
    domain = company.get("scraper_config", {}).get("domain", "")
    if not domain:
        print(f"  [teamtailor] {company['name']}: missing domain in scraper_config")
        return _fallback(company)

    all_jobs: list[dict] = []
    seen_ids: set[str] = set()

    # ── Page 1 (full HTML) ──────────────────────────────────────────────────
    url1 = f"https://{domain}/jobs"
    try:
        r = requests.get(url1, headers=HEADERS, timeout=20)
        r.raise_for_status()
    except Exception as exc:
        print(f"  [teamtailor] {company['name']}: could not reach {url1}: {exc}")
        return _fallback(company)

    page1_jobs = _parse_jobs_from_html(r.text, domain, company)
    for j in page1_jobs:
        if j["id"] not in seen_ids:
            seen_ids.add(j["id"])
            all_jobs.append(j)

    # Check if there's a "show more" link
    soup1 = BeautifulSoup(r.text, "lxml")
    has_more = bool(soup1.select_one('a[href*="show_more"]'))

    # ── Subsequent pages (Turbo Stream) ─────────────────────────────────────
    if has_more:
        for page in range(2, MAX_PAGES + 1):
            time.sleep(0.4)
            url_more = f"https://{domain}/jobs/show_more?page={page}"
            try:
                r_more = requests.get(url_more, headers=HEADERS, timeout=20)
                r_more.raise_for_status()
            except Exception as exc:
                print(f"  [teamtailor] {company['name']}: show_more page {page} error: {exc}")
                break

            if not r_more.text.strip():
                break

            page_jobs = _parse_jobs_from_html(r_more.text, domain, company)
            if not page_jobs and len(r_more.text) < 500:
                break  # empty page = end of list

            new = 0
            for j in page_jobs:
                if j["id"] not in seen_ids:
                    seen_ids.add(j["id"])
                    all_jobs.append(j)
                    new += 1

            if new == 0:
                break  # no new jobs = done

    if not all_jobs:
        print(f"  [teamtailor] {company['name']}: 0 jobs in target states — showing link")
        return _fallback(company)

    print(f"  [teamtailor] {company['name']}: {len(all_jobs)} jobs in target states")
    return all_jobs


def _fallback(company: dict) -> list[dict]:
    return [
        {
            "id": f"tt-{company['id']}-{s}",
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
