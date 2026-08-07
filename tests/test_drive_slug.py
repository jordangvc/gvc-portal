"""Pure tests for Drive folder/file name sanitization."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from adapters.drive import slug_for_path  # noqa: E402


def test_slug_strips_pipe_and_illegal_chars():
    assert slug_for_path(
        '9195 Silva Drive, Cincinnati, OH 45241 | Willow Creek | Smith residence'
    ) == (
        "9195 Silva Drive, Cincinnati, OH 45241 Willow Creek Smith residence"
    )
    assert "/" not in slug_for_path("a/b:c*d?e\"f<g>h|i")
    assert "\\" not in slug_for_path("a\\b")


def test_slug_default_max_len_fits_typical_three_part_title():
    name = (
        "150 West Dorothy Lane, Kettering, OH 45429 | "
        "Terraces Senior Apartments | Terraces Renovation"
    )
    slug = slug_for_path(name)
    assert len(slug) <= 200
    assert "Terraces Renovation" in slug
    assert "150 West Dorothy" in slug


def test_slug_long_titles_do_not_collide_on_job_title_tail():
    """Regression: head-only [:80] made these two Job Titles identical."""
    left = (
        "150 West Dorothy Lane, Kettering, OH 45429 | "
        "Terraces Senior Apartments | Terraces Senior Living Renovation"
    )
    right = (
        "150 West Dorothy Lane, Kettering, OH 45429 | "
        "Terraces Senior Apartments | Terraces Senior Living Phase 2"
    )
    # Force the overflow regime so the head+tail strategy is exercised.
    a = slug_for_path(left, max_len=80)
    b = slug_for_path(right, max_len=80)
    assert a != b
    assert "Renovation" in a
    assert "Phase 2" in b
    assert len(a) <= 80 and len(b) <= 80


def test_slug_short_names_unchanged():
    assert slug_for_path("Short Name") == "Short Name"
