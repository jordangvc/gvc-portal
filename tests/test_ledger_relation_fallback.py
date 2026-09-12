"""
Regression for the bare-ledger-row incident (Sep 2026): the Ops-Ready → Invoice
path handed an OPERATIONS item to the Invoices board's Projects-only Linked
Project relation; Monday rejected the whole batch; five rows were created with a
name and nothing else. Two guards now exist:

  1. resolve_ledger_project_link follows Operations.link_to_projects → Projects.
  2. upsert_invoice_row retries once without a rejected relation, and reports it.

Self-running and pytest-compatible. Monday is faked; no network.
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from adapters.monday import client as mc_mod  # noqa: E402
from shared.boards import OPERATIONS_BOARD_ID, PROJECTS_BOARD_ID  # noqa: E402

REJECTION = ("Monday API error: [{'message': 'There are items that are not in the "
             "connected boards', 'extensions': {'code': 'ColumnValueException', "
             "'error_data': {'column_type': 'board-relation', 'column_id': "
             f"'{mc_mod.INV_COL_LINKED_PROJECT}', 'column_validation_error_code': "
             "'itemsNotInConnectedBoards', 'column_name': 'Linked Project'}}}]")


class FakeMC(mc_mod.MondayClient):
    def __init__(self, *, item_board=None, linked=None, reject_relation=True):
        # skip the real __init__ (token/env); we only exercise the write paths
        self.calls = []
        self._item_board = item_board
        self._linked = linked or []
        self._reject = reject_relation

    def _query(self, query, variables=None):
        self.calls.append((query, variables))
        if "change_multiple_column_values" in query:
            values = json.loads(variables["values"])
            if self._reject and mc_mod.INV_COL_LINKED_PROJECT in values:
                raise RuntimeError(REJECTION)
            return {"change_multiple_column_values": {"id": variables["itemId"]}}
        if "create_item" in query:
            return {"create_item": {"id": "999"}}
        if "items(ids:" in query:
            return {"items": [{"id": "1", "board": {"id": str(self._item_board)},
                               "column_values": [{"id": "link_to_projects",
                                                  "linked_item_ids": self._linked}]}]}
        raise AssertionError(f"unexpected query: {query[:60]}")

    def find_invoice_row_by_document(self, identifier, board_id=None):
        return None


def _written_values(mc):
    return [json.loads(v["values"]) for q, v in mc.calls if "change_multiple" in q]


def test_rejected_relation_is_dropped_and_reported():
    mc = FakeMC()
    res = mc.upsert_invoice_row(
        identifier="2026-0910-01", item_name="Willow Creek", amount=19200,
        issue_date="2026-09-10", due_date="2026-10-10",
        linked_project_id=2734329664, stripe_invoice_id="in_x")
    assert res["action"] == "created"
    assert res["dropped_columns"] == [mc_mod.INV_COL_LINKED_PROJECT], res
    writes = _written_values(mc)
    assert len(writes) == 2, "expected one rejected write and one retry"
    assert mc_mod.INV_COL_LINKED_PROJECT not in writes[1]
    # everything ELSE about the invoice still landed
    assert writes[1][mc_mod.INV_COL_DOCUMENT] == "2026-0910-01"
    assert writes[1][mc_mod.INV_COL_STRIPE_INVOICE] == "in_x"
    assert writes[1][mc_mod.INV_COL_ISSUE_DATE] == {"date": "2026-09-10"}


def test_clean_write_reports_nothing_dropped():
    mc = FakeMC(reject_relation=False)
    res = mc.upsert_invoice_row(identifier="A", item_name="A", amount=1,
                                issue_date="2026-09-10", due_date="2026-10-10",
                                linked_project_id=1)
    assert res["dropped_columns"] == []
    assert len(_written_values(mc)) == 1


def test_strict_writes_still_raise_without_droppable():
    # stamp / paid writes must not silently half-apply
    mc = FakeMC()
    try:
        mc._set_invoice_columns(5, {mc_mod.INV_COL_LINKED_PROJECT: {"item_ids": [1]},
                                    mc_mod.INV_COL_STATUS: {"label": "Paid"}})
    except RuntimeError:
        return
    raise AssertionError("strict write should have raised")


def test_resolver_follows_ops_link_to_projects():
    mc = FakeMC(item_board=OPERATIONS_BOARD_ID, linked=["2725762273"])
    out = mc.resolve_ledger_project_link(2734329664)
    assert out == {"project_item_id": 2725762273, "source": "ops→projects", "note": None}, out


def test_resolver_accepts_projects_item_as_is():
    mc = FakeMC(item_board=PROJECTS_BOARD_ID)
    out = mc.resolve_ledger_project_link(2725762273)
    assert out["project_item_id"] == 2725762273 and out["source"] == "projects"


def test_resolver_notes_when_ops_item_has_no_project():
    mc = FakeMC(item_board=OPERATIONS_BOARD_ID, linked=[])
    out = mc.resolve_ledger_project_link(42)
    assert out["project_item_id"] is None
    assert "Operations item 42" in out["note"] and "link manually" in out["note"]


def test_resolver_handles_missing_id():
    assert FakeMC().resolve_ledger_project_link(None)["project_item_id"] is None


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for t in tests:
        try:
            t()
        except AssertionError as e:
            failed += 1
            print(f"FAIL {t.__name__}: {e}")
    if failed:
        sys.exit(1)
    print(f"OK — {len(tests)} tests passed.")
