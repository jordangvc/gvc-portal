"""
The GC scope-confirmation email.
=========================================================================
The highest-ROI step in the whole handoff, and the only outbound one. From
Jordan's handoff standard: send the scope to the GC's PM and site super, in the
email BODY (not an attachment nobody opens), with a reply deadline. Every
correction a GC sends back is a change order we do not eat.

It also states, in writing and on purpose, that nobody in the field prices
anything — so the super stops asking our foreman for a number.

LOCKED ARCHITECTURE (see AGENTS.md #2): this builds a Gmail DRAFT only. Nothing
is ever auto-sent to a customer. A human reads it and clicks send.

Voice per GVC Brand Guidelines 2026 — Authoritative / Knowledgeable /
Professional, with a Reassuring / Collaborative / Respectful tone. This email
goes to a customer, so it reads as partnership, never as a legal shot across
the bow.
"""
from __future__ import annotations

import re
from typing import Optional

# Business days the GC gets to reply before silence becomes our documented
# position. Three matches the handoff standard.
REPLY_BUSINESS_DAYS = 3


# ---------------------------------------------------------------------------
# Client-facing scope rules — from Jake's own Estimating Pipelines Reference
# ("Bid Description", trigger word). These are HIS conventions, enforced here so
# the portal can't violate them on his behalf:
#   • never include square footage
#   • never say "1-side / 2-side / one side / two sides" for wall coverage
# His doc says: "Before presenting a draft, re-scan it specifically for stray
# square footage and '1-side/2-side' phrasing — the two most common slip-ups."
# That re-scan is exactly the kind of thing a machine should do, so it's
# automated rather than left as a habit.
#
# This matters because the scope on a Job Start packet is often ingested from
# the scope review, which is an INTERNAL document and is full of both ("~8,775
# SF per occupant load calc", "2 layers on symbol side"). Piping that straight
# into a GC email would break Jake's rules in front of a customer.
# ---------------------------------------------------------------------------

_SQFT_RE = re.compile(
    r"""(?ix)
    (?: ~?\s*[\d,]+(?:\.\d+)?\s* (?:sq\.?\s*ft\.?|sqft|s\.?f\.?|square\s+feet|square\s+foot) )
    | (?: \b(?:approx\.?|approximately)\s+~?\s*[\d,]+(?:\.\d+)?\s*(?:sf|sq)\b )
    """
)

_SIDES_RE = re.compile(
    r"(?i)\b(?:1|2|one|two|single|double)[\s-]*(?:side[sd]?|layer[s]?\s+(?:each|per|on\s+one)\s+side)\b"
)


def scope_warnings(text: str) -> list[str]:
    """
    PURE. What in this scope text breaks Jake's client-facing rules. Returns
    human-readable warnings; empty list means it's clean to send.
    """
    out: list[str] = []
    if not text:
        return out
    sf = _SQFT_RE.findall(text)
    if sf:
        out.append("Square footage appears in the scope — Jake's bid-description "
                   "rule is never to show SF to a client.")
    if _SIDES_RE.search(text):
        out.append("\"1-side / 2-side\" phrasing appears — state board type, "
                   "thickness and location instead.")
    return out


