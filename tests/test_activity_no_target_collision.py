"""
Regression: activity_detail.summarize() returns a dict that INCLUDES `target`.
Any log_event call that splats it AND passes target= explicitly raises
"got multiple values for keyword argument 'target'" at call time — after the
real work is done, so the user sees a 500 for a request that succeeded
(estimate finalize, Sep 8–11 2026; billing.search had the same shape).

Static AST scan of app/service.py — no import of the service needed.
"""
import ast
import os
import sys

HERE = os.path.dirname(__file__)
SERVICE = os.path.join(HERE, "..", "app", "service.py")


def _collisions():
    with open(SERVICE, encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), SERVICE)
    hits = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
        if name != "log_event":
            continue
        explicit_target = any(kw.arg == "target" for kw in node.keywords)
        splats_summarize = any(
            kw.arg is None and isinstance(kw.value, ast.Call)
            and getattr(kw.value.func, "attr", getattr(kw.value.func, "id", "")) == "summarize"
            for kw in node.keywords)
        if explicit_target and splats_summarize:
            hits.append(node.lineno)
    return hits


def test_no_log_event_passes_target_and_splats_summarize():
    hits = _collisions()
    assert not hits, f"target= collides with **summarize() at service.py lines {hits}"


if __name__ == "__main__":
    try:
        test_no_log_event_passes_target_and_splats_summarize()
    except AssertionError as e:
        print("FAIL", e)
        sys.exit(1)
    print("OK — 1 test passed.")
