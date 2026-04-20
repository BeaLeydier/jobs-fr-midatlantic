"""
Jobvite HTML careers page scraper.

The Jobvite v2 JSON API requires an API key (returns 401 without one).
Instead we scrape the public HTML jobs listing page at:
  https://jobs.jobvite.com/{company_id}/jobs
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

BASE = "https://jobs.jobvite.com"


def scrape_jobvite(company: dict) -> list[dict]:
    company_id = company["scraper_config"].get("company_id", company["id"])
    url = f"{BASE}/{company_id}/jobs"

    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")
    except Exception as exc:
        print(f"  [Jobvite] {company['name']}: fetch failed — {exc}")
        return []

    jobs = []

    # Jobvite HTML structure: table rows or list items with class jv-job-list-*
    rows = (
        soup.select("tr.jv-job-list-name, li.jv-job-list-name")
        or soup.select("tr[class*='job'], li[class*='job']")
        or soup.select(".jv-job-list-item")
    )

    # Fallback: any <a> inside a table/list that looks like a job posting
    if not rows:
        rows = soup.select("table tr, ul.jobs li")

    for row in rows:
        link = row.find("a", href=True)
        if not link:
            continue
        title = link.get_text(strip=True)
        if not title or len(title) < 4:
            continue

        href = link["href"]
        if not href.startswith("http"):
            href = urljoin(url, href)

        # Location is usually in a sibling <td> or <span>
        loc_el = row.find(class_=re.compile(r"location|city|state", re.I))
        if not loc_el:
            # Try second/third td
            tds = row.find_all("td")
            loc_el = tds[1] if len(tds) > 1 else None

        location_raw = loc_el.get_text(strip=True) if loc_el else ""
        state = is_target_location(location_raw)

        # Also check full row text as fallback
        if not state:
            state = is_target_location(row.get_text(" "))
        if not state:
            continue

        jobs.append({
            "id": f"jv-{company_id}-{len(jobs)}",
            "company": company["name"],
            "company_id": company["id"],
            "title": title[:120],
            "location": normalize_location(location_raw or state),
            "state": state,
            "sector": company["sector"],
            "seniority": detect_seniority(title),
            "url": href,
            "posted_date": "",
            "source": "jobvite",
        })

    if not jobs:
        print(f"  [Jobvite] {company['name']}: no matching jobs — showing link")
        return [{
            "id": f"jv-{company['id']}-{s}",
            "company": company["name"],
            "company_id": company["id"],
            "title": "Voir les offres d'emploi →",
            "location": s,
            "state": s,
            "sector": company["sector"],
            "seniority": "N/A",
            "url": company["careers_url"],
            "posted_date": "",
            "source": "manual_link",
        } for s in company["states"]]

    print(f"  [Jobvite] {company['name']}: {len(jobs)} jobs in target states")
    return jobs
