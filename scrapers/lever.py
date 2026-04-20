"""
Lever public postings API scraper.
Docs: https://hire.lever.co/developer/postings
"""

import requests
from .utils import normalize_location, detect_seniority, is_target_location

BASE_URL = "https://api.lever.co/v0/postings/{company}"


def scrape_lever(company: dict) -> list[dict]:
    company_id = company["scraper_config"].get("company_id", company["id"])
    url = BASE_URL.format(company=company_id)

    try:
        resp = requests.get(
            url, params={"mode": "json", "limit": 500}, timeout=15
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        print(f"  [Lever] {company['name']}: request failed — {exc}")
        return []

    jobs = []
    for item in data:
        categories = item.get("categories", {})
        location_raw = categories.get("location") or item.get("workplaceType") or ""

        # Lever jobs often have location in categories or the posting text
        # Also check the commitment/team fields
        if not location_raw:
            location_raw = item.get("text", "")

        state = is_target_location(location_raw)
        if not state:
            # Try searching posting description for location hints
            desc = item.get("descriptionPlain", "") or ""
            state = is_target_location(desc[:500])
            if not state:
                continue

        jobs.append(
            {
                "id": f"lv-{item['id']}",
                "company": company["name"],
                "company_id": company["id"],
                "title": item.get("text", ""),
                "location": normalize_location(location_raw),
                "state": state,
                "sector": company["sector"],
                "seniority": detect_seniority(item.get("text", "")),
                "url": item.get("hostedUrl", item.get("applyUrl", "")),
                "posted_date": "",
                "source": "lever",
            }
        )

    print(f"  [Lever] {company['name']}: {len(jobs)} jobs in target states")
    return jobs
