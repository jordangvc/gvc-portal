"""app — web layer (FastAPI).

Routes, auth guards, and request models ONLY. Handlers parse the request,
resolve the signed-in user, and DELEGATE to an orchestrator. No cross-system
business logic lives here.

Depends on: orchestrators, subsystems, adapters, shared.
"""
