"""
Workday jobs API scraper.

Workday exposes a semi-public JSON endpoint on every tenant's jobs site:
  POST https://{tenant}.{instance}.myworkdayjobs.com/wday/cxs/{tenant}/{board}/jobs

Geographic facet IDs are universal across all Workday tenants.

Strategy:
  1. Try server-side state filtering via locationRegionStateProvince facet (most tenants).
  2. If that returns an error, fall back to US-country filter + client-side text matching.
  3. Client-side matching uses city/state names, not raw state codes, to avoid false positives
     like "DE" appearing in a Montreal street name.
"""

import time
import requests
from .utils import normalize_location, detect_seniority, is_target_location

BOARD_FALLBACKS = ["External", "Careers", "Career", "jobs", "external", "careers"]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Content-Type": "application/json",
    "Accept-Language": "en-US,en;q=0.9",
}

# Universal Workday geographic facet IDs (consistent across all tenants)
US_COUNTRY_ID = "bc33aa3152ec42d4995f4791a106ed09"

TARGET_STATE_IDS = {
    "DC": "0d2bcd0308f541938f3ae29e7cc69ae0",
    "MD": "586509e6daa741808206b095fee97e8f",
    "VA": "e0889a76c58d4fff9b54b80dadc49000",
    "PA": "f620a79f2dc44473828b42881312de2b",
    "WV": "dc5bc08fd91446bdbd108d98a19ac0ff",
    "DE": "18b4cf9ddb4e4542a39614cb55b4dde7",
}

STATE_ID_TO_CODE = {v: k for k, v in TARGET_STATE_IDS.items()}


def _build_url(tenant: str, instance: str, board: str) -> str:
    return (
        f"https://{tenant}.{instance}.myworkdayjobs.com"
        f"/wday/cxs/{tenant}/{board}/jobs"
    )


def _post(url: str, payload: dict) -> dict | None:
    try:
        resp = requests.post(url, json=payload, headers=HEADERS, timeout=20)
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _fetch_all(url: str, facets: dict) -> list[dict]:
    """Paginate through all jobs matching the given facets."""
    all_raw = []
    offset = 0
    limit = 20
    total = 9999

    while offset < total:
        payload = {
            "appliedFacets": facets,
            "limit": limit,
            "offset": offset,
            "searchText": "",
        }
        data = _post(url, payload)
        if not data:
            break
        postings = data.get("jobPostings", [])
        if not postings:
            break
        if offset == 0:
            total = data.get("total", total) or total
        all_raw.extend(postings)
        offset += limit
        if len(postings) < limit:
            break
        if offset < total:
            time.sleep(0.3)

    return all_raw


def _resolve_board_url(tenant: str, instance: str, configured_board: str) -> tuple[str, str] | tuple[None, None]:
    """Try the configured board first, then fall back to common names.
    Returns (url, resolved_board_name)."""
    candidates = [configured_board] + [b for b in BOARD_FALLBACKS if b != configured_board]
    for board in candidates:
        url = _build_url(tenant, instance, board)
        probe = _post(url, {"appliedFacets": {}, "limit": 1, "offset": 0, "searchText": ""})
        if probe is not None:
            total = probe.get("total", 0)
            if total > 0 or board == configured_board:
                return url, board
        time.sleep(0.4)
    return None, None


def _detect_filter_mode(url: str) -> str:
    """
    Detect which geographic filtering this board supports.
    Returns one of: "state", "country", "global"
    """
    # Try full state filter
    data = _post(url, {
        "appliedFacets": {
            "locationCountry": [US_COUNTRY_ID],
            "locationRegionStateProvince": [TARGET_STATE_IDS["VA"]],
        },
        "limit": 1, "offset": 0, "searchText": "",
    })
    if data is not None:
        return "state"

    # Try country-only filter
    data = _post(url, {
        "appliedFacets": {"locationCountry": [US_COUNTRY_ID]},
        "limit": 1, "offset": 0, "searchText": "",
    })
    if data is not None:
        return "country"

    return "global"


def _state_from_location(location_raw: str) -> str | None:
    """Derive state code from a job's locationsText string."""
    return is_target_location(location_raw)


def scrape_workday(company: dict) -> list[dict]:
    cfg = company["scraper_config"]
    tenant = cfg.get("tenant", company["id"].replace("-", ""))
    instance = cfg.get("instance", "wd3")
    board = cfg.get("board", "External")

    url, resolved_board = _resolve_board_url(tenant, instance, board)
    if url is None:
        print(f"  [Workday] {company['name']}: could not reach API — check tenant/board config")
        return []

    state_ids = list(TARGET_STATE_IDS.values())
    mode = _detect_filter_mode(url)

    if mode == "state":
        # Best: server-side state filtering — exact, matches the Workday website
        facets = {
            "locationCountry": [US_COUNTRY_ID],
            "locationRegionStateProvince": state_ids,
        }
    elif mode == "country":
        # OK: US-only reduces fetch volume; text match filters to target states
        facets = {"locationCountry": [US_COUNTRY_ID]}
    else:
        # Fallback: fetch all global jobs, text match filters target states
        facets = {}

    all_raw = _fetch_all(url, facets)

    jobs = []
    seen = set()
    for item in all_raw:
        location_raw = (
            item.get("locationsText", "")
            or item.get("location", "")
            or item.get("jobPostingLocation", "")
            or ""
        )

        if mode == "state":
            # API guarantees it's in one of our states; use text match to identify which
            state = is_target_location(location_raw) or "US"
        else:
            # Must verify via text match
            state = is_target_location(location_raw)
            if not state:
                continue

        external_path = item.get("externalPath", "")
        job_url = (
            f"https://{tenant}.{instance}.myworkdayjobs.com/{resolved_board}{external_path}"
            if external_path else company["careers_url"]
        )

        raw_id = external_path or item.get("title", str(len(seen)))
        job_id = f"wd-{tenant}-{raw_id}"
        if job_id in seen:
            continue
        seen.add(job_id)

        jobs.append({
            "id": job_id,
            "company": company["name"],
            "company_id": company["id"],
            "title": item.get("title", ""),
            "location": normalize_location(location_raw),
            "state": state,
            "sector": company["sector"],
            "seniority": detect_seniority(item.get("title", "")),
            "url": job_url,
            "posted_date": (item.get("startDate") or "")[:10],
            "source": "workday",
        })

    mode_label = {"state": "server-filtered", "country": "us-filtered+text", "global": "global+text"}[mode]
    print(
        f"  [Workday] {company['name']}: {len(jobs)} jobs in target states "
        f"(from {len(all_raw)} fetched, {mode_label})"
    )
    return jobs
