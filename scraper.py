#!/usr/bin/env python3
"""
Main job scraper orchestrator.

Usage:
    python scraper.py                   # scrape all companies
    python scraper.py --company airbus  # scrape a single company
    python scraper.py --dry-run         # print counts without writing

Output: data/jobs.json
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from scrapers import (
    scrape_workday,
    scrape_smartrecruiters,
    scrape_icims,
    scrape_jobvite,
    scrape_successfactors,
    scrape_generic,
)

ROOT = Path(__file__).parent
COMPANIES_FILE = ROOT / "data" / "companies.json"
JOBS_FILE = ROOT / "data" / "jobs.json"

SCRAPER_MAP = {
    "workday": scrape_workday,
    "smartrecruiters": scrape_smartrecruiters,
    "icims": scrape_icims,
    "jobvite": scrape_jobvite,
    "successfactors": scrape_successfactors,
    "generic": scrape_generic,
    "manual": scrape_generic,
}

# Seconds to wait between companies to avoid hammering servers
INTER_COMPANY_DELAY = 1.5


def load_companies(filter_id: str | None = None) -> list[dict]:
    with open(COMPANIES_FILE) as f:
        companies = json.load(f)
    if filter_id:
        companies = [c for c in companies if c["id"] == filter_id]
        if not companies:
            print(f"ERROR: No company with id '{filter_id}' found in companies.json")
            sys.exit(1)
    return companies


def scrape_company(company: dict) -> list[dict]:
    scraper_type = company.get("scraper_type", "generic")
    scraper_fn = SCRAPER_MAP.get(scraper_type, scrape_generic)
    try:
        return scraper_fn(company)
    except Exception as exc:
        print(f"  ERROR scraping {company['name']} ({scraper_type}): {exc}")
        return []


def deduplicate(jobs: list[dict]) -> list[dict]:
    """Remove duplicate jobs by (company_id, title, location)."""
    seen: set[tuple] = set()
    unique = []
    for job in jobs:
        key = (job["company_id"], job["title"].lower().strip(), job["location"].lower().strip())
        if key not in seen:
            seen.add(key)
            unique.append(job)
    return unique


def main():
    parser = argparse.ArgumentParser(description="Scrape French company job postings")
    parser.add_argument("--company", help="Scrape only this company id")
    parser.add_argument("--dry-run", action="store_true", help="Don't write output file")
    args = parser.parse_args()

    companies = load_companies(filter_id=args.company)
    print(f"Scraping {len(companies)} company/companies…\n")

    all_jobs: list[dict] = []
    errors: list[str] = []

    for i, company in enumerate(companies, 1):
        print(f"[{i}/{len(companies)}] {company['name']} ({company['scraper_type']})")
        jobs = scrape_company(company)
        all_jobs.extend(jobs)
        if i < len(companies):
            time.sleep(INTER_COMPANY_DELAY)

    all_jobs = deduplicate(all_jobs)

    # Sort: state → company → title
    all_jobs.sort(key=lambda j: (j["state"], j["company"], j["title"]))

    print(f"\nTotal: {len(all_jobs)} unique jobs across {len(companies)} companies")
    if errors:
        print(f"Errors: {', '.join(errors)}")

    if args.dry_run:
        print("(dry run — not writing jobs.json)")
        return

    # Load existing jobs to merge (preserve jobs from companies not scraped this run)
    existing_jobs: list[dict] = []
    if JOBS_FILE.exists() and args.company:
        with open(JOBS_FILE) as f:
            data = json.load(f)
        existing_jobs = [
            j for j in data.get("jobs", [])
            if j["company_id"] != args.company
        ]

    final_jobs = existing_jobs + all_jobs
    final_jobs = deduplicate(final_jobs)
    final_jobs.sort(key=lambda j: (j["state"], j["company"], j["title"]))

    output = {
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "total": len(final_jobs),
        "jobs": final_jobs,
    }

    JOBS_FILE.parent.mkdir(exist_ok=True)
    with open(JOBS_FILE, "w") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"Written to {JOBS_FILE} ({len(final_jobs)} jobs)")


if __name__ == "__main__":
    main()
