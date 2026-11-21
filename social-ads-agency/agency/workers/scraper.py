"""Layer 4: fetch a client's site and detect tracking pixels. No model involved.

The URL comes from a chat message, so it is untrusted: only http(s), only
public addresses, a size cap and a short timeout (this blocks SSRF into your
own network).
"""

from __future__ import annotations

import ipaddress
import re
import socket
import time
from urllib.parse import urlparse

import httpx2 as httpx

from agency.models import SiteFindings

MAX_BYTES = 2_000_000
TIMEOUT = 10.0
UA = "Mozilla/5.0 (compatible; AdAuditBot/1.0)"

PIXEL_PATTERNS: dict[str, list[str]] = {
    "meta_pixel": [r"connect\.facebook\.net/[^\"']*/fbevents\.js", r"fbq\(\s*['\"]init['\"]"],
    "tiktok_pixel": [r"analytics\.tiktok\.com/i18n/pixel", r"\bttq\.load\("],
    "google_analytics_4": [r"googletagmanager\.com/gtag/js\?id=G-", r"gtag\(\s*['\"]config['\"]\s*,\s*['\"]G-"],
    "google_tag_manager": [r"googletagmanager\.com/gtm\.js", r"GTM-[A-Z0-9]{4,}"],
    "google_ads": [r"gtag\(\s*['\"]config['\"]\s*,\s*['\"]AW-", r"googleadservices\.com/pagead/conversion"],
    "linkedin_insight": [r"snap\.licdn\.com/li\.lms-analytics", r"_linkedin_partner_id"],
    "snap_pixel": [r"sc-static\.net/scevent\.min\.js", r"\bsnaptr\("],
    "pinterest_tag": [r"s\.pinimg\.com/ct/core\.js", r"\bpintrk\("],
}


class UnsafeURL(ValueError):
    pass


def normalize_url(raw: str) -> str:
    raw = raw.strip()
    if "://" in raw and not re.match(r"^https?://", raw, re.I):
        raise UnsafeURL(f"only http and https are allowed: {raw!r}")
    if not re.match(r"^https?://", raw, re.I):
        raw = "https://" + raw
    parsed = urlparse(raw)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise UnsafeURL(f"not a web address: {raw!r}")
    return raw


def assert_public_host(hostname: str) -> None:
    try:
        infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror as exc:
        raise UnsafeURL(f"cannot resolve {hostname}") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise UnsafeURL(f"{hostname} resolves to a non-public address")


def detect_pixels(html: str) -> dict[str, bool]:
    return {
        name: any(re.search(p, html, re.I) for p in patterns)
        for name, patterns in PIXEL_PATTERNS.items()
    }


def _meta(html: str, name: str) -> str | None:
    m = re.search(
        r"<meta[^>]+(?:name|property)=[\"']%s[\"'][^>]*content=[\"']([^\"']*)[\"']" % re.escape(name), html, re.I
    ) or re.search(
        r"<meta[^>]+content=[\"']([^\"']*)[\"'][^>]*(?:name|property)=[\"']%s[\"']" % re.escape(name), html, re.I
    )
    return m.group(1).strip() if m else None


def analyze_html(url: str, html: str, status: int | None = None, elapsed_ms: int | None = None) -> SiteFindings:
    title = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
    return SiteFindings(
        url=url,
        reachable=True,
        status_code=status,
        https=url.lower().startswith("https://"),
        title=re.sub(r"\s+", " ", title.group(1)).strip()[:200] if title else None,
        meta_description=(_meta(html, "description") or "")[:300] or None,
        pixels=detect_pixels(html),
        has_og_image=_meta(html, "og:image") is not None,
        response_ms=elapsed_ms,
    )


def scan_site(raw_url: str, client: httpx.Client | None = None) -> SiteFindings:
    try:
        url = normalize_url(raw_url)
        own = client is None
        client = client or httpx.Client(timeout=TIMEOUT, headers={"User-Agent": UA}, follow_redirects=False)
        try:
            # Follow redirects by hand so every hop is checked against private ranges.
            for _ in range(5):
                assert_public_host(urlparse(url).hostname or "")
                start = time.monotonic()
                with client.stream("GET", url) as resp:
                    if resp.status_code in (301, 302, 303, 307, 308) and "location" in resp.headers:
                        url = str(httpx.URL(url).join(resp.headers["location"]))
                        continue
                    body = b""
                    for chunk in resp.iter_bytes():
                        body += chunk
                        if len(body) > MAX_BYTES:
                            break
                    elapsed = int((time.monotonic() - start) * 1000)
                    html = body.decode(resp.encoding or "utf-8", errors="replace")
                    return analyze_html(url, html, resp.status_code, elapsed)
            return SiteFindings(url=url, reachable=False, error="too many redirects")
        finally:
            if own:
                client.close()
    except UnsafeURL as exc:
        return SiteFindings(url=raw_url, reachable=False, error=str(exc))
    except httpx.HTTPError as exc:
        return SiteFindings(url=raw_url, reachable=False, error=f"{type(exc).__name__}: {exc}")


def tracking_gaps(site: SiteFindings, platforms: list[str]) -> list[str]:
    gaps = []
    if not site.reachable:
        return [f"Website could not be checked ({site.error})."]
    if not site.https:
        gaps.append("Site is not on HTTPS; ad platforms and browsers penalize this.")
    if "meta" in platforms and not site.pixels.get("meta_pixel"):
        gaps.append("No Meta Pixel found: Meta cannot optimize for purchases or leads.")
    if "tiktok" in platforms and not site.pixels.get("tiktok_pixel"):
        gaps.append("No TikTok Pixel found: TikTok cannot optimize for conversions.")
    if not (site.pixels.get("google_analytics_4") or site.pixels.get("google_tag_manager")):
        gaps.append("No GA4 or Google Tag Manager: no independent source of truth for results.")
    if not site.has_og_image:
        gaps.append("No og:image tag: shared links show without a preview image.")
    if site.response_ms and site.response_ms > 2500:
        gaps.append(f"Slow first response ({site.response_ms} ms): paid clicks will bounce.")
    return gaps
