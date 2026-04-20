"""
SAP SuccessFactors career portal scraper.

SuccessFactors career sites expose a public job-search action used by
their own frontend. We try two common endpoint patterns and fall back
to a manual careers link if both fail (SF sites vary by version/instance).

Endpoint (v1):  GET  https://{host}/career?action=getAllJobsFilter&company={id}&country=US&lang=en_US
Endpoint (v2):  POST https://{host}/restws/v3/jobboard/searchjobs
"""

import requests
from .utils import normalize_location, detect_seniority, is_target_location

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/javascript, */*",
    "X-Requested-With": "XMLHttpRequest",
}


def _try_v1(host: str, company_id: str) -> list | None:
    """Try the classic getAllJobsFilter action (SuccessFactors v1 career sites)."""
    url = f"https://{host}/career"
    params = {
        "action": "getAllJobsFilter",
        "company": company_id,
        "country": "US",
        "lang": "en_US",
        "pageSize": 200,
    }
    try:
        resp = requests.get(url, params=params, headers=HEADERS, timeout=20)
        if resp.status_code != 200:
            return None
        data = resp.json()
        # Response is either {"jobs": [...]} or a raw list
        if isinstance(data, list):
            return data
        return data.get("jobs") or data.get("result") or None
    except Exception:
        return None


def _try_v2(host: str, company_id: str) -> list | None:
    """Try the newer restws searchjobs endpoint (SuccessFactors v2 career sites)."""
    url = f"https://{host}/restws/v3/jobboard/searchjobs"
    payload = {
        "company": company_id,
        "lang": "en_US",
        "country": ["US"],
        "pageSize": 200,
        "currentPage": 0,
    }
    try:
        resp = requests.post(url, json=payload, headers=HEADERS, timeout=20)
        if resp.status_code != 200:
            return None
        data = resp.json()
        return data.get("jobPostings") or data.get("jobs") or None
    except Exception:
        return None


def scrape_successfactors(company: dict) -> list[dict]:
    cfg = company["scraper_config"]
    host = cfg.get("host", "career5.successfactors.eu")
    company_id = cfg.get("company_id", company["id"])

    raw_jobs = _try_v1(host, company_id) or _try_v2(host, company_id)

    if not raw_jobs:
        print(f"  [SuccessFactors] {company['name']}: API unreachable — showing link")
        return [{
            "id": f"sf-{company['id']}-{s}",
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

    jobs = []
    for item in raw_jobs:
        # SF field names vary between instances
        location_raw = (
            item.get("location") or item.get("jobLocation") or
            item.get("city", "") + ", " + item.get("stateProvince", "")
        ).strip(", ")

        state = is_target_location(location_raw)
        if not state:
            continue

        # Build apply URL
        job_id = item.get("jobReqId") or item.get("id") or ""
        apply_url = (
            item.get("applyUrl") or item.get("externalApplyUrl") or
            f"https://{host}/careers/{company_id}/job/{job_id}" if job_id else company["careers_url"]
        )

        title = item.get("externalTitle") or item.get("title") or item.get("jobTitle") or ""

        jobs.append({
            "id": f"sf-{company_id}-{job_id or len(jobs)}",
            "company": company["name"],
            "company_id": company["id"],
            "title": title,
            "location": normalize_location(location_raw),
            "state": state,
            "sector": company["sector"],
            "seniority": detect_seniority(title),
            "url": apply_url,
            "posted_date": (item.get("postingDate") or item.get("startDate") or "")[:10],
            "source": "successfactors",
        })

    print(f"  [SuccessFactors] {company['name']}: {len(jobs)} jobs in target states")
    return jobs
