"""Wires every layer together. One Services object is shared by the API, the
orchestrator and the background jobs; tests build one with fakes."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from agency.config import Settings, get_settings
from agency.integrations.ads import AdsRegistry
from agency.integrations.leadsie import Leadsie
from agency.integrations.messaging import ClientMessenger, Slack
from agency.integrations.payments import Payments
from agency.memory.brand_brain import BrandBrain, build_brand_brain
from agency.memory.repository import Repository, build_repository
from agency.models import SiteFindings
from agency.session import SessionStore, build_session_store
from agency.workers.scraper import scan_site


@dataclass
class Services:
    settings: Settings
    llm: Any  # anthropic.Anthropic, or a fake in tests
    sessions: SessionStore
    repo: Repository
    brand: BrandBrain
    payments: Payments
    leadsie: Leadsie
    ads: AdsRegistry
    messenger: ClientMessenger
    slack: Slack
    scan_site: Callable[[str], SiteFindings] = field(default=scan_site)


def build_services(settings: Optional[Settings] = None, llm: Any = None) -> Services:
    settings = settings or get_settings()
    if llm is None:
        import anthropic

        llm = anthropic.Anthropic()
    return Services(
        settings=settings,
        llm=llm,
        sessions=build_session_store(settings.redis_url),
        repo=build_repository(settings.database_url),
        brand=build_brand_brain(settings.database_url, settings.voyage_api_key),
        payments=Payments(settings),
        leadsie=Leadsie(settings),
        ads=AdsRegistry(settings),
        messenger=ClientMessenger(settings),
        slack=Slack(settings),
    )
