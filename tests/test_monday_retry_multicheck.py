"""MondayClient retry + multi-check commit fail-safe."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def test_is_retryable_monday_classifies():
    import requests
    from adapters.monday import client as mc_mod

    assert mc_mod._is_retryable_monday(RuntimeError("x"), status=429) is True
    assert mc_mod._is_retryable_monday(RuntimeError("x"), status=503) is True
    assert mc_mod._is_retryable_monday(
        RuntimeError("Monday API error: ComplexityException budget")
    ) is True
    assert mc_mod._is_retryable_monday(RuntimeError("INVALID_COLUMN")) is False
    err = requests.HTTPError()
    err.response = MagicMock(status_code=429)
    assert mc_mod._is_retryable_monday(err) is True


def test_query_retries_on_429_then_succeeds():
    import adapters.monday.client as mc_mod

    sleeps: list[float] = []
    mc_mod._TRACE_ENABLED = False

    class _Resp429:
        status_code = 429
        headers = {"Retry-After": "0"}

        def raise_for_status(self):
            import requests
            err = requests.HTTPError("429")
            err.response = self
            raise err

        def json(self):
            return {}

    class _RespOk:
        status_code = 200
        headers = {}

        def raise_for_status(self):
            return None

        def json(self):
            return {"data": {"me": {"name": "ok"}}}

    with patch.dict("os.environ", {"MONDAY_API_TOKEN": "tok"}):
        client = mc_mod.MondayClient(token="tok")
        client.session = MagicMock()
        client.session.post.side_effect = [_Resp429(), _RespOk()]
        with patch.object(mc_mod, "_sleep", side_effect=lambda s: sleeps.append(s)):
            out = client._query("query { me { name } }")
    assert out == {"me": {"name": "ok"}}
    assert client.session.post.call_count == 2
    assert sleeps  # backed off once


def test_query_does_not_retry_non_retryable_graphql():
    import adapters.monday.client as mc_mod

    mc_mod._TRACE_ENABLED = False

    class _Resp:
        status_code = 200
        headers = {}

        def raise_for_status(self):
            return None

        def json(self):
            return {"errors": [{"message": "ColumnDoesNotExist"}]}

    with patch.dict("os.environ", {"MONDAY_API_TOKEN": "tok"}):
        client = mc_mod.MondayClient(token="tok")
        client.session = MagicMock()
        client.session.post.return_value = _Resp()
        with patch.object(mc_mod, "_sleep") as sleep:
            with pytest.raises(RuntimeError, match="ColumnDoesNotExist"):
                client._query("query { boards { id } }")
            sleep.assert_not_called()
    assert client.session.post.call_count == 1


def test_commit_rejects_multi_check_image():
    from orchestrators import check_flow
    from subsystems.checks import deposit as check_deposit

    class _VisionOk:
        @staticmethod
        def ocr_text(b):
            return "PAY TO THE ORDER OF\nPAY TO THE ORDER OF"

    orig_vision = check_flow.vision
    check_flow.vision = _VisionOk()
    assert check_deposit.count_checks(_VisionOk.ocr_text(b"")) >= 2
    try:
        with pytest.raises(HTTPException) as ei:
            check_flow.commit_check(
                monday_item_ids=[1],
                image_bytes=b"fake",
                content_type="image/jpeg",
                check_no="1",
                amount="10.00",
                date_str="2026-08-08",
                email="a@x.com",
            )
        assert ei.value.status_code == 409
        assert ei.value.detail["code"] == "MULTI_CHECK_IMAGE"
    finally:
        check_flow.vision = orig_vision
