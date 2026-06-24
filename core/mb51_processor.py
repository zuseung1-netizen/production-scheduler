"""
MB51 Processor — applies SAP material document movements to inventory
and re-runs SO allocation for affected SKUs.

Movement type sign convention:
  101 GR              → +qty (add to inventory)
  102 GR reversal     → -qty
  331 Sampling GI     → -qty (deduct from inventory)
  332 Sampling reversal → +qty
"""
from collections import defaultdict
from typing import Dict, List, Tuple

from data.repositories import AllocationRepo, InventoryRepo, MB51Repo


_MOVEMENT_SIGN: Dict[str, int] = {
    "101": +1,
    "102": -1,
    "331": -1,
    "332": +1,
}


class MB51Processor:

    def process(self, rows: List[Dict]) -> Dict:
        """
        Process a list of MB51 rows (from parse_mb51).
        Skips already-processed (material_document, movement_type) pairs.
        Returns result summary dict.
        """
        already = MB51Repo.processed_docs()

        new_rows = []
        for r in rows:
            mv  = str(r.get("movement_type") or "").strip().lstrip("0")
            doc = str(r.get("material_document") or "").strip()
            # Skip rows with no document number — cannot dedup safely
            if not doc:
                continue
            if (doc, mv) not in already:
                new_rows.append(r)

        if not new_rows:
            return {
                "new_docs": 0,
                "skipped_docs": len(rows),
                "affected_skus": [],
                "lot_changes": {},
                "allocation_stats": {},
            }

        # Group deltas by (sku, lot)
        lot_deltas: Dict[Tuple[str, str], float] = defaultdict(float)
        lot_posting: Dict[Tuple[str, str], str]  = {}

        for r in new_rows:
            mv  = str(r.get("movement_type") or "").strip().lstrip("0")
            sku = str(r.get("material")       or "").strip()
            lot = str(r.get("batch")          or "").strip()
            try:
                qty = float(r.get("quantity") or 0)
            except (TypeError, ValueError):
                qty = 0.0
            sign = _MOVEMENT_SIGN.get(mv, 0)
            if not sign or not sku or not lot:
                continue
            # SAP sometimes exports reversal rows with negative qty already.
            # Normalise to absolute value so sign always comes from movement type.
            lot_deltas[(sku, lot)] += sign * abs(qty)
            if (sku, lot) not in lot_posting:
                pd = r.get("posting_date")
                lot_posting[(sku, lot)] = (
                    str(pd)[:10] if pd is not None else None)

        # Apply inventory adjustments — mark docs only after ALL succeed
        affected_skus: set = set()
        lot_changes: Dict[str, float] = {}
        try:
            for (sku, lot), delta in lot_deltas.items():
                if delta == 0:
                    continue
                InventoryRepo.adjust_qty_from_mb51(
                    sku, lot, delta, lot_posting.get((sku, lot)))
                affected_skus.add(sku)
                lot_changes[f"{sku}/{lot}"] = delta
        except Exception:
            # Do NOT mark docs as processed — let the caller see the exception
            raise

        # Mark documents as processed (only reached if all adjustments succeeded)
        for r in new_rows:
            MB51Repo.insert_doc(r)

        # Re-allocate for each affected SKU
        allocation_stats: Dict[str, Dict] = {}
        for sku in affected_skus:
            allocation_stats[sku] = AllocationRepo.reallocate_sku(sku)

        return {
            "new_docs":        len(new_rows),
            "skipped_docs":    len(rows) - len(new_rows),
            "affected_skus":   sorted(affected_skus),
            "lot_changes":     lot_changes,
            "allocation_stats": allocation_stats,
        }
