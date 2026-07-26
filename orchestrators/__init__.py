"""orchestrators — end-to-end business flows.

One flow per operation (bill an invoice, record a check, draft an estimate,
create a change order). A flow coordinates adapters + subsystems and owns the
multi-system side-effect sequencing, idempotency, and error envelope.

Rule: orchestrators may import subsystems, adapters, and shared. They must NOT
import app. Two apps that need to cooperate do so through an orchestrator (or a
shared subsystem function) — never by importing each other's internals.
"""
