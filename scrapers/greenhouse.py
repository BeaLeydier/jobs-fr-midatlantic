"""
Greenhouse public job board API scraper.
Docs: https://developers.greenhouse.io/job-board.html
"""

import requests
from .utils import normalize_location, detect_seniority, is_target_location

BASE_URL = "https://boards-api.greenhouse.io/v1/boards/{token}/jobs"


def scrape_greenhouse(company: dict) -> list[dict]:
    token = company["scraper_config"].get("board_token", company["id"])
    url = BASE_URL.format(token=token)

    try:
        resp = requests.get(url, params={"content": "true"}, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        print(f"  [Greenhouse] {company['name']}: request failed — {exc}")
        return []

    jobs = []
    for item in data.get("jobs", []):
        location_raw = item.get("location", {}).get("name", "") or ""
        state = is_target_location(location_raw)
        if not state:
            continue

        jobs.append(
            {
                "id": f"gh-{item['id']}",
                "company": company["name"],
                "company_id": company["id"],
                "title": item.get("title", ""),
                "location": normalize_location(location_raw),
                "state": state,
                "sector": company["sector"],
                "seniority": detect_seniority(item.get("title", "")),
                "url": item.get("absolute_url", ""),
                "posted_date": (item.get("updated_at") or "")[:10],
                "source": "greenhouse",
            }
        )

    print(f"  [Greenhouse] {company['name']}: {len(jobs)} jobs in target states")
    return jobs