def scrub_client_scope(text: str) -> str:
    """
    PURE. Scope text → the same text with square-footage figures and
    1-side/2-side phrasing removed, so it's safe in front of a GC.

    Deliberately conservative: it removes the offending fragment and tidies the
    punctuation rather than trying to rewrite the sentence. A slightly terse
    sentence is fine; a square-footage number in a customer email is not.
    """
    if not text:
        return ""

    # A parenthetical that exists BECAUSE of the figure goes whole. Removing
    # just the number leaves husks like "Floor 34 ( per occupant load calc)",
    # which reads worse to a GC than no aside at all.
    def _drop_paren(m: "re.Match") -> str:
        inner = m.group(1)
        if _SQFT_RE.search(inner) or _SIDES_RE.search(inner):
            return ""
        return m.group(0)

    s = re.sub(r"\(([^()]*)\)", _drop_paren, text)
    s = _SQFT_RE.sub("", s)
    s = _SIDES_RE.sub("", s)
    # Tidy what the removals left behind: empty "()" husks, doubled
    # punctuation, dangling connectors, runaway whitespace.
    s = re.sub(r"\(\s*[,;–—-]*\s*\)", "", s)
    s = re.sub(r"\s+([,.;:])", r"\1", s)
    s = re.sub(r"([,;])\s*([,.;])", r"\1", s)
    s = re.sub(r"\b(?:approx\.?|approximately|per)\s*([,.;])", r"\1", s, flags=re.I)
    s = re.sub(r"[ \t]{2,}", " ", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return "\n".join(line.strip().strip("-–— ,;") for line in s.splitlines()).strip()


def recipients(values: dict) -> tuple[Optional[str], Optional[str]]:
    """
    PURE. Packet → (to, cc). The GC's PM is the addressee; the site super is
    copied so they can't say they never saw it. Returns (None, ...) when we have
    no PM address — the caller turns that into a clear "add the GC's email"
    error rather than drafting into the void.
    """
    to = (values.get("gc_email") or "").strip() or None
    cc = (values.get("super_email") or "").strip() or None
    if to and cc and to.lower() == cc.lower():
        cc = None
    return to, cc


def subject(job_name: str, *, estimate_number: Optional[str] = None) -> str:
    """PURE. Subject line. Leads with what it is so it survives a busy inbox."""
    base = f"Scope confirmation — {job_name}"
    return f"{base} (Estimate {estimate_number})" if estimate_number else base


def body(values: dict, *, job_name: str, company_contact: str,
         estimate_number: Optional[str] = None) -> str:
    """
    PURE. The email body, plain text, scope inline.

    Structure is deliberate: what we're doing → what we are NOT doing → when we
    start → what we need back. The exclusions block is the reason this email
    exists, so it gets its own heading rather than being buried in a paragraph.
    """
    gc_pm = (values.get("gc_pm") or "").strip()
    greeting = f"Hi {gc_pm.split()[0]}," if gc_pm else "Hi,"

    # Scrubbed, not raw: this text is going to a customer, and the scope often
    # came from the internal scope review. See scrub_client_scope() above.
    scope = scrub_client_scope((values.get("scope") or "").strip())
    exclusions = scrub_client_scope((values.get("exclusions") or "").strip())
    start = (values.get("start_date") or "").strip()
    supervisor = (values.get("supervisor") or "").strip()

    lines = [
        greeting,
        "",
        f"We're scheduled on {job_name} and I want to make sure your "
        "understanding of our scope matches ours before we mobilize. Below is "
        "exactly what we've priced and what we haven't.",
        "",
        "WHAT WE'RE DOING",
        scope or "(scope to follow)",
        "",
        "WHAT'S NOT IN OUR SCOPE",
        exclusions or "(none noted)",
    ]

    if start:
        lines += ["", "START DATE", start]

    lines += [
        "",
        f"Please reply within {REPLY_BUSINESS_DAYS} business days if anything "
        "above doesn't match what you have. If it all looks right, a quick "
        "confirmation is all we need — we'd much rather sort out a difference "
        "now than discover it with a crew on site.",
        "",
        "One note that saves everyone time: our field crew doesn't price work. "
        "If you need something added or changed, send it to me and I'll get you "
        "a number the same way we did this one. That keeps our foreman focused "
        "on your walls and keeps your costs in writing.",
    ]

    if supervisor:
        lines += ["", f"Our day-to-day contact for this job is {supervisor}."]

    lines += [
        "",
        "Thanks —",
        company_contact or "The Green Valley Team",
        "Green Valley Contractors",
    ]
    return "\n".join(lines)


def draft_identifier(bid_id: int) -> str:
    """
    Dedup key for the Gmail draft. Re-sending the confirmation for the same bid
    UPDATES the existing unsent draft in place rather than stacking duplicates
    in hello@ — same contract as the estimate/CO drafts.
    """
    return f"GVC-HANDOFF-GC-{int(bid_id)}"
