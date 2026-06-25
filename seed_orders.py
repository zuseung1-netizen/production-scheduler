#!/usr/bin/env python3
"""
seed_orders.py — Generate 300 demo Sales Orders for testing.

Usage:
    python seed_orders.py

Creates:
  - demo_crp.xlsx  (June 1 ~ October 31, 70 HC/shift)
  - 300 OPEN SOs distributed across June-October
    with a bottleneck concentration in August 1-20
"""
import os
import sys
import random
import sqlite3
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from data.database import init_db, DB_PATH
init_db()

from data.repositories import SKURepo, SORepo, ConfigRepo

CUSTOMERS = [
    "Seoul Medical", "Busan Pharma", "Incheon Hospital", "Daejeon Health",
    "Gwangju Medical", "Suwon Clinic", "Ulsan Healthcare", "Jeju Pharma",
    "Daegu Hospital", "Yonsei Medical",
]

# Preferred SKU codes (use these if they exist in the DB)
PREFERRED_SKUS = ["SKU-A10", "SKU-A30", "SKU-B04", "SKU-C30", "SKU-E10"]


def make_demo_crp(path: str) -> None:
    """Create demo_crp.xlsx covering 2026-06-01 to 2026-10-31, 70 HC/shift."""
    try:
        import openpyxl
        from openpyxl import Workbook
    except ImportError:
        print("[ERR] openpyxl not installed. Run: pip install openpyxl")
        sys.exit(1)

    wb = Workbook()
    ws = wb.active
    ws.title = "CRP"

    start = date(2026, 6, 1)
    end   = date(2026, 10, 31)
    dates = []
    cur = start
    while cur <= end:
        dates.append(cur)
        cur += timedelta(days=1)

    # Header: ShiftNo | 2026-06-01 | 2026-06-02 | ...
    ws.append(["ShiftNo"] + [d.strftime("%Y-%m-%d") for d in dates])
    ws.append([1] + [70] * len(dates))
    ws.append([2] + [70] * len(dates))

    # HOLD sheet (required by CRPManager, but empty)
    hold_ws = wb.create_sheet("HOLD")
    hold_ws.append(["Date", "RoomCode", "ShiftNo"])

    wb.save(path)


def clear_open_sos() -> int:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute("SELECT COUNT(*) FROM sales_order WHERE status='OPEN'")
    n = cur.fetchone()[0]
    conn.execute("DELETE FROM sales_order WHERE status='OPEN'")
    conn.commit()
    conn.close()
    return n


def main():
    rng = random.Random(42)

    # ── 1. Read existing SKUs ────────────────────────────────────────────────
    skus = SKURepo.all()
    if not skus:
        print("[ERR] No SKU masters found.")
        print("   Please upload SKU/Room/Routing masters first (Masters tab).")
        sys.exit(1)

    sku_map = {s["sku_code"]: int(s.get("uom") or 1) for s in skus}

    # Use preferred SKUs if available, otherwise use first 5
    use_codes = [c for c in PREFERRED_SKUS if c in sku_map]
    if not use_codes:
        use_codes = list(sku_map.keys())[:5]
    use_skus = [(c, sku_map[c]) for c in use_codes]

    print(f"Using SKUs: {[s[0] for s in use_skus]}")

    # ── 2. Create demo_crp.xlsx ──────────────────────────────────────────────
    crp_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "demo_crp.xlsx")
    make_demo_crp(crp_path)
    ConfigRepo.set("crp_excel_path", crp_path)
    print(f"[OK] demo_crp.xlsx created (2026-06-01 ~ 2026-10-31, 70 HC/shift)")
    print(f"   Path: {crp_path}")

    # ── 3. Clear existing OPEN SOs ───────────────────────────────────────────
    existing = len(SORepo.all(status="OPEN"))
    if existing:
        ans = input(
            f"\n현재 OPEN SO {existing}건이 있습니다. "
            f"삭제 후 새 데이터로 교체하시겠습니까? [y/N]: "
        )
        if ans.strip().lower() == "y":
            deleted = clear_open_sos()
            print(f"[OK] 기존 OPEN SO {deleted}건 삭제")
        else:
            print("기존 SO 유지. 새 SO를 추가합니다.")
    else:
        print("기존 OPEN SO 없음.")

    # ── 4. Generate 300 SOs ──────────────────────────────────────────────────
    # Phase definitions: (label, start, end, count, qty_range, pri_range)
    phases = [
        ("phase1", date(2026, 6,  1),  date(2026, 7, 31),  90,  ( 50,  250), (3, 8)),
        ("phase2", date(2026, 8,  1),  date(2026, 8, 20), 120,  (300,  700), (1, 4)),
        ("phase3", date(2026, 8, 21),  date(2026, 10, 31), 90,  ( 50,  200), (3, 8)),
    ]

    so_num = 1
    counts = {}

    for label, d_start, d_end, count, qty_range, pri_range in phases:
        span = (d_end - d_start).days
        phase_count = 0

        for _ in range(count):
            # Random due date within the phase window
            offset = rng.randint(0, span)
            due = d_start + timedelta(days=offset)
            # Skip Sundays for slightly more realistic dates
            if due.weekday() == 6:
                due = due + timedelta(days=1)
                if due > d_end:
                    due = d_end

            sku_code, uom = use_skus[so_num % len(use_skus)]
            qty      = rng.randint(*qty_range)
            priority = rng.randint(*pri_range)
            customer = CUSTOMERS[so_num % len(CUSTOMERS)]

            # received_at: 30~90 days before due (for pull-in scenario testing)
            days_before = rng.randint(30, min(90, (due - date(2026, 1, 1)).days))
            received_dt = due - timedelta(days=days_before)
            received_str = received_dt.strftime("%Y-%m-%d 09:00:00")

            SORepo.upsert({
                "so_number":     f"SO-DEMO-{so_num:04d}",
                "sku_code":      sku_code,
                "line_item":     "001",
                "qty":           qty,
                "due_date":      due.strftime("%Y-%m-%d"),
                "priority":      priority,
                "status":        "OPEN",
                "customer_name": customer,
                "order_type":    "CUSTOMER",
                "received_at":   received_str,
                "note":          f"Demo {label}",
            })
            so_num += 1
            phase_count += 1

        counts[label] = phase_count

    total = sum(counts.values())
    print(f"\n[OK] SO {total}건 생성 완료")
    print(f"   Phase 1 (Jun 1  ~ Jul 31): {counts['phase1']:>3} orders (normal)")
    print(f"   Phase 2 (Aug 1  ~ Aug 20): {counts['phase2']:>3} orders (BOTTLENECK - high qty concentrated)")
    print(f"   Phase 3 (Aug 21 ~ Oct 31): {counts['phase3']:>3} orders (normal)")
    print("""
Next steps:
  1. python main.py
  2. Masters > App Config: verify CRP path (set to demo_crp.xlsx)
     Click Refresh in CRP tab after confirming
  3. Gantt tab -> Auto-Plan
  4. Check August Cap% bar (expect red >90% in Aug 1-20)
  5. Alerts tab: check LATE SO list
  6. Use Pull-Forward button to relieve August bottleneck
""")


if __name__ == "__main__":
    main()
