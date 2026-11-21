"""Layer 4: kill-switch rules for live campaigns. Pure rules + a sweep runner.

Pausing is the one platform write that does NOT need human approval: it can
only reduce spend. Launching, resuming and budget increases always go
through the Slack gate.

Run it every 15 to 30 minutes:
    cron:  */15 * * * *  python -m agency.workers.killswitch
    n8n / Temporal: POST /jobs/killswitch with header X-Job-Token
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal, Optional

from pydantic import BaseModel

from agency.models import Guardrails

log = logging.getLogger(__name__)


class Metrics(BaseModel):
    """Today's numbers for one campaign, in the campaign's currency."""

    spend_minor: int = 0
    impressions: int = 0
    clicks: int = 0
    conversions: int = 0
    frequency: float = 0.0

    @property
    def ctr(self) -> float:
        return self.clicks / self.impressions if self.impressions else 0.0

    @property
    def cpa_minor(self) -> Optional[int]:
        return self.spend_minor // self.conversions if self.conversions else None


@dataclass(frozen=True)
class Verdict:
    action: Literal["pause", "alert"]
    rule: str
    detail: str


def evaluate(m: Metrics, g: Guardrails) -> list[Verdict]:
    out: list[Verdict] = []

    cap = int(g.daily_budget_minor * g.max_overspend_ratio)
    if m.spend_minor > cap:
        out.append(Verdict("pause", "overspend",
                           f"spent {m.spend_minor} today against a cap of {cap} "
                           f"({g.max_overspend_ratio:g}x the daily budget)"))

    if g.target_cpa_minor:
        if m.conversions == 0 and m.spend_minor >= g.target_cpa_minor * g.no_conversion_spend_multiple:
            out.append(Verdict("pause", "no_conversions",
                               f"spent {m.spend_minor} with zero conversions "
                               f"({g.no_conversion_spend_multiple:g}x target CPA)"))
        elif m.cpa_minor is not None and m.cpa_minor > g.target_cpa_minor * g.max_cpa_multiple and m.conversions >= 3:
            out.append(Verdict("pause", "cpa_blowout",
                               f"CPA {m.cpa_minor} is over {g.max_cpa_multiple:g}x the target {g.target_cpa_minor}"))

    if m.impressions >= g.min_impressions_for_ctr and m.ctr < g.min_ctr:
        out.append(Verdict("alert", "low_ctr",
                           f"CTR {m.ctr:.2%} after {m.impressions} impressions (floor {g.min_ctr:.2%}); "
                           "creative fatigue or a weak hook"))
    if m.frequency > g.max_frequency:
        out.append(Verdict("alert", "high_frequency",
                           f"frequency {m.frequency:.1f} (ceiling {g.max_frequency:g}); audience is saturated"))
    return out


def sweep(services) -> list[dict]:  # services: agency.app.Services (untyped to avoid an import cycle)
    """Check every active campaign, pause the ones that break a hard rule, report the rest."""
    report = []
    for campaign in services.repo.list_campaigns(status="active"):
        platform = services.ads.for_platform(campaign.draft.platform)
        try:
            metrics = platform.today_metrics(campaign)
        except Exception as exc:  # one bad API call must not stop the sweep
            log.exception("metrics failed for %s", campaign.id)
            report.append({"campaign_id": campaign.id, "error": str(exc)})
            continue
        verdicts = evaluate(metrics, campaign.guardrails)
        pauses = [v for v in verdicts if v.action == "pause"]
        if pauses:
            platform.set_status(campaign, active=False)
            campaign.status = "paused"
            campaign.pause_reasons.extend(f"{v.rule}: {v.detail}" for v in pauses)
            services.repo.save_campaign(campaign)
            services.repo.log_event(campaign.client_key, "killswitch.paused",
                                    {"campaign_id": campaign.id, "rules": [v.rule for v in pauses]})
        if verdicts:
            services.slack.notify(
                f"{'PAUSED' if pauses else 'Alert'} `{campaign.id}` ({campaign.draft.name}, {campaign.draft.platform}): "
                + "; ".join(f"{v.rule}: {v.detail}" for v in verdicts)
            )
        report.append({"campaign_id": campaign.id, "metrics": metrics.model_dump(),
                       "verdicts": [v.__dict__ for v in verdicts]})
    return report


if __name__ == "__main__":
    import json

    from agency.app import build_services

    logging.basicConfig(level=logging.INFO)
    print(json.dumps(sweep(build_services()), indent=2, default=str))
