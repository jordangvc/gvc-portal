"""
Field Guide content model — schemas, validation, catalog load, search, render.
=============================================================================
The live Field Manual shell remains ``web/fieldguide.html``. This package is
the scalable content layer: stable procedure IDs, searchable metadata,
governance fields, and HTML render helpers that match the existing CSS
contracts (``.doc``, ``.plainwords``, ``.steps``, ``.expert``, ``.nextpath``).

Imports: bottom of the graph (shared + stdlib only from here upward).
"""
from __future__ import annotations

from subsystems.fieldguide import catalog as catalog
from subsystems.fieldguide import render as render
from subsystems.fieldguide import schema as schema
from subsystems.fieldguide import search as search
from subsystems.fieldguide import validate as validate

__all__ = [
    "catalog",
    "render",
    "schema",
    "search",
    "validate",
]
