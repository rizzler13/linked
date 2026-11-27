"""
PDF generation from analysis results using Playwright's Chromium print engine.
No native library dependencies beyond what's already needed for the fetch pipeline.
"""

import html as _html

from app.schemas import LinkAnalysisResponse


async def generate_pdf(data: LinkAnalysisResponse) -> bytes:
    """Render a LinkAnalysisResponse to PDF bytes using headless Chromium."""
    from playwright.async_api import async_playwright

    html_content = _build_html(data)

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"],
        )
        try:
            page = await browser.new_page()
            await page.set_content(html_content, wait_until="domcontentloaded")
            pdf_bytes = await page.pdf(
                format="A4",
                print_background=True,
                margin={"top": "18mm", "bottom": "18mm", "left": "20mm", "right": "20mm"},
            )
        finally:
            await browser.close()

    return pdf_bytes


def _e(val) -> str:
    if val is None:
        return ""
    return _html.escape(str(val))


def _build_html(data: LinkAnalysisResponse) -> str:
    brand    = data.brand
    analysis = data.analysis
    meta     = data.meta
    ca       = analysis.competitor_analysis
    qs       = analysis.target_search_queries or []
    seo      = analysis.seo_improvements
    svs      = analysis.top_services or []
    aud      = analysis.target_audience

    sections = []

    # Brand
    adv = brand.competitive_advantages or []
    pills_html = "".join(f'<span class="pill">{_e(a)}</span>' for a in adv)
    sections.append(f"""
    <div class="section brand-section">
      <table class="brand-header" cellpadding="0" cellspacing="0"><tr>
        <td class="avatar">{_e((brand.name or "?")[0].upper())}</td>
        <td class="brand-info-cell">
          <div class="brand-name">{_e(brand.name)}</div>
          <div class="brand-cat">{_e(brand.category)} &middot; {_e(brand.site_type)}</div>
        </td>
      </tr></table>
      <p class="summary">{_e(brand.summary)}</p>
      {f'<div class="pills">{pills_html}</div>' if adv else ""}
    </div>
    """)

    # SEO
    if seo:
        kw_rows = ""
        if seo.primary_keyword:
            kw_rows += f'<div class="kw-row"><strong>Primary:</strong> {_e(seo.primary_keyword)}</div>'
        if seo.secondary_keywords:
            kw_rows += f'<div class="kw-row"><strong>Secondary:</strong> {", ".join(_e(k) for k in seo.secondary_keywords)}</div>'
        if seo.long_tail_keywords:
            kw_rows += f'<div class="kw-row"><strong>Long-tail:</strong> {", ".join(_e(k) for k in seo.long_tail_keywords)}</div>'
        sections.append(f"""
        <div class="section">
          <h2>SEO Improvements</h2>
          <div class="serp-preview">
            <div class="serp-url">yourdomain.com</div>
            <div class="serp-title">{_e(seo.recommended_title)}</div>
            <div class="serp-desc">{_e(seo.meta_description)}</div>
          </div>
          <div class="kw-block">{kw_rows}</div>
        </div>
        """)

    # Competitors
    if ca and ca.competitors:
        comp_cards = ""
        for c in ca.competitors:
            str_items = "".join(f"<li>{_e(s)}</li>" for s in (c.strengths or []))
            gap_items = "".join(f"<li>{_e(g)}</li>" for g in (c.weaknesses_or_gaps or []))
            comp_cards += f"""
            <div class="comp-card">
              <div class="comp-name">{_e(c.name)}</div>
              {f'<div class="comp-domain">{_e(c.domain)}</div>' if c.domain else ""}
              <div class="comp-service">{_e(c.main_service)} &middot; {_e(c.target_market)}</div>
              <table class="cols-2" cellpadding="0" cellspacing="0"><tr>
                <td><div class="col-label">Strengths</div><ul class="str-list">{str_items}</ul></td>
                <td><div class="col-label">Gaps</div><ul class="gap-list">{gap_items}</ul></td>
              </tr></table>
            </div>
            """
        sections.append(f"""
        <div class="section">
          <h2>Competitor Analysis</h2>
          {f'<p class="overview">{_e(ca.brand_overview)}</p>' if ca.brand_overview else ""}
          {comp_cards}
        </div>
        """)

    # Services
    if svs:
        svc_cards = "".join(f"""
        <div class="svc-card">
          <div class="svc-name">{_e(s.service_name)}</div>
          <div class="svc-desc">{_e(s.description)}</div>
          <div class="svc-meta">Industry: {_e(s.target_industry)} &middot; Customer: {_e(s.target_customer)}</div>
        </div>
        """ for s in svs)
        sections.append(f"""
        <div class="section">
          <h2>Top Services</h2>
          {svc_cards}
        </div>
        """)

    # Search queries
    if qs:
        q_rows = "".join(f"""
        <tr>
          <td class="plat">{_e(q.platform)}</td>
          <td>{_e(q.query)}</td>
          <td>{_e(q.search_intent)}</td>
        </tr>
        """ for q in qs)
        sections.append(f"""
        <div class="section">
          <h2>Target Search Queries</h2>
          <table class="q-table" cellpadding="0" cellspacing="0">
            <thead><tr><th>Platform</th><th>Query</th><th>Intent</th></tr></thead>
            <tbody>{q_rows}</tbody>
          </table>
        </div>
        """)

    # Audience
    if aud:
        pa = aud.primary_audience or []
        sa = aud.secondary_audience or []
        aud_cards = ""
        for seg in pa:
            aud_cards += f'<div class="aud-card"><div class="aud-name">{_e(seg.segment_name)} <span class="aud-type">Primary</span></div><div class="aud-req">{_e(seg.requirement)}</div></div>'
        for seg in sa:
            aud_cards += f'<div class="aud-card"><div class="aud-name">{_e(seg.segment_name)} <span class="aud-type">Secondary</span></div><div class="aud-req">{_e(seg.requirement)}</div></div>'
        sections.append(f"""
        <div class="section">
          <h2>Target Audience</h2>
          {aud_cards}
          {f'<div class="geo">Geographic focus: {_e(aud.geographical_focus)}</div>' if aud.geographical_focus else ""}
        </div>
        """)

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: 'Helvetica Neue', Helvetica, Arial, sans-serif; font-size: 10.5pt; color: #1a1a2e; line-height: 1.55; }}
  h2 {{ font-size: 12.5pt; font-weight: 700; color: #3a3a6e; margin-bottom: 12px; padding-bottom: 5px; border-bottom: 1px solid #e0e0f0; }}
  .section {{ margin-bottom: 24px; page-break-inside: avoid; }}
  .brand-section {{ background: #f4f4ff; border-radius: 7px; padding: 16px 18px; margin-bottom: 20px; }}
  .brand-header {{ width: 100%; margin-bottom: 10px; border-collapse: collapse; }}
  .avatar {{ width: 44px; background: #5a5aef; color: white; border-radius: 7px; text-align: center; font-size: 18pt; font-weight: 700; padding: 6px 0; vertical-align: middle; }}
  .brand-info-cell {{ padding-left: 14px; vertical-align: middle; }}
  .brand-name {{ font-size: 17pt; font-weight: 700; color: #1a1a2e; }}
  .brand-cat {{ font-size: 9.5pt; color: #6b6b8a; margin-top: 2px; }}
  .summary {{ font-size: 10.5pt; color: #3a3a5e; margin-bottom: 10px; }}
  .pills {{ display: flex; flex-wrap: wrap; gap: 5px; }}
  .pill {{ background: #ebebff; border: 1px solid #c5c5ef; color: #5a5aef; border-radius: 100px; padding: 2px 10px; font-size: 8.5pt; }}
  .serp-preview {{ background: white; border: 1px solid #e0e0ef; border-radius: 6px; padding: 12px 16px; margin-bottom: 10px; }}
  .serp-url {{ color: #2e7d32; font-size: 9pt; margin-bottom: 2px; }}
  .serp-title {{ color: #1a73e8; font-size: 12.5pt; font-weight: 500; }}
  .serp-desc {{ color: #3c4043; font-size: 9.5pt; margin-top: 4px; }}
  .kw-block {{ font-size: 9.5pt; }}
  .kw-row {{ margin-bottom: 4px; color: #4a4a6a; }}
  .kw-row strong {{ color: #1a1a2e; }}
  .comp-card {{ border: 1px solid #e0e0f0; border-radius: 6px; padding: 11px 14px; margin-bottom: 9px; }}
  .comp-name {{ font-size: 11.5pt; font-weight: 700; color: #1a1a2e; margin-bottom: 2px; }}
  .comp-domain {{ font-size: 8.5pt; color: #5a5aef; margin-bottom: 3px; }}
  .comp-service {{ font-size: 9.5pt; color: #6b6b8a; margin-bottom: 8px; }}
  .cols-2 {{ width: 100%; border-collapse: collapse; }}
  .cols-2 td {{ width: 50%; vertical-align: top; padding-right: 12px; }}
  .col-label {{ font-size: 8.5pt; font-weight: 700; text-transform: uppercase; letter-spacing: 0.5px; color: #9a9ab0; margin-bottom: 4px; }}
  .str-list, .gap-list {{ list-style: none; padding: 0; }}
  .str-list li, .gap-list li {{ font-size: 9pt; color: #4a4a6a; margin-bottom: 2px; padding-left: 14px; text-indent: -14px; }}
  .str-list li::before {{ content: "+  "; color: #059669; font-weight: 700; }}
  .gap-list li::before {{ content: "-  "; color: #e11d48; font-weight: 700; }}
  .overview {{ font-size: 9.5pt; color: #4a4a6a; margin-bottom: 11px; }}
  .svc-card {{ border-left: 3px solid #5a5aef; padding: 9px 13px; margin-bottom: 9px; background: #f8f8ff; border-radius: 0 5px 5px 0; }}
  .svc-name {{ font-size: 11pt; font-weight: 700; color: #1a1a2e; margin-bottom: 2px; }}
  .svc-desc {{ font-size: 9.5pt; color: #4a4a6a; margin-bottom: 3px; }}
  .svc-meta {{ font-size: 8.5pt; color: #9a9ab0; }}
  .q-table {{ width: 100%; border-collapse: collapse; font-size: 9.5pt; }}
  .q-table th {{ background: #f0f0ff; padding: 7px 11px; text-align: left; font-weight: 700; color: #3a3a6e; border: 1px solid #e0e0f0; }}
  .q-table td {{ padding: 7px 11px; border: 1px solid #e0e0f0; color: #4a4a6a; }}
  .plat {{ font-weight: 600; color: #1a1a2e; white-space: nowrap; }}
  .aud-card {{ background: #f8f8ff; border-radius: 6px; padding: 9px 13px; margin-bottom: 7px; }}
  .aud-name {{ font-size: 11pt; font-weight: 700; color: #1a1a2e; margin-bottom: 2px; }}
  .aud-type {{ font-size: 8.5pt; font-weight: 400; color: #9a9ab0; }}
  .aud-req {{ font-size: 9.5pt; color: #4a4a6a; }}
  .geo {{ font-size: 9.5pt; color: #6b6b8a; margin-top: 9px; }}
  .footer {{ position: fixed; bottom: 0; left: 0; right: 0; text-align: center; font-size: 7.5pt; color: #c0c0d0; border-top: 1px solid #e8e8f4; padding: 5px 0; background: white; }}
</style>
</head>
<body>
  {"".join(sections)}
  <div class="footer">Generated by link-ed &nbsp;&middot;&nbsp; {_e(meta.request_id[:8])}</div>
</body>
</html>"""
