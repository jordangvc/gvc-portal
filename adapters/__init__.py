"""adapters — one module per external system.

Stripe, Google Drive/Gmail/GCS/Vision, Slack, Monday. ALL outbound I/O lives
here, behind functions/classes that raise the area's own *NotConfigured / error
types so callers can degrade gracefully.

Rule: adapters depend only on shared. Never import orchestrators, subsystems, or
app.
"""
