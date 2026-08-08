"""Render Field Guide procedures to HTML fragments matching fieldguide.css contracts.

Classes used by ``web/fieldguide.html``:
``.doc``, ``.plainwords``, ``.steps``, ``.expert``, ``.nextpath``, ``.callout``,
``.warn``, ``.note``, ``.tip``.
"""
from __future__ import annotations

import html
from typing import Any, Optional


def _e(s: Any) -> str:
    return html.escape("" if s is None else str(s), quote=True)


def _ul(items: list[str], *, cls: str = "") -> str:
    if not items:
        return ""
    lis = "".join(f"<li>{_e(i)}</li>" for i in items if i)
    c = f' class="{_e(cls)}"' if cls else ""
    return f"<ul{c}>{lis}</ul>"


def render_procedure_article(proc: dict, *, include_expert: bool = True) -> str:
    """Full ``<article class="doc">`` for a task guide — matches shell CSS."""
    pid = _e(proc.get("id"))
    title = _e(proc.get("title"))
    stage = proc.get("jobcheck_stage") or proc.get("stage_line")
    stage_html = (
        f'<p class="stage-line">Job Check: <strong>{_e(stage)}</strong></p>'
        if stage else ""
    )
    lede = proc.get("lede") or proc.get("short_answer") or proc.get("summary") or ""
    plain = f'<div class="plainwords"><p>{_e(lede)}</p></div>' if lede else ""

    when = proc.get("when_to_use") or ""
    when_html = (
        f'<section class="when"><h3>When to use</h3><p>{_e(when)}</p></section>'
        if when else ""
    )
    prereq = _ul(list(proc.get("prerequisites") or []))
    prereq_html = (
        f'<section class="prereq"><h3>Before you start</h3>{prereq}</section>'
        if prereq else ""
    )
    tools = _ul(list(proc.get("tools") or []))
    mats = _ul(list(proc.get("materials") or []))
    needs = ""
    if tools or mats:
        needs = '<section class="needs"><h3>What you need</h3>'
        if tools:
            needs += f"<h4>Tools</h4>{tools}"
        if mats:
            needs += f"<h4>Materials</h4>{mats}"
        needs += "</section>"

    steps_lis = []
    for step in proc.get("steps") or []:
        text = step.get("text") or ""
        sid = _e(step.get("id"))
        steps_lis.append(
            f'<li id="{sid}"><label class="step">'
            f'<input type="checkbox" data-step="{sid}">'
            f'<span class="txt">{_e(text)}</span></label></li>'
        )
    steps_html = (
        f'<ul class="steps">{"".join(steps_lis)}</ul>' if steps_lis else ""
    )

    warn_blocks = []
    for w in proc.get("warnings") or []:
        kind = (w.get("kind") or "note").lower()
        cls = {"stop": "callout stop", "warn": "callout warn",
               "tip": "callout tip", "safety": "callout stop",
               "money": "callout money"}.get(kind, "callout note")
        warn_blocks.append(f'<div class="{cls}">{_e(w.get("text"))}</div>')
    warnings_html = "".join(warn_blocks)

    var_blocks = []
    for v in proc.get("variations") or []:
        var_blocks.append(
            f'<div class="variation">'
            f'<h4>{_e(v.get("title"))}</h4>'
            f'<p class="when-cond"><em>When:</em> {_e(v.get("when"))}</p>'
            f'<p>{_e(v.get("guidance"))}</p></div>'
        )
    variations_html = (
        f'<section class="variations"><h3>Field conditions / variations</h3>'
        f'{"".join(var_blocks)}</section>'
        if var_blocks else ""
    )

    qc = _ul(list(proc.get("quality_checks") or []), cls="checks")
    qc_html = (
        f'<section class="quality"><h3>Check before moving on</h3>{qc}</section>'
        if qc else ""
    )
    mistakes = _ul(list(proc.get("common_mistakes") or []))
    mistakes_html = (
        f'<section class="mistakes"><h3>Common mistakes</h3>{mistakes}</section>'
        if mistakes else ""
    )

    ts_blocks = []
    for t in proc.get("troubleshooting") or []:
        ts_blocks.append(
            f'<div class="trouble">'
            f'<p><strong>Symptom:</strong> {_e(t.get("symptom"))}</p>'
            f'<p><strong>Likely cause:</strong> {_e(t.get("likely_cause"))}</p>'
            f'<p><strong>Fix:</strong> {_e(t.get("fix"))}</p></div>'
        )
    trouble_html = (
        f'<section class="troubleshooting"><h3>If something looks wrong</h3>'
        f'{"".join(ts_blocks)}</section>'
        if ts_blocks else ""
    )

    next_links = []
    for link in proc.get("next_steps") or []:
        link_pid = _e(link.get("procedure_id"))
        label = _e(link.get("label") or link.get("procedure_id"))
        why = _e(link.get("why") or "")
        btn = (
            f'<button type="button" class="doclink" data-go="{link_pid}">'
            f"{label}</button>"
        )
        if why:
            btn += f' <span class="why">({why})</span>'
        next_links.append(btn)
    related_links = []
    for link in proc.get("related") or []:
        link_pid = _e(link.get("procedure_id"))
        label = _e(link.get("label") or link.get("procedure_id"))
        related_links.append(
            f'<button type="button" class="doclink" data-go="{link_pid}">'
            f"{label}</button>"
        )

    next_html = ""
    if next_links or related_links:
        next_html = (
            '<div class="nextpath" data-catalog-nav="1" aria-label="What next">'
            '<span class="tag">What&apos;s next</span>'
        )
        if next_links:
            next_html += "<p>" + " · ".join(next_links) + "</p>"
        if related_links:
            next_html += (
                '<p><span class="tag">Related</span> '
                + " · ".join(related_links) + "</p>"
            )
        next_html += "</div>"

    gov = proc.get("governance") or {}
    prov = proc.get("provenance") or {}
    meta = (
        f'<footer class="doc-meta">'
        f'<span>Status: {_e(gov.get("status"))}</span>'
        f' · <span>Owner: {_e(gov.get("owner"))}</span>'
        f' · <span>Reviewed: {_e(gov.get("last_reviewed") or "—")}</span>'
        f'</footer>'
    )
    prov_html = ""
    if prov.get("note"):
        prov_html = (
            f'<p class="provenance"><em>Where this came from:</em> '
            f'{_e(prov.get("note"))}</p>'
        )

    expert_html = ""
    if include_expert:
        for ex in proc.get("experts") or []:
            expert_html += (
                f'<aside class="expert" id="{_e(ex.get("id"))}">'
                f'<span class="tag">{_e(ex.get("tag") or "Detail")}</span>'
                f'<p>{_e(ex.get("summary"))}</p></aside>'
            )

    return (
        f'<section class="doc" id="{pid}" data-trade="{_e(proc.get("trade"))}" '
        f'data-template="{_e(proc.get("template") or "task_guide")}" '
        f'data-source="catalog">'
        f'<div class="doc-head">'
        f"{stage_html}"
        f"<h2>{title}</h2>"
        f"</div>"
        f"{plain}{when_html}{prereq_html}{needs}"
        f'<div class="block"><h3>Steps</h3>{steps_html}</div>'
        f"{warnings_html}{variations_html}{qc_html}{mistakes_html}"
        f"{trouble_html}{expert_html}{next_html}{prov_html}{meta}"
        f"</section>"
    )


def render_card_tile(card: dict) -> str:
    """Home-tile HTML compatible with fieldguide ``.tile`` filtering."""
    pid = _e(card.get("id"))
    roles = " ".join(_e(r) for r in (card.get("roles") or []))
    return (
        f'<a class="tile" data-go="{pid}" data-role="{roles}" href="#{pid}">'
        f'<span class="tile-title">{_e(card.get("title"))}</span>'
        f'<span class="tile-desc">{_e(card.get("summary") or card.get("short_answer"))}</span>'
        f"</a>"
    )


def render_search_results(results: list[dict]) -> str:
    if not results:
        return '<p class="empty">No matching procedures — try a synonym (scrape, hang rock, knockdown).</p>'
    return '<div class="tiles catalog-results">' + "".join(
        render_card_tile(c) for c in results
    ) + "</div>"
