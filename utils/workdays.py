"""
Workday arithmetic using the `holidays` library (Korean public holidays).
"""
from datetime import date, timedelta
from typing import Dict

try:
    import holidays as _holidays_lib
    _HAS_HOLIDAYS = True
except ImportError:
    _HAS_HOLIDAYS = False

_kr_cache: Dict[int, object] = {}


def _kr_holidays(year: int):
    if year not in _kr_cache:
        if _HAS_HOLIDAYS:
            _kr_cache[year] = _holidays_lib.KR(years=year)
        else:
            _kr_cache[year] = set()
    return _kr_cache[year]


def is_workday(d: date) -> bool:
    """Return True if d is a Korean workday (not weekend, not public holiday)."""
    if d.weekday() >= 5:          # 5=Sat, 6=Sun
        return False
    return d not in _kr_holidays(d.year)


def sub_workdays(base: date, n: int) -> date:
    """Return the date that is n Korean workdays before base."""
    if n <= 0:
        return base
    d = base
    remaining = n
    while remaining > 0:
        d -= timedelta(days=1)
        if is_workday(d):
            remaining -= 1
    return d


def add_workdays(base: date, n: int) -> date:
    """Return the date that is n Korean workdays after base."""
    if n <= 0:
        return base
    d = base
    remaining = n
    while remaining > 0:
        d += timedelta(days=1)
        if is_workday(d):
            remaining -= 1
    return d
