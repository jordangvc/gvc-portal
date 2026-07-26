"""Money + date formatting helpers (extracted from invoice.py)."""
from __future__ import annotations

from datetime import date, datetime


def to_cents(amount: float | int) -> int:
    """Stripe wants integer cents. Round half-up to avoid float drift."""
    return int(round(float(amount) * 100))

def fmt_money(amount: float | int, *, signed: bool = False) -> str:
    """$1,234.56 — always show 2 decimals. signed=True keeps the minus on discounts."""
    n = float(amount)
    sign = "-" if (signed and n < 0) else ""
    return f"{sign}${abs(n):,.2f}"

def fmt_qty(qty: float | int) -> str:
    """Integer qty renders without decimals; fractional qty shows up to 3."""
    n = float(qty)
    if n == int(n):
        return f"{int(n)}"
    return f"{n:.3f}".rstrip("0").rstrip(".")

def fmt_date(d: str | date) -> str:
    """YYYY-MM-DD → 'May 15, 2026'."""
    if isinstance(d, str):
        d = datetime.strptime(d, "%Y-%m-%d").date()
    return d.strftime("%b %d, %Y")
