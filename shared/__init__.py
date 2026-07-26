"""shared — cross-cutting primitives with no portal-internal dependencies.

paths (repo-relative locations), money (formatting), boards (Monday board IDs),
errors (the HTTP {code,detail,advice} envelope), access, auth, portal_store
(GCS), activity (audit log).

Rule: everything may import shared; shared imports nothing else internal. This is
the bottom of the dependency graph.
"""
