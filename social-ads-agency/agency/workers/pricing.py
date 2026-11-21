"""Layer 4: pricing and tax. Pure functions, no model involved.

The orchestrator can choose a package and add-ons, but every number a client
ever sees comes from here. Edit RATE_CARD to change your prices.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Literal, Optional

from agency.models import LineItem, Quote, now_utc

Package = Literal["starter", "growth", "scale"]


@dataclass(frozen=True)
class PackageRate:
    monthly_fee: int  # minor units
    included_platforms: int
    included_creatives: int
    max_ad_budget: Optional[int]  # monthly ad budget this package is sized for; None = no ceiling
    spend_fee_bps: int = 0  # % of ad spend above spend_fee_threshold, in basis points
    spend_fee_threshold: int = 0


@dataclass(frozen=True)
class RateCard:
    currency: str
    setup_fee: int
    extra_platform_fee: int
    creative_pack_fee: int  # per 6 extra creatives
    packages: dict[str, PackageRate]


RATE_CARD: dict[str, RateCard] = {
    "INR": RateCard(
        currency="INR",
        setup_fee=15_000_00,
        extra_platform_fee=10_000_00,
        creative_pack_fee=8_000_00,
        packages={
            "starter": PackageRate(25_000_00, 1, 6, 1_50_000_00),
            "growth": PackageRate(45_000_00, 2, 12, 5_00_000_00),
            "scale": PackageRate(85_000_00, 2, 24, None, spend_fee_bps=800, spend_fee_threshold=5_00_000_00),
        },
    ),
    "USD": RateCard(
        currency="USD",
        setup_fee=500_00,
        extra_platform_fee=300_00,
        creative_pack_fee=250_00,
        packages={
            "starter": PackageRate(900_00, 1, 6, 5_000_00),
            "growth": PackageRate(1_800_00, 2, 12, 20_000_00),
            "scale": PackageRate(3_500_00, 2, 24, None, spend_fee_bps=800, spend_fee_threshold=20_000_00),
        },
    ),
}

# (label, basis points). India: 18% GST on advertising services. Change for your entity.
TAX: dict[str, tuple[str, int]] = {
    "INR": ("GST 18%", 1800),
    "USD": ("Tax", 0),
}

QUOTE_VALID_DAYS = 14


class PricingError(ValueError):
    pass


def recommend_package(currency: str, monthly_ad_budget: int) -> Package:
    card = _card(currency)
    for name in ("starter", "growth", "scale"):
        ceiling = card.packages[name].max_ad_budget
        if ceiling is None or monthly_ad_budget <= ceiling:
            return name  # type: ignore[return-value]
    return "scale"


def build_quote(
    *,
    currency: str,
    monthly_ad_budget: int,
    platforms: list[str],
    package: Optional[Package] = None,
    extra_creative_packs: int = 0,
    waive_setup: bool = False,
) -> Quote:
    if monthly_ad_budget <= 0:
        raise PricingError("monthly ad budget must be positive")
    if not platforms:
        raise PricingError("at least one platform is required")
    if not 0 <= extra_creative_packs <= 10:
        raise PricingError("extra_creative_packs must be between 0 and 10")
    card = _card(currency)
    package = package or recommend_package(currency, monthly_ad_budget)
    rate = card.packages[package]

    items = [LineItem(
        description=f"{package.title()} management, first month "
                    f"({rate.included_platforms} platform(s), {rate.included_creatives} creatives)",
        amount_minor=rate.monthly_fee,
    )]
    extra_platforms = max(0, len(set(platforms)) - rate.included_platforms)
    if extra_platforms:
        items.append(LineItem(description=f"Additional platform x{extra_platforms}",
                              amount_minor=extra_platforms * card.extra_platform_fee))
    if rate.spend_fee_bps and monthly_ad_budget > rate.spend_fee_threshold:
        over = monthly_ad_budget - rate.spend_fee_threshold
        items.append(LineItem(
            description=f"Performance fee {rate.spend_fee_bps / 100:g}% of ad spend above threshold",
            amount_minor=_bps(over, rate.spend_fee_bps),
        ))
    if extra_creative_packs:
        items.append(LineItem(description=f"Extra creative pack x{extra_creative_packs} (6 ads each)",
                              amount_minor=extra_creative_packs * card.creative_pack_fee))
    if not waive_setup:
        items.append(LineItem(description="One-time setup: tracking audit, pixel and events, account structure",
                              amount_minor=card.setup_fee))

    subtotal = sum(i.amount_minor for i in items)
    tax_label, tax_bps = TAX.get(card.currency, ("Tax", 0))
    tax = _bps(subtotal, tax_bps)
    return Quote(
        package=package,
        currency=card.currency,
        line_items=items,
        subtotal_minor=subtotal,
        tax_label=tax_label,
        tax_rate_bps=tax_bps,
        tax_minor=tax,
        total_minor=subtotal + tax,
        monthly_ad_budget_minor=monthly_ad_budget,
        valid_until=now_utc() + timedelta(days=QUOTE_VALID_DAYS),
    )


def format_money(amount_minor: int, currency: str) -> str:
    major, minor = divmod(abs(amount_minor), 100)
    sign = "-" if amount_minor < 0 else ""
    if currency == "INR":
        return f"{sign}INR {_indian_grouping(major)}.{minor:02d}"
    return f"{sign}{currency} {major:,}.{minor:02d}"


def _bps(amount: int, bps: int) -> int:
    # Round half up on integers; no floats.
    return (amount * bps + 5000) // 10000


def _card(currency: str) -> RateCard:
    try:
        return RATE_CARD[currency.upper()]
    except KeyError as exc:
        raise PricingError(f"no rate card for currency {currency!r}; add one to RATE_CARD") from exc


def _indian_grouping(n: int) -> str:
    s = str(n)
    if len(s) <= 3:
        return s
    head, tail = s[:-3], s[-3:]
    parts = []
    while len(head) > 2:
        parts.insert(0, head[-2:])
        head = head[:-2]
    if head:
        parts.insert(0, head)
    return ",".join(parts) + "," + tail
