from .workday import scrape_workday
from .smartrecruiters import scrape_smartrecruiters
from .icims import scrape_icims
from .jobvite import scrape_jobvite
from .j2w import scrape_j2w
from .engie import scrape_engie
from .teamtailor import scrape_teamtailor
from .profilsorg import scrape_profilsorg
from .jobspy import scrape_jobspy
from .generic import scrape_generic

__all__ = [
    "scrape_workday",
    "scrape_smartrecruiters",
    "scrape_icims",
    "scrape_jobvite",
    "scrape_j2w",
    "scrape_engie",
    "scrape_teamtailor",
    "scrape_profilsorg",
    "scrape_jobspy",
    "scrape_generic",
]
