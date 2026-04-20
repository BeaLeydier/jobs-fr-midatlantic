"""
SmartRecruiters public API scraper.
"""

import requests
from .utils import normalize_location, detect_seniority, is_target_location

BASE_URL = "https://api.smartrecruiters.com/v1/companies/{company}/postings"

TARGET_STATE_CODES = ["DC", "MD", "VA", "PA", "WV", "DE"]
US_STATE_MAP = {
    "DC": "District of Columbia",
    "MD": "Maryland",
    "VA": "Virginia",
    "PA": "Pennsylvania",
    "WV": "West Virginia",
    "DE": "Delaware",
}


def scrape_smartrecruiters(company: dict) -> list[dict]:
    company_id = company["scraper_config"].get("company_id", company["id"])
    jobs = []

    for state_code in TARGET_STATE_CODES:
        url = BASE_URL.format(company=company_id)
        params = {
            "country": "us",
            "region": US_STATE_MAP[state_code],
            "limit": 100,
            "status": "PUBLIC",
        }

        try:
            resp = requests.get(url, params=params, timeout=15)
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            print(f"  [SmartRecruiters] {company['name']} ({state_code}): request failed — {exc}")
            continue

        for item in data.get("content", []):
            location_raw = ""
            loc = item.get("location", {})
            if loc:
                parts = [loc.get("city", ""), loc.get("region", ""), loc.get("country", "")]
                location_raw = ", ".join(p for p in parts if p)

            # Verify state match
            state = is_target_location(location_raw)
            if not state:
                state = state_code  # trust the API filter

            jobs.append(
                {
                    "id": f"sr-{item['id']}",
                    "company": company["name"],
                    "company_id": company["id"],
                    "title": item.get("name", ""),
                    "location": normalize_location(location_raw),
                    "state": state,
                    "sector": company["sector"],
                    "seniority": detect_seniority(item.get("name", "")),
                    "url": f"https://jobs.smartrecruiters.com/{company_id}/{item['id']}",
                    "posted_date": (item.get("releasedDate") or "")[:10],
                    "source": "smartrecruiters",
                }
            )

    # Deduplicate by job id
    seen = set()
    unique_jobs = []
    for job in jobs:
        if job["id"] not in seen:
            seen.add(job["id"])
            unique_jobs.append(job)

    print(f"  [SmartRecruiters] {company['name']}: {len(unique_jobs)} jobs in target states")
    return unique_jobs
