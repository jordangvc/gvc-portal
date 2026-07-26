"""subsystems — domain logic, one package per business area.

invoice / estimate / change_order / checks. Validation, enrichment, formatting,
document rendering, draft stores — the rules of each area. Subsystems do NOT
orchestrate cross-system side effects (that is the orchestrators' job).

Rule: depend only on shared (and, within an area, sibling modules). Never import
orchestrators or app.
"""
