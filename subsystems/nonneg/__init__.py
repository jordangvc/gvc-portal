"""5 Daily Non-Negotiables — Jordan's personal habit tracker.

Owner-only (route-gated to access.superadmin_emails(), NOT a grants feature —
a grants feature would be absorbed by every ``*`` admin's wildcard and the
whole point is that only Jordan can reach it). Data is per-email under
``portal/nonneg/`` so a second owner later gets their own tracker for free.

Pure logic in tracker.py; GCS persistence in store.py (mirrors the
generation-guarded contract every drafts-style store in this repo uses).
"""
