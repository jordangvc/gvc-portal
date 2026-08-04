"""
Job Start → Invoice bridge: fill Projects Project # from Bid Estimate #.
=========================================================================
Self-running:  python tests/test_jobstart_project_number.py

Invoice "Look up & fill" keys on Projects Project # (text_mm4fvj91). Ops Accept
must carry Bid Board Estimate # onto that column fill-if-empty so billing can
find the job — never overwriting a human-typed Project #.
"""
import json
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if "weasyprint" not in sys.modules:
    _stub = types.ModuleType("weasyprint")
    _stub.HTML = object
    sys.modules["weasyprint"] = _stub

from adapters.monday import jobstart as mj  # noqa: E402
from shared import boards  # noqa: E402

PASS = 0
FAIL = []


def check(name, cond, extra=""):
    global PASS
    if cond:
        PASS += 1
    else:
        FAIL.append(f"{name} {extra}".strip())


# ---------------------------------------------------------------------------
# 1. Config: boards constant matches the invoice lookup column
# ---------------------------------------------------------------------------

check("JOBSTART_P_COL_PROJECT_NUMBER is defined",
      bool(boards.JOBSTART_P_COL_PROJECT_NUMBER))
check("default Project # column is text_mm4fvj91 (invoice lookup key)",
      boards.JOBSTART_P_COL_PROJECT_NUMBER == "text_mm4fvj91"
      or os.environ.get("GVC_MONDAY_COL_PROJECT_NUMBER")
      == boards.JOBSTART_P_COL_PROJECT_NUMBER)
check("Project # is NOT an always-write column (fill-if-empty only)",
      boards.JOBSTART_P_COL_PROJECT_NUMBER not in mj.ALWAYS_WRITE_COLUMNS)


# ---------------------------------------------------------------------------
# 2. Conflict filter: empty → fill; filled disagreeing → keep Monday
# ---------------------------------------------------------------------------

col = boards.JOBSTART_P_COL_PROJECT_NUMBER
proposed = {col: "C-005"}

safe, conflicts = mj.filter_conflicting_writes(proposed, {})
check("empty Project # is filled from Estimate #",
      col in safe and safe[col] == "C-005" and conflicts == [])

safe, conflicts = mj.filter_conflicting_writes(proposed, {col: "C-005"})
check("matching Project # is a no-op",
      col not in safe and conflicts == [])

safe, conflicts = mj.filter_conflicting_writes(proposed, {col: "MV-001"})
check("human-typed Project # is never overwritten",
      col not in safe, safe)
check("disagreeing Project # is reported as a conflict",
      len(conflicts) == 1
      and conflicts[0]["column_id"] == col
      and conflicts[0]["existing"] == "MV-001"
      and conflicts[0]["proposed"] == "C-005",
      conflicts)


# ---------------------------------------------------------------------------
# 3. hand_off writes Project # on create; skips when Estimate # blank
# ---------------------------------------------------------------------------

class HandOffClient:
    """Minimal Monday stand-in for hand_off create/adopt paths."""

    def __init__(self, *, existing_cols=None):
        self.created = []
        self.updated = []
        self.existing_cols = existing_cols  # None → create path (no adopt read)
        self._next = 7000

    def _query(self, query, variables=None):
        variables = variables or {}
        if "create_item" in query:
            self._next += 1
            vals = json.loads(variables.get("values") or "{}")
            row = {
                "id": self._next,
                "boardId": variables.get("boardId"),
                "name": variables.get("name"),
                "values": vals,
            }
            self.created.append(row)
            return {"create_item": {"id": str(self._next)}}
        if "change_multiple_column_values" in query:
            vals = json.loads(variables.get("values") or "{}")
            self.updated.append({
                "boardId": variables.get("boardId"),
                "itemId": variables.get("itemId"),
                "values": vals,
            })
            return {"change_multiple_column_values": {
                "id": variables.get("itemId")}}
        if "items_page" in query:
            # find_item_by_name probes — none found → create
            return {"boards": [{"items_page": {"items": []}}]}
        if "items(ids:" in query.replace(" ", ""):
            # fetch_item_column_texts on adopt
            cols = []
            for cid, text in (self.existing_cols or {}).items():
                cols.append({"id": cid, "text": text, "value": None})
            return {"items": [{"id": "1", "column_values": cols}]}
        return {}


