from .workday import scrape_workday
from .smartrecruiters import scrape_smartrecruiters
from .icims import scrape_icims
from .jobvite import scrape_jobvite
from .successfactors import scrape_successfactors
from .generic import scrape_generic

__all__ = [
    "scrape_workday",
    "scrape_smartrecruiters",
    "scrape_icims",
    "scrape_jobvite",
    "scrape_successfactors",
    "scrape_generic",
]
