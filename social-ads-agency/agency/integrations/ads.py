"""Layer 6: Meta and TikTok Marketing APIs.

Three operations, and only three:
  create_paused  builds the campaign tree with every object PAUSED / DISABLE
  set_status     turns it on (only ever called from the Slack approval handler)
                 or off (also called by the kill-switch)
  today_metrics  reads today's numbers for the kill-switch and for reporting

Nothing here is reachable from the model directly. The orchestrator's tools
can only create a *draft* and ask a human; see orchestrator/tools.py.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import date
from decimal import Decimal
from typing import Any, Protocol

import httpx2 as httpx

from agency.config import Settings
from agency.models import AdCreative, Campaign
from agency.workers.killswitch import Metrics

log = logging.getLogger(__name__)


class AdPlatform(Protocol):
    def create_paused(self, campaign: Campaign, creatives: list[AdCreative]) -> dict[str, str]: ...
    def set_status(self, campaign: Campaign, active: bool) -> None: ...
    def today_metrics(self, campaign: Campaign) -> Metrics: ...


def _to_minor(major: Any) -> int:
    return int((Decimal(str(major or "0")) * 100).quantize(Decimal("1")))


# ------------------------------------------------------------------ Meta

META_CONVERSION_ACTIONS = {
    "OUTCOME_LEADS": ("lead", "offsite_conversion.fb_pixel_lead", "onsite_conversion.lead_grouped"),
    "OUTCOME_SALES": ("purchase", "offsite_conversion.fb_pixel_purchase", "omni_purchase"),
    "OUTCOME_TRAFFIC": ("landing_page_view",),
    "OUTCOME_AWARENESS": (),
}


class MetaAds:
    def __init__(self, settings: Settings, client: httpx.Client | None = None) -> None:
        self.s = settings
        self.http = client or httpx.Client(timeout=30.0)
        self.base = f"https://graph.facebook.com/{settings.meta_api_version}"

    def _post(self, path: str, data: dict[str, Any]) -> dict[str, Any]:
        form = {k: json.dumps(v) if isinstance(v, (dict, list)) else str(v) for k, v in data.items()}
        form["access_token"] = self.s.meta_access_token
        resp = self.http.post(f"{self.base}/{path}", data=form)
        if resp.status_code >= 400:
            raise RuntimeError(f"Meta API {path}: {resp.status_code} {resp.text[:500]}")
        return resp.json()

    def create_paused(self, campaign: Campaign, creatives: list[AdCreative]) -> dict[str, str]:
        d = campaign.draft
        act = campaign.external_ids["ad_account"]
        if not act.startswith("act_"):
            act = f"act_{act}"
        ids: dict[str, str] = {"ad_account": act}
        ids["campaign"] = self._post(f"{act}/campaigns", {
            "name": d.name,
            "objective": d.objective,
            "status": "PAUSED",
            "special_ad_categories": [],
            "daily_budget": d.daily_budget_minor,  # campaign budget optimization
            "bid_strategy": "LOWEST_COST_WITHOUT_CAP",
        })["id"]
        optimization = {"OUTCOME_TRAFFIC": "LANDING_PAGE_VIEWS", "OUTCOME_AWARENESS": "REACH"}.get(
            d.objective, "LINK_CLICKS")
        ids["adset"] = self._post(f"{act}/adsets", {
            "name": f"{d.name} | core",
            "campaign_id": ids["campaign"],
            "billing_event": "IMPRESSIONS",
            "optimization_goal": optimization,
            "targeting": {"geo_locations": {"countries": d.countries}, "age_min": d.age_min, "age_max": d.age_max},
            "status": "PAUSED",
        })["id"]
        for i, cr in enumerate(creatives):
            creative_id = self._post(f"{act}/adcreatives", {
                "name": f"{d.name} | {cr.angle[:40]}",
                "object_story_spec": {
                    "page_id": self.s.meta_page_id,
                    "link_data": {
                        "link": d.landing_url,
                        "message": cr.primary_text,
                        "name": cr.headline,
                        "call_to_action": {"type": cr.call_to_action, "value": {"link": d.landing_url}},
                    },
                },
            })["id"]
            ids[f"ad_{i}"] = self._post(f"{act}/ads", {
                "name": f"{d.name} | ad {i + 1}",
                "adset_id": ids["adset"],
                "creative": {"creative_id": creative_id},
                "status": "PAUSED",
            })["id"]
        return ids

    def set_status(self, campaign: Campaign, active: bool) -> None:
        status = "ACTIVE" if active else "PAUSED"
        keys = ["campaign"] if not active else [k for k in campaign.external_ids if k != "ad_account"]
        for k in keys:
            self._post(campaign.external_ids[k], {"status": status})

    def today_metrics(self, campaign: Campaign) -> Metrics:
        resp = self.http.get(f"{self.base}/{campaign.external_ids['campaign']}/insights", params={
            "fields": "spend,impressions,clicks,frequency,actions",
            "date_preset": "today",
            "access_token": self.s.meta_access_token,
        })
        resp.raise_for_status()
        rows = resp.json().get("data", [])
        if not rows:
            return Metrics()
        r = rows[0]
        wanted = META_CONVERSION_ACTIONS.get(campaign.draft.objective, ())
        conversions = max(
            [int(float(a.get("value", 0))) for a in r.get("actions", []) or [] if a.get("action_type") in wanted],
            default=0,
        )
        return Metrics(spend_minor=_to_minor(r.get("spend")), impressions=int(r.get("impressions", 0)),
                       clicks=int(r.get("clicks", 0)), conversions=conversions,
                       frequency=float(r.get("frequency", 0) or 0))


# ------------------------------------------------------------------ TikTok

TIKTOK_OBJECTIVE = {
    "OUTCOME_LEADS": "LEAD_GENERATION",
    "OUTCOME_SALES": "WEB_CONVERSIONS",
    "OUTCOME_TRAFFIC": "TRAFFIC",
    "OUTCOME_AWARENESS": "REACH",
}


class TikTokAds:
    """Creates the campaign shell (DISABLE). TikTok ad groups need TikTok's own
    location ids and ads need uploaded video, so those are built by the
    strategist in Ads Manager during review, or extend create_paused here."""

    BASE = "https://business-api.tiktok.com/open_api/v1.3"

    def __init__(self, settings: Settings, client: httpx.Client | None = None) -> None:
        self.s = settings
        self.http = client or httpx.Client(timeout=30.0)

    def _call(self, method: str, path: str, body: dict[str, Any]) -> dict[str, Any]:
        headers = {"Access-Token": self.s.tiktok_access_token}
        if method == "GET":
            params = {k: json.dumps(v) if isinstance(v, (list, dict)) else v for k, v in body.items()}
            resp = self.http.get(f"{self.BASE}{path}", params=params, headers=headers)
        else:
            resp = self.http.post(f"{self.BASE}{path}", json=body, headers=headers)
        data = resp.json()
        if resp.status_code >= 400 or data.get("code") != 0:
            raise RuntimeError(f"TikTok API {path}: {data.get('code')} {data.get('message')}")
        return data.get("data", {})

    def create_paused(self, campaign: Campaign, creatives: list[AdCreative]) -> dict[str, str]:
        d = campaign.draft
        adv = campaign.external_ids["ad_account"]
        data = self._call("POST", "/campaign/create/", {
            "advertiser_id": adv,
            "campaign_name": d.name,
            "objective_type": TIKTOK_OBJECTIVE.get(d.objective, "TRAFFIC"),
            "budget_mode": "BUDGET_MODE_DAY",
            "budget": float(Decimal(d.daily_budget_minor) / 100),
            "operation_status": "DISABLE",
        })
        return {"ad_account": adv, "campaign": str(data["campaign_id"])}

    def set_status(self, campaign: Campaign, active: bool) -> None:
        self._call("POST", "/campaign/status/update/", {
            "advertiser_id": campaign.external_ids["ad_account"],
            "campaign_ids": [campaign.external_ids["campaign"]],
            "operation_status": "ENABLE" if active else "DISABLE",
        })

    def today_metrics(self, campaign: Campaign) -> Metrics:
        today = date.today().isoformat()
        data = self._call("GET", "/report/integrated/get/", {
            "advertiser_id": campaign.external_ids["ad_account"],
            "report_type": "BASIC",
            "data_level": "AUCTION_CAMPAIGN",
            "dimensions": ["campaign_id"],
            "metrics": ["spend", "impressions", "clicks", "conversion", "frequency"],
            "start_date": today,
            "end_date": today,
            "filtering": [{"field_name": "campaign_ids", "filter_type": "IN",
                           "filter_value": json.dumps([campaign.external_ids["campaign"]])}],
        })
        rows = data.get("list", [])
        if not rows:
            return Metrics()
        m = rows[0].get("metrics", {})
        return Metrics(spend_minor=_to_minor(m.get("spend")), impressions=int(float(m.get("impressions", 0))),
                       clicks=int(float(m.get("clicks", 0))), conversions=int(float(m.get("conversion", 0))),
                       frequency=float(m.get("frequency", 0) or 0))


# ------------------------------------------------------------------ dry run

class DryRunAds:
    """Records calls instead of spending money. Set .metrics[campaign_id] in tests."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, Any]] = []
        self.metrics: dict[str, Metrics] = {}

    def create_paused(self, campaign: Campaign, creatives: list[AdCreative]) -> dict[str, str]:
        ids = {"ad_account": campaign.external_ids.get("ad_account", "dry"),
               "campaign": f"dry_{uuid.uuid4().hex[:8]}"}
        self.calls.append(("create_paused", campaign.id, len(creatives)))
        return ids

    def set_status(self, campaign: Campaign, active: bool) -> None:
        self.calls.append(("set_status", campaign.id, active))

    def today_metrics(self, campaign: Campaign) -> Metrics:
        return self.metrics.get(campaign.id, Metrics())


class AdsRegistry:
    def __init__(self, settings: Settings) -> None:
        if settings.dry_run:
            dry = DryRunAds()
            self._by = {"meta": dry, "tiktok": dry}
            self.dry: DryRunAds | None = dry
        else:
            self._by = {"meta": MetaAds(settings), "tiktok": TikTokAds(settings)}
            self.dry = None

    def for_platform(self, platform: str) -> AdPlatform:
        return self._by[platform]  # type: ignore[return-value]