def _bid(*, estimate_number="C-005", project_ids=None, ops_ids=None):
    return {
        "item_id": 1001,
        "name": "9761 Gertrude | Jent Construction",
        "context": {"estimate_number": estimate_number},
        "copy": {},
        "existing_project_ids": list(project_ids or []),
        "existing_ops_ids": list(ops_ids or []),
        "accepted_date": "",
    }


mc = HandOffClient()
report = mj.hand_off(
    mc, bid=_bid(estimate_number="C-005"),
    job_name="9761 Gertrude | Jent Construction",
    projects_values={}, ops_values={}, accepted_date="2026-08-04",
)
proj_writes = [c for c in mc.created
               if str(c["boardId"]) == str(boards.PROJECTS_BOARD_ID)]
check("create path made a Projects item", len(proj_writes) == 1, proj_writes)
check("create path writes Project # from Estimate #",
      proj_writes[0]["values"].get(col) == "C-005",
      proj_writes[0]["values"] if proj_writes else None)
check("Project # write is a bare text string (not long_text dict)",
      isinstance(proj_writes[0]["values"].get(col), str) if proj_writes else False)

mc_blank = HandOffClient()
mj.hand_off(
    mc_blank, bid=_bid(estimate_number=""),
    job_name="Blank Estimate Job",
    projects_values={}, ops_values={}, accepted_date="2026-08-04",
)
proj_blank = [c for c in mc_blank.created
              if str(c["boardId"]) == str(boards.PROJECTS_BOARD_ID)]
check("blank Estimate # does not invent a Project # write",
      proj_blank and col not in proj_blank[0]["values"],
      proj_blank[0]["values"] if proj_blank else None)


# ---------------------------------------------------------------------------
# 4. Adopt path: fill empty; never overwrite human-typed
# ---------------------------------------------------------------------------

mc_fill = HandOffClient(existing_cols={})  # Project # empty on Monday
mj.hand_off(
    mc_fill, bid=_bid(estimate_number="MV-001", project_ids=[555]),
    job_name="Adopt Fill Job",
    projects_values={}, ops_values={}, accepted_date="2026-08-04",
)
upd_fill = [u for u in mc_fill.updated
            if str(u["itemId"]) == "555"
            or str(u["boardId"]) == str(boards.PROJECTS_BOARD_ID)]
# Projects update is the one for item 555
proj_upd = [u for u in mc_fill.updated if str(u["itemId"]) == "555"]
check("adopt + empty Project # fills from Estimate #",
      proj_upd and proj_upd[0]["values"].get(col) == "MV-001",
      proj_upd)

mc_keep = HandOffClient(existing_cols={col: "HUMAN-99"})
mj.hand_off(
    mc_keep, bid=_bid(estimate_number="C-005", project_ids=[555]),
    job_name="Adopt Keep Job",
    projects_values={}, ops_values={}, accepted_date="2026-08-04",
)
proj_keep = [u for u in mc_keep.updated if str(u["itemId"]) == "555"]
check("adopt + filled Project # does NOT overwrite",
      proj_keep and col not in proj_keep[0]["values"],
      proj_keep[0]["values"] if proj_keep else mc_keep.updated)


# ---------------------------------------------------------------------------
# 5. Portal spine: Estimate # → PRO-{core} on Projects
# ---------------------------------------------------------------------------

mc_spine = HandOffClient()
mj.hand_off(
    mc_spine, bid=_bid(estimate_number="2026-0804-012"),
    job_name="Spine Number Job",
    projects_values={}, ops_values={}, accepted_date="2026-08-04",
)
proj_spine = [c for c in mc_spine.created
              if str(c["boardId"]) == str(boards.PROJECTS_BOARD_ID)]
check("spine Estimate # stamps PRO- on Project #",
      proj_spine and proj_spine[0]["values"].get(col) == "PRO-2026-0804-012",
      proj_spine[0]["values"] if proj_spine else None)

mc_est = HandOffClient()
mj.hand_off(
    mc_est, bid=_bid(estimate_number="EST-2026-0804-012"),
    job_name="Spine EST Job",
    projects_values={}, ops_values={}, accepted_date="2026-08-04",
)
proj_est = [c for c in mc_est.created
            if str(c["boardId"]) == str(boards.PROJECTS_BOARD_ID)]
check("EST- Estimate # also stamps PRO- (same core)",
      proj_est and proj_est[0]["values"].get(col) == "PRO-2026-0804-012",
      proj_est[0]["values"] if proj_est else None)


print(f"\n{PASS} passed, {len(FAIL)} failed")
for f in FAIL:
    print(f"  FAIL: {f}")
sys.exit(1 if FAIL else 0)
