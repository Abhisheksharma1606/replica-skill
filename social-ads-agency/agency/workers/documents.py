"""Layer 4: render the proposal + quote into a shareable document.

Writes a self-contained HTML file (always) and a PDF next to it when
WeasyPrint is installed. If you use PandaDoc, see integrations/pandadoc.py:
the same Proposal and Quote objects fill a PandaDoc template instead.
"""

from __future__ import annotations

import html
import os
import secrets
from dataclasses import dataclass
from typing import Optional

from agency.models import ClientProfile, Proposal, Quote
from agency.workers.pricing import format_money


@dataclass
class RenderedDoc:
    file_id: str
    html_path: str
    pdf_path: Optional[str]


def render_proposal_html(agency_name: str, profile: ClientProfile, proposal: Proposal, quote: Quote) -> str:
    e = html.escape
    rows = "".join(
        f"<tr><td>{e(i.description)}</td><td class=num>{e(format_money(i.amount_minor, quote.currency))}</td></tr>"
        for i in quote.line_items
    )
    sections = "".join(
        f"<h2>{e(s.heading)}</h2>" + "".join(f"<p>{e(p)}</p>" for p in s.body.split("\n\n") if p.strip())
        for s in proposal.sections
    )
    kpis = "".join(f"<li>{e(k)}</li>" for k in proposal.kpis)
    plan = "".join(f"<li>{e(k)}</li>" for k in proposal.first_30_days)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(proposal.title)}</title>
<style>
  body {{ font: 16px/1.55 system-ui, sans-serif; color: #1b1f24; background: #fff; max-width: 760px;
         margin: 0 auto; padding: 40px 16px; }}
  h1 {{ font-size: 28px; margin: 0 0 4px; }} h2 {{ font-size: 19px; margin-top: 32px; }}
  .muted {{ color: #5b6470; }} table {{ width: 100%; border-collapse: collapse; margin-top: 12px; }}
  td {{ padding: 8px 0; border-bottom: 1px solid #e3e6ea; }} .num {{ text-align: right; white-space: nowrap; }}
  tr.total td {{ font-weight: 700; border-bottom: none; }}
  .box {{ background: #f5f7f9; border-radius: 8px; padding: 16px 20px; }}
</style></head><body>
<p class=muted>{e(agency_name)} for {e(profile.business_name or "you")}</p>
<h1>{e(proposal.title)}</h1>
<div class=box><p>{e(proposal.executive_summary)}</p></div>
{sections}
<h2>How we will measure success</h2><ul>{kpis}</ul>
<h2>Your first 30 days</h2><ol>{plan}</ol>
<h2>Investment</h2>
<table>{rows}
<tr><td>Subtotal</td><td class=num>{e(format_money(quote.subtotal_minor, quote.currency))}</td></tr>
<tr><td>{e(quote.tax_label)}</td><td class=num>{e(format_money(quote.tax_minor, quote.currency))}</td></tr>
<tr class=total><td>Total due now</td><td class=num>{e(format_money(quote.total_minor, quote.currency))}</td></tr>
</table>
<p class=muted>Recommended ad budget of {e(format_money(quote.monthly_ad_budget_minor, quote.currency))} per month
is paid directly to the ad platforms from your own account. Quote {e(quote.id)}, valid until
{quote.valid_until:%d %b %Y}.</p>
</body></html>"""


def save_document(files_dir: str, content_html: str) -> RenderedDoc:
    os.makedirs(files_dir, exist_ok=True)
    # Unguessable id: the link is shared over chat with no login.
    file_id = secrets.token_urlsafe(18)
    html_path = os.path.join(files_dir, f"{file_id}.html")
    with open(html_path, "w", encoding="utf-8") as fh:
        fh.write(content_html)
    pdf_path = None
    try:
        from weasyprint import HTML  # optional dependency

        pdf_path = os.path.join(files_dir, f"{file_id}.pdf")
        HTML(string=content_html).write_pdf(pdf_path)
    except ImportError:
        pass
    return RenderedDoc(file_id=file_id, html_path=html_path, pdf_path=pdf_path)
