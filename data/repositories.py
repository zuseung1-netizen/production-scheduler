"""
Repository layer — abstracts all data access.
All DB operations go through these classes so swapping the backend is easy.
"""
import json
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from data.database import get_connection


# ─── helpers ────────────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def _rows_to_dicts(rows) -> List[Dict]:
    return [dict(r) for r in rows]


# ─── Config ──────────────────────────────────────────────────────────────────

class ConfigRepo:
    @staticmethod
    def get(key: str, default=None):
        with get_connection() as conn:
            row = conn.execute(
                "SELECT config_value FROM app_config WHERE config_key=?", (key,)
            ).fetchone()
        return row["config_value"] if row else default

    @staticmethod
    def set(key: str, value: str):
        with get_connection() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO app_config(config_key,config_value) VALUES(?,?)",
                (key, value))

    @staticmethod
    def all() -> List[Dict]:
        with get_connection() as conn:
            return _rows_to_dicts(conn.execute("SELECT * FROM app_config").fetchall())


# ─── SKU Master ───────────────────────────────────────────────────────────────

class SKURepo:
    @staticmethod
    def all() -> List[Dict]:
        with get_connection() as conn:
            return _rows_to_dicts(
                conn.execute("SELECT * FROM sku_master ORDER BY sku_code").fetchall())

    @staticmethod
    def get(sku_code: str) -> Optional[Dict]:
        with get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM sku_master WHERE sku_code=?", (sku_code,)).fetchone()
        return dict(row) if row else None

    @staticmethod
    def upsert(data: Dict):
        data["updated_at"] = _now()
        with get_connection() as conn:
            conn.execute("""
                INSERT INTO sku_master(sku_code,sku_name,uom,post_lead_days,note,updated_at)
                VALUES(:sku_code,:sku_name,:uom,:post_lead_days,:note,:updated_at)
                ON CONFLICT(sku_code) DO UPDATE SET
                    sku_name=excluded.sku_name, uom=excluded.uom,
                    post_lead_days=excluded.post_lead_days,
                    note=excluded.note, updated_at=excluded.updated_at
            """, data)

    @staticmethod
    def delete(sku_code: str):
        with get_connection() as conn:
            conn.execute("DELETE FROM sku_master WHERE sku_code=?", (sku_code,))

    @staticmethod
    def bulk_upsert(rows: List[Dict]):
        for r in rows: SKURepo.upsert(r)


# ─── Material Master ──────────────────────────────────────────────────────────

class MaterialRepo:
    @staticmethod
    def all() -> List[Dict]:
        with get_connection() as conn:
            return _rows_to_dicts(
                conn.execute(
                    "SELECT * FROM material_master ORDER BY material_code"
                ).fetchall())

    @staticmethod
    def get(material_code: str) -> Optional[Dict]:
        with get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM material_master WHERE material_code=?",
                (material_code,)).fetchone()
        return dict(row) if row else None

    @staticmethod
    def upsert(data: Dict):
        data["updated_at"] = _now()
        with get_connection() as conn:
            conn.execute("""
                INSERT INTO material_master
                    (material_code,material_name,uom,post_lead_days,note,updated_at)
                VALUES(:material_code,:material_name,:uom,:post_lead_days,:note,:updated_at)
                ON CONFLICT(material_code) DO UPDATE SET
                    material_name=excluded.material_name,
                    uom=excluded.uom,
                    post_lead_days=excluded.post_lead_days,
                    note=excluded.note,
                    updated_at=excluded.updated_at
            """, data)

    @staticmethod
    def delete(material_code: str):
        with get_connection() as conn:
            conn.execute(
                "DELETE FROM material_master WHERE material_code=?", (material_code,))

    @staticmethod
    def bulk_upsert(rows: List[Dict]):
        for r in rows: MaterialRepo.upsert(r)


# ─── Process Routing (unified SKU + MATERIAL) ────────────────────────────────

class ProcessRoutingRepo:
    """
    Unified routing for both SKU and MATERIAL entities.
    entity_type: 'SKU' | 'MATERIAL'
    entity_code: sku_code or material_code
    """

    @staticmethod
    def for_entity(entity_type: str, entity_code: str) -> List[Dict]:
        with get_connection() as conn:
            return _rows_to_dicts(conn.execute("""
                SELECT * FROM process_routing
                WHERE entity_type=? AND entity_code=?
                ORDER BY process_seq
            """, (entity_type, entity_code)).fetchall())

    @staticmethod
    def all() -> List[Dict]:
        with get_connection() as conn:
            return _rows_to_dicts(conn.execute(
                "SELECT * FROM process_routing ORDER BY entity_type,entity_code,process_seq"
            ).fetchall())

    @staticmethod
    def upsert(data: Dict):
        data.setdefault("requires_material_code", None)
        data.setdefault("min_gap_shifts", 0)
        data.setdefault("note", None)
        with get_connection() as conn:
            conn.execute("""
                INSERT INTO process_routing
                    (entity_type,entity_code,process_seq,process_name,
                     allowed_room_types,is_final_seq,requires_material_code,
                     min_gap_shifts,note)
                VALUES
                    (:entity_type,:entity_code,:process_seq,:process_name,
                     :allowed_room_types,:is_final_seq,:requires_material_code,
                     :min_gap_shifts,:note)
                ON CONFLICT(entity_type,entity_code,process_seq) DO UPDATE SET
                    process_name=excluded.process_name,
                    allowed_room_types=excluded.allowed_room_types,
                    is_final_seq=excluded.is_final_seq,
                    requires_material_code=excluded.requires_material_code,
                    min_gap_shifts=excluded.min_gap_shifts,
                    note=excluded.note
            """, data)

    @staticmethod
    def delete(entity_type: str, entity_code: str, process_seq: int):
        with get_connection() as conn:
            conn.execute("""
                DELETE FROM process_routing
                WHERE entity_type=? AND entity_code=? AND process_seq=?
            """, (entity_type, entity_code, process_seq))

    @staticmethod
    def delete_all_for_entity(entity_type: str, entity_code: str):
        with get_connection() as conn:
            conn.execute("""
                DELETE FROM process_routing
                WHERE entity_type=? AND entity_code=?
            """, (entity_type, entity_code))

    @staticmethod
    def bulk_upsert(rows: List[Dict]):
        for r in rows: ProcessRoutingRepo.upsert(r)

    @staticmethod
    def validate(entity_type: str, entity_code: str) -> Tuple[bool, str]:
        """
        Validates routing for an entity:
        1. At least one step exists
        2. Exactly one final step, must be highest seq
        3. allowed_room_types not empty
        4. Each process_name exists in room_master with matching room_type
        5. requires_material_code (if set) exists in material_master
        """
        steps = ProcessRoutingRepo.for_entity(entity_type, entity_code)
        if not steps:
            return False, f"{entity_type} {entity_code}: no routing defined"

        finals = [s for s in steps if s["is_final_seq"]]
        if len(finals) != 1:
            return False, (f"{entity_type} {entity_code}: "
                           f"must have exactly 1 final step (found {len(finals)})")

        max_seq = max(s["process_seq"] for s in steps)
        if finals[0]["process_seq"] != max_seq:
            return False, (f"{entity_type} {entity_code}: "
                           f"final step must be the last sequence")

        for step in steps:
            allowed = [t.strip() for t in
                       step["allowed_room_types"].split(",") if t.strip()]
            if not allowed:
                return False, (f"{entity_type} {entity_code} "
                               f"seq {step['process_seq']}: "
                               f"allowed_room_types is empty")
            matching = RoomRepo.rooms_for_process(step["process_name"], allowed)
            if not matching:
                return False, (f"{entity_type} {entity_code} "
                               f"seq {step['process_seq']}: no room found for "
                               f"process '{step['process_name']}' "
                               f"with room types {allowed}")
            mat = step.get("requires_material_code")
            if mat:
                if not MaterialRepo.get(mat):
                    return False, (f"{entity_type} {entity_code} "
                                   f"seq {step['process_seq']}: "
                                   f"material '{mat}' not found in material_master")

        return True, "OK"


# ─── Room Master ──────────────────────────────────────────────────────────────

class RoomRepo:
    @staticmethod
    def all() -> List[Dict]:
        with get_connection() as conn:
            return _rows_to_dicts(conn.execute(
                "SELECT * FROM room_master ORDER BY room_code,process_name"
            ).fetchall())

    @staticmethod
    def get(room_code: str, process_name: str) -> Optional[Dict]:
        with get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM room_master WHERE room_code=? AND process_name=?",
                (room_code, process_name)).fetchone()
        return dict(row) if row else None

    @staticmethod
    def rooms() -> List[str]:
        with get_connection() as conn:
            return [r["room_code"] for r in conn.execute(
                "SELECT DISTINCT room_code FROM room_master ORDER BY room_code"
            ).fetchall()]

    @staticmethod
    def room_types() -> List[str]:
        with get_connection() as conn:
            return [r["room_type"] for r in conn.execute(
                "SELECT DISTINCT room_type FROM room_master ORDER BY room_type"
            ).fetchall()]

    @staticmethod
    def processes_for_room(room_code: str) -> List[Dict]:
        with get_connection() as conn:
            return _rows_to_dicts(conn.execute(
                "SELECT * FROM room_master WHERE room_code=? ORDER BY process_name",
                (room_code,)).fetchall())

    @staticmethod
    def rooms_for_process(process_name: str,
                          allowed_room_types: List[str] = None) -> List[Dict]:
        with get_connection() as conn:
            rows = _rows_to_dicts(conn.execute(
                "SELECT * FROM room_master WHERE process_name=? ORDER BY room_code",
                (process_name,)).fetchall())
        if allowed_room_types:
            rows = [r for r in rows if r["room_type"] in allowed_room_types]
        return rows

    @staticmethod
    def upsert(data: Dict):
        with get_connection() as conn:
            conn.execute("""
                INSERT INTO room_master
                    (room_code,process_name,process_type,room_type,
                     upph,uph_fixed,hc_min,hc_max,hc_fixed,note)
                VALUES
                    (:room_code,:process_name,:process_type,:room_type,
                     :upph,:uph_fixed,:hc_min,:hc_max,:hc_fixed,:note)
                ON CONFLICT(room_code,process_name) DO UPDATE SET
                    process_type=excluded.process_type,
                    room_type=excluded.room_type,
                    upph=excluded.upph,
                    uph_fixed=excluded.uph_fixed,
                    hc_min=excluded.hc_min,
                    hc_max=excluded.hc_max,
                    hc_fixed=excluded.hc_fixed,
                    note=excluded.note
            """, data)

    @staticmethod
    def delete(room_code: str, process_name: str):
        with get_connection() as conn:
            conn.execute(
                "DELETE FROM room_master WHERE room_code=? AND process_name=?",
                (room_code, process_name))

    @staticmethod
    def bulk_upsert(rows: List[Dict]):
        for r in rows: RoomRepo.upsert(r)


# ─── Shift Config ─────────────────────────────────────────────────────────────

class ShiftRepo:
    @staticmethod
    def all() -> List[Dict]:
        with get_connection() as conn:
            return _rows_to_dicts(conn.execute(
                "SELECT * FROM shift_config ORDER BY shift_no").fetchall())

    @staticmethod
    def upsert(data: Dict):
        with get_connection() as conn:
            conn.execute("""
                INSERT INTO shift_config(shift_no,shift_name,start_time,end_time)
                VALUES(:shift_no,:shift_name,:start_time,:end_time)
                ON CONFLICT(shift_no) DO UPDATE SET
                    shift_name=excluded.shift_name,
                    start_time=excluded.start_time,
                    end_time=excluded.end_time
            """, data)


# ─── Calendar ─────────────────────────────────────────────────────────────────

class CalendarRepo:
    @staticmethod
    def get_open_slots(date_from: str, date_to: str) -> List[Dict]:
        with get_connection() as conn:
            return _rows_to_dicts(conn.execute("""
                SELECT * FROM calendar
                WHERE cal_date BETWEEN ? AND ?
                  AND is_open=1 AND is_hold=0
                ORDER BY cal_date,shift_no,room_code
            """, (date_from, date_to)).fetchall())

    @staticmethod
    def set_slot(cal_date: str, shift_no: int, room_code: str,
                 is_open: int = 1, is_hold: int = 0):
        with get_connection() as conn:
            conn.execute("""
                INSERT INTO calendar(cal_date,shift_no,room_code,is_open,is_hold)
                VALUES(?,?,?,?,?)
                ON CONFLICT(cal_date,shift_no,room_code) DO UPDATE SET
                    is_open=excluded.is_open, is_hold=excluded.is_hold
            """, (cal_date, shift_no, room_code, is_open, is_hold))

    @staticmethod
    def get_unavailable_slots(date_from: str, date_to: str) -> List[Dict]:
        """Return calendar records that are closed (is_open=0) or on hold (is_hold=1)."""
        with get_connection() as conn:
            return _rows_to_dicts(conn.execute("""
                SELECT * FROM calendar
                WHERE cal_date BETWEEN ? AND ?
                  AND (is_open=0 OR is_hold=1)
                ORDER BY cal_date, shift_no, room_code
            """, (date_from, date_to)).fetchall())

    @staticmethod
    def get_slot(cal_date: str, shift_no: int,
                 room_code: str) -> Optional[Dict]:
        with get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM calendar WHERE cal_date=? AND shift_no=? AND room_code=?",
                (cal_date, shift_no, room_code)).fetchone()
        return dict(row) if row else None


# ─── Sales Order ──────────────────────────────────────────────────────────────

class SORepo:
    @staticmethod
    def all(status: str = None) -> List[Dict]:
        with get_connection() as conn:
            if status:
                rows = conn.execute(
                    "SELECT * FROM sales_order WHERE status=? "
                    "ORDER BY priority,received_at",
                    (status,)).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM sales_order ORDER BY status,priority,received_at"
                ).fetchall()
        return _rows_to_dicts(rows)

    @staticmethod
    def get(so_number: str, sku_code: str, line_item: str) -> Optional[Dict]:
        with get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM sales_order "
                "WHERE so_number=? AND sku_code=? AND line_item=?",
                (so_number, sku_code, line_item)).fetchone()
        return dict(row) if row else None

    @staticmethod
    def upsert(data: Dict, batch_id: str = None) -> str:
        existing = SORepo.get(
            data["so_number"], data["sku_code"], data["line_item"])
        now = _now()
        if not existing:
            data.setdefault("received_at", now)
            data.setdefault("status", "OPEN")
            data.setdefault("customer_name", None)
            with get_connection() as conn:
                conn.execute("""
                    INSERT INTO sales_order
                        (so_number,sku_code,line_item,customer_name,qty,due_date,priority,
                         received_at,status,start_no_earlier,note)
                    VALUES
                        (:so_number,:sku_code,:line_item,:customer_name,:qty,:due_date,:priority,
                         :received_at,:status,:start_no_earlier,:note)
                """, data)
            if batch_id:
                _log_so_history(batch_id, data, "NEW", None, data)
            return "NEW"

        changed = {k for k in ("qty","due_date","priority","status","note")
                   if str(existing.get(k,"")) != str(data.get(k,""))}
        if not changed:
            return "UNCHANGED"

        with get_connection() as conn:
            conn.execute("""
                UPDATE sales_order SET
                    customer_name=:customer_name,
                    qty=:qty, due_date=:due_date, priority=:priority,
                    status=:status, start_no_earlier=:start_no_earlier, note=:note
                WHERE so_number=:so_number AND sku_code=:sku_code
                  AND line_item=:line_item
            """, data)
        if batch_id:
            _log_so_history(batch_id, data, "MODIFIED", existing, data)
        return "MODIFIED"

    @staticmethod
    def close(so_number: str, sku_code: str, line_item: str,
              batch_id: str = None):
        existing = SORepo.get(so_number, sku_code, line_item)
        if not existing:
            return
        with get_connection() as conn:
            conn.execute(
                "UPDATE sales_order SET status='CLOSED' "
                "WHERE so_number=? AND sku_code=? AND line_item=?",
                (so_number, sku_code, line_item))
        if batch_id:
            _log_so_history(
                batch_id,
                {"so_number": so_number, "sku_code": sku_code,
                 "line_item": line_item},
                "CLOSED", existing, {**existing, "status": "CLOSED"})

    @staticmethod
    def hold(so_number: str, sku_code: str, line_item: str, hold: bool):
        with get_connection() as conn:
            conn.execute(
                "UPDATE sales_order SET status=? "
                "WHERE so_number=? AND sku_code=? AND line_item=?",
                ("HOLD" if hold else "OPEN",
                 so_number, sku_code, line_item))

    @staticmethod
    def set_priority(so_number: str, sku_code: str, line_item: str,
                     priority: Optional[int]):
        with get_connection() as conn:
            conn.execute(
                "UPDATE sales_order SET priority=? "
                "WHERE so_number=? AND sku_code=? AND line_item=?",
                (priority, so_number, sku_code, line_item))

    @staticmethod
    def history(so_number: str = None) -> List[Dict]:
        with get_connection() as conn:
            if so_number:
                rows = conn.execute(
                    "SELECT * FROM so_history WHERE so_number=? "
                    "ORDER BY changed_at DESC",
                    (so_number,)).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM so_history ORDER BY changed_at DESC LIMIT 500"
                ).fetchall()
        return _rows_to_dicts(rows)

    @staticmethod
    def save_snapshot(batch_id: str):
        with get_connection() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO so_snapshot"
                "(batch_id,snapshot_data,created_at) VALUES(?,?,?)",
                (batch_id,
                 json.dumps(SORepo.all(), ensure_ascii=False),
                 _now()))

    @staticmethod
    def list_snapshots() -> List[Dict]:
        with get_connection() as conn:
            return _rows_to_dicts(conn.execute(
                "SELECT batch_id,created_at FROM so_snapshot "
                "ORDER BY created_at DESC").fetchall())

    @staticmethod
    def rollback(batch_id: str):
        with get_connection() as conn:
            row = conn.execute(
                "SELECT snapshot_data FROM so_snapshot WHERE batch_id=?",
                (batch_id,)).fetchone()
        if not row:
            raise ValueError(f"Snapshot {batch_id} not found")
        data = json.loads(row["snapshot_data"])
        with get_connection() as conn:
            conn.execute("DELETE FROM sales_order")
            for so in data:
                conn.execute("""
                    INSERT INTO sales_order
                        (so_number,sku_code,line_item,qty,due_date,priority,
                         received_at,status,start_no_earlier,note)
                    VALUES
                        (:so_number,:sku_code,:line_item,:qty,:due_date,:priority,
                         :received_at,:status,:start_no_earlier,:note)
                """, so)

    @staticmethod
    def unplanned() -> List[Dict]:
        """OPEN SOs with at least one routing step whose planned qty is
        insufficient. Returns one row per (SO, process step) combination
        where step_planned < production_needed, so a missing middle step
        appears even when the final step is already scheduled."""
        result = []
        for so in SORepo.all(status="OPEN"):
            needed = AllocationRepo.production_needed(
                so["so_number"], so["sku_code"], so["line_item"])
            if needed <= 0:
                continue
            steps = ProcessRoutingRepo.for_entity("SKU", so["sku_code"])
            if not steps:
                result.append({**so, "production_needed": needed,
                               "process_seq": None,
                               "process_name": "(no routing)",
                               "is_final_seq": 0,
                               "step_planned_qty": 0,
                               "remaining_qty": needed})
                continue
            for step in steps:
                seq = step["process_seq"]
                step_planned = PlanRepo.planned_qty_for_step(
                    so["so_number"], so["sku_code"], so["line_item"], seq)
                remaining = needed - step_planned
                if remaining > 0:
                    result.append({
                        **so,
                        "production_needed": needed,
                        "process_seq": seq,
                        "process_name": step["process_name"],
                        "is_final_seq": step["is_final_seq"],
                        "step_planned_qty": step_planned,
                        "remaining_qty": remaining,
                    })
        result.sort(key=lambda r: (
            r.get("due_date") or "",
            r.get("priority") if r.get("priority") is not None else 999,
            r.get("process_seq") or 0,
        ))
        return result


def _log_so_history(batch_id, key_data, change_type, old_val, new_val):
    with get_connection() as conn:
        conn.execute("""
            INSERT INTO so_history
                (upload_batch,so_number,sku_code,line_item,
                 change_type,old_value,new_value,changed_at)
            VALUES (?,?,?,?,?,?,?,?)
        """, (
            batch_id,
            key_data.get("so_number"), key_data.get("sku_code"),
            key_data.get("line_item"),
            change_type,
            json.dumps(old_val, ensure_ascii=False, default=str) if old_val else None,
            json.dumps(new_val, ensure_ascii=False, default=str) if new_val else None,
            _now()))


# ─── Production Plan ──────────────────────────────────────────────────────────

class PlanRepo:
    @staticmethod
    def all(date_from: str = None, date_to: str = None,
            entity_type: str = None) -> List[Dict]:
        with get_connection() as conn:
            clauses, params = [], []
            if date_from and date_to:
                clauses.append("plan_date BETWEEN ? AND ?")
                params += [date_from, date_to]
            if entity_type:
                clauses.append("entity_type=?")
                params.append(entity_type)
            where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
            rows = conn.execute(
                f"SELECT * FROM production_plan {where} "
                f"ORDER BY plan_date,shift_no,room_code",
                params).fetchall()
        return _rows_to_dicts(rows)

    @staticmethod
    def get(plan_id: int) -> Optional[Dict]:
        with get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM production_plan WHERE plan_id=?",
                (plan_id,)).fetchone()
        return dict(row) if row else None

    @staticmethod
    def insert(data: Dict) -> int:
        now = _now()
        data.setdefault("created_at", now)
        data.setdefault("updated_at", now)
        data.setdefault("entity_type", "SKU")
        data.setdefault("entity_code", data.get("sku_code", ""))
        data.setdefault("so_number", "")
        data.setdefault("sku_code", "")
        data.setdefault("line_item", "")
        data.setdefault("process_seq", 1)
        data.setdefault("is_final_seq", 0)
        data.setdefault("qty_produced", 0)
        data.setdefault("is_locked", 0)
        data.setdefault("is_consolidated", 0)
        data.setdefault("consolidation_group", None)
        data.setdefault("material_group_id", None)
        data.setdefault("block_type", None)
        data.setdefault("memo", None)
        with get_connection() as conn:
            cur = conn.execute("""
                INSERT INTO production_plan
                    (entity_type,entity_code,so_number,sku_code,line_item,
                     process_name,process_seq,is_final_seq,room_code,
                     plan_date,shift_no,qty_planned,qty_produced,
                     is_locked,is_consolidated,consolidation_group,
                     material_group_id,block_type,memo,created_at,updated_at)
                VALUES
                    (:entity_type,:entity_code,:so_number,:sku_code,:line_item,
                     :process_name,:process_seq,:is_final_seq,:room_code,
                     :plan_date,:shift_no,:qty_planned,:qty_produced,
                     :is_locked,:is_consolidated,:consolidation_group,
                     :material_group_id,:block_type,:memo,:created_at,:updated_at)
            """, data)
            return cur.lastrowid

    @staticmethod
    def update(plan_id: int, fields: Dict, reason: str = None):
        old = PlanRepo.get(plan_id)
        fields["updated_at"] = _now()
        set_clause = ", ".join(f"{k}=:{k}" for k in fields)
        fields["plan_id"] = plan_id
        with get_connection() as conn:
            conn.execute(
                f"UPDATE production_plan SET {set_clause} "
                f"WHERE plan_id=:plan_id", fields)
        _log_plan_history(plan_id, "MODIFIED", old,
                          {**(old or {}), **fields}, reason)

    @staticmethod
    def delete(plan_id: int, reason: str = None):
        old = PlanRepo.get(plan_id)
        with get_connection() as conn:
            conn.execute(
                "DELETE FROM production_plan WHERE plan_id=?", (plan_id,))
        if old:
            _log_plan_history(plan_id, "DELETED", old, None, reason)

    @staticmethod
    def delete_all(date_from: str = None, date_to: str = None,
                   reason: str = None) -> int:
        """Bulk-clear plans (optionally restricted to a date range), along
        with any material_demand_group rows that become orphaned. Returns
        the number of plan rows deleted."""
        clauses, params = [], []
        if date_from and date_to:
            clauses.append("plan_date BETWEEN ? AND ?")
            params += [date_from, date_to]
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        mat_where = "WHERE " + " AND ".join(clauses + ["material_group_id IS NOT NULL"]) \
            if clauses else "WHERE material_group_id IS NOT NULL"

        with get_connection() as conn:
            n = conn.execute(
                f"SELECT COUNT(*) AS t FROM production_plan {where}",
                params).fetchone()["t"]
            group_ids = [r["material_group_id"] for r in conn.execute(
                f"SELECT DISTINCT material_group_id FROM production_plan {mat_where}",
                params).fetchall()]
            conn.execute(f"DELETE FROM production_plan {where}", params)
            if group_ids:
                placeholders = ",".join("?" * len(group_ids))
                conn.execute(
                    f"DELETE FROM material_demand_group "
                    f"WHERE group_id IN ({placeholders})", group_ids)
            conn.execute("""
                INSERT INTO plan_history
                    (plan_id, action, old_value, new_value, reason, changed_at)
                VALUES (NULL, 'BULK_CLEARED', ?, NULL, ?, ?)
            """, (json.dumps({"deleted_count": n}), reason, _now()))
        return n

    @staticmethod
    def delete_unlocked(date_from: str, date_to: str) -> int:
        """Clear ALL unlocked plans (SKU + MATERIAL) in a date range and
        orphaned demand-group rows. Called at the start of auto_plan() so
        that every run starts with a clean slate — locked plans are kept."""
        clauses = ["is_locked=0", "plan_date BETWEEN ? AND ?"]
        params = [date_from, date_to]
        where = "WHERE " + " AND ".join(clauses)
        mat_where = "WHERE " + " AND ".join(
            clauses + ["material_group_id IS NOT NULL"])
        with get_connection() as conn:
            n = conn.execute(
                f"SELECT COUNT(*) AS t FROM production_plan {where}",
                params).fetchone()["t"]
            group_ids = [r["material_group_id"] for r in conn.execute(
                f"SELECT DISTINCT material_group_id FROM production_plan {mat_where}",
                params).fetchall()]
            conn.execute(f"DELETE FROM production_plan {where}", params)
            if group_ids:
                placeholders = ",".join("?" * len(group_ids))
                conn.execute(
                    f"DELETE FROM material_demand_group "
                    f"WHERE group_id IN ({placeholders})", group_ids)
        return n

    @staticmethod
    def delete_unlocked_material(date_from: str, date_to: str) -> int:
        """Clear unlocked MATERIAL plans (and orphaned demand-group rows)
        in a date range. Called before re-deriving material demand so
        auto_plan() doesn't stack a fresh duplicate layer on every run —
        locked material plans are left untouched."""
        clauses = ["entity_type='MATERIAL'", "is_locked=0",
                   "plan_date BETWEEN ? AND ?"]
        params = [date_from, date_to]
        where = "WHERE " + " AND ".join(clauses)
        mat_where = "WHERE " + " AND ".join(
            clauses + ["material_group_id IS NOT NULL"])
        with get_connection() as conn:
            n = conn.execute(
                f"SELECT COUNT(*) AS t FROM production_plan {where}",
                params).fetchone()["t"]
            group_ids = [r["material_group_id"] for r in conn.execute(
                f"SELECT DISTINCT material_group_id FROM production_plan {mat_where}",
                params).fetchall()]
            conn.execute(f"DELETE FROM production_plan {where}", params)
            if group_ids:
                placeholders = ",".join("?" * len(group_ids))
                conn.execute(
                    f"DELETE FROM material_demand_group "
                    f"WHERE group_id IN ({placeholders})", group_ids)
        return n

    @staticmethod
    def lock(plan_id: int, locked: bool):
        with get_connection() as conn:
            conn.execute(
                "UPDATE production_plan SET is_locked=? WHERE plan_id=?",
                (1 if locked else 0, plan_id))
        _log_plan_history(plan_id, "LOCKED" if locked else "UNLOCKED",
                          None, None, None)

    @staticmethod
    def for_so(so_number: str, sku_code: str,
               line_item: str) -> List[Dict]:
        with get_connection() as conn:
            return _rows_to_dicts(conn.execute(
                "SELECT * FROM production_plan "
                "WHERE so_number=? AND sku_code=? AND line_item=? "
                "ORDER BY plan_date,shift_no",
                (so_number, sku_code, line_item)).fetchall())

    @staticmethod
    def for_material_group(group_id: str) -> List[Dict]:
        with get_connection() as conn:
            return _rows_to_dicts(conn.execute(
                "SELECT * FROM production_plan "
                "WHERE material_group_id=? ORDER BY plan_date,shift_no",
                (group_id,)).fetchall())

    @staticmethod
    def planned_qty(so_number: str, sku_code: str,
                    line_item: str) -> int:
        with get_connection() as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(qty_planned),0) as t "
                "FROM production_plan "
                "WHERE so_number=? AND sku_code=? AND line_item=?",
                (so_number, sku_code, line_item)).fetchone()
        return int(row["t"])

    @staticmethod
    def final_planned_qty(so_number: str, sku_code: str,
                          line_item: str) -> int:
        """Sum of qty_planned at the FINAL process step only. A multi-step
        routing creates one full set of rows per step (each summing to the
        same quantity), so plain planned_qty() over-counts by the number
        of steps — it's fine for the scheduler's own re-plan guard, but
        wrong for "how much of this SO is actually scheduled to complete"."""
        with get_connection() as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(qty_planned),0) as t "
                "FROM production_plan "
                "WHERE so_number=? AND sku_code=? AND line_item=? "
                "AND is_final_seq=1",
                (so_number, sku_code, line_item)).fetchone()
        return int(row["t"])

    @staticmethod
    def planned_qty_for_step(so_number: str, sku_code: str,
                              line_item: str, process_seq: int) -> int:
        with get_connection() as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(qty_planned),0) as t "
                "FROM production_plan "
                "WHERE so_number=? AND sku_code=? AND line_item=? AND process_seq=?",
                (so_number, sku_code, line_item, process_seq)).fetchone()
        return int(row["t"])

    @staticmethod
    def plan_history(plan_id: int = None) -> List[Dict]:
        with get_connection() as conn:
            if plan_id:
                rows = conn.execute(
                    "SELECT * FROM plan_history WHERE plan_id=? "
                    "ORDER BY changed_at DESC",
                    (plan_id,)).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM plan_history "
                    "ORDER BY changed_at DESC LIMIT 1000"
                ).fetchall()
        return _rows_to_dicts(rows)


def _log_plan_history(plan_id, action, old_val, new_val, reason):
    with get_connection() as conn:
        conn.execute("""
            INSERT INTO plan_history
                (plan_id,action,old_value,new_value,reason,changed_at)
            VALUES (?,?,?,?,?,?)
        """, (
            plan_id, action,
            json.dumps(old_val, ensure_ascii=False, default=str)
            if old_val else None,
            json.dumps(new_val, ensure_ascii=False, default=str)
            if new_val else None,
            reason, _now()))


# ─── Material Demand Group ────────────────────────────────────────────────────

class MaterialDemandRepo:
    @staticmethod
    def insert_group_member(data: Dict):
        data.setdefault("created_at", _now())
        with get_connection() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO material_demand_group
                    (group_id,material_code,so_number,sku_code,
                     line_item,due_date,qty_required,created_at)
                VALUES
                    (:group_id,:material_code,:so_number,:sku_code,
                     :line_item,:due_date,:qty_required,:created_at)
            """, data)

    @staticmethod
    def for_group(group_id: str) -> List[Dict]:
        with get_connection() as conn:
            return _rows_to_dicts(conn.execute(
                "SELECT * FROM material_demand_group WHERE group_id=? "
                "ORDER BY due_date",
                (group_id,)).fetchall())

    @staticmethod
    def for_groups_bulk(group_ids: List[str]) -> Dict[str, List[Dict]]:
        """Return {group_id: [members]} for all given IDs in one query."""
        if not group_ids:
            return {}
        placeholders = ",".join("?" * len(group_ids))
        with get_connection() as conn:
            rows = _rows_to_dicts(conn.execute(
                f"SELECT * FROM material_demand_group "
                f"WHERE group_id IN ({placeholders}) ORDER BY due_date",
                group_ids).fetchall())
        result: Dict[str, List[Dict]] = {}
        for r in rows:
            result.setdefault(r["group_id"], []).append(r)
        return result

    @staticmethod
    def for_material(material_code: str) -> List[Dict]:
        with get_connection() as conn:
            return _rows_to_dicts(conn.execute(
                "SELECT * FROM material_demand_group "
                "WHERE material_code=? ORDER BY due_date",
                (material_code,)).fetchall())

    @staticmethod
    def delete_group(group_id: str):
        with get_connection() as conn:
            conn.execute(
                "DELETE FROM material_demand_group WHERE group_id=?",
                (group_id,))


# ─── Production Actuals ───────────────────────────────────────────────────────

class ActualRepo:
    @staticmethod
    def insert(data: Dict) -> int:
        data["entered_at"] = _now()
        data.setdefault("entity_type", "SKU")
        data.setdefault("entity_code", data.get("sku_code", ""))
        data.setdefault("so_number", "")
        data.setdefault("sku_code", "")
        data.setdefault("line_item", "")
        with get_connection() as conn:
            cur = conn.execute("""
                INSERT INTO production_actual
                    (entity_type,entity_code,plan_id,so_number,sku_code,
                     line_item,lot_number,room_code,process_name,
                     actual_date,shift_no,qty_actual,entered_at,note)
                VALUES
                    (:entity_type,:entity_code,:plan_id,:so_number,:sku_code,
                     :line_item,:lot_number,:room_code,:process_name,
                     :actual_date,:shift_no,:qty_actual,:entered_at,:note)
            """, data)
            return cur.lastrowid

    @staticmethod
    def for_plan(plan_id: int) -> List[Dict]:
        with get_connection() as conn:
            return _rows_to_dicts(conn.execute(
                "SELECT * FROM production_actual "
                "WHERE plan_id=? ORDER BY entered_at",
                (plan_id,)).fetchall())

    @staticmethod
    def for_so(so_number: str, sku_code: str,
               line_item: str) -> List[Dict]:
        with get_connection() as conn:
            return _rows_to_dicts(conn.execute(
                "SELECT * FROM production_actual "
                "WHERE so_number=? AND sku_code=? AND line_item=? "
                "ORDER BY actual_date,shift_no",
                (so_number, sku_code, line_item)).fetchall())

    @staticmethod
    def actual_qty(so_number: str, sku_code: str,
                   line_item: str) -> int:
        with get_connection() as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(qty_actual),0) as t "
                "FROM production_actual "
                "WHERE so_number=? AND sku_code=? AND line_item=?",
                (so_number, sku_code, line_item)).fetchone()
        return int(row["t"])

    @staticmethod
    def for_entity(entity_type: str, entity_code: str) -> List[Dict]:
        with get_connection() as conn:
            return _rows_to_dicts(conn.execute(
                "SELECT * FROM production_actual "
                "WHERE entity_type=? AND entity_code=? "
                "ORDER BY actual_date,shift_no",
                (entity_type, entity_code)).fetchall())

    @staticmethod
    def recent(limit: int = 200) -> List[Dict]:
        with get_connection() as conn:
            return _rows_to_dicts(conn.execute(
                "SELECT * FROM production_actual "
                "ORDER BY entered_at DESC LIMIT ?",
                (limit,)).fetchall())


# ─── LOT Sample ───────────────────────────────────────────────────────────────

class LotSampleRepo:
    @staticmethod
    def insert(data: Dict) -> int:
        data["entered_at"] = _now()
        data.setdefault("entity_type", "SKU")
        data.setdefault("so_number", "")
        data.setdefault("sku_code", "")
        data.setdefault("line_item", "")
        data.setdefault("sample_qty", 0)
        data.setdefault("reject_qty", 0)
        data.setdefault("note", None)
        with get_connection() as conn:
            cur = conn.execute("""
                INSERT INTO lot_sample
                    (actual_id,entity_type,entity_code,lot_number,
                     so_number,sku_code,line_item,
                     sample_qty,reject_qty,entered_at,note)
                VALUES
                    (:actual_id,:entity_type,:entity_code,:lot_number,
                     :so_number,:sku_code,:line_item,
                     :sample_qty,:reject_qty,:entered_at,:note)
            """, data)
            return cur.lastrowid

    @staticmethod
    def for_actual(actual_id: int) -> List[Dict]:
        with get_connection() as conn:
            return _rows_to_dicts(conn.execute(
                "SELECT * FROM lot_sample WHERE actual_id=? "
                "ORDER BY entered_at",
                (actual_id,)).fetchall())

    @staticmethod
    def for_so(so_number: str, sku_code: str,
               line_item: str) -> List[Dict]:
        with get_connection() as conn:
            return _rows_to_dicts(conn.execute(
                "SELECT * FROM lot_sample "
                "WHERE so_number=? AND sku_code=? AND line_item=? "
                "ORDER BY entered_at",
                (so_number, sku_code, line_item)).fetchall())

    @staticmethod
    def total_sample_qty(so_number: str, sku_code: str,
                         line_item: str) -> int:
        with get_connection() as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(sample_qty),0) as t FROM lot_sample "
                "WHERE so_number=? AND sku_code=? AND line_item=?",
                (so_number, sku_code, line_item)).fetchone()
        return int(row["t"])

    @staticmethod
    def total_reject_qty(so_number: str, sku_code: str,
                         line_item: str) -> int:
        with get_connection() as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(reject_qty),0) as t FROM lot_sample "
                "WHERE so_number=? AND sku_code=? AND line_item=?",
                (so_number, sku_code, line_item)).fetchone()
        return int(row["t"])

    @staticmethod
    def net_qty(so_number: str, sku_code: str, line_item: str) -> int:
        """Deliverable qty = actual - sample - reject."""
        actual   = ActualRepo.actual_qty(so_number, sku_code, line_item)
        sampled  = LotSampleRepo.total_sample_qty(so_number, sku_code, line_item)
        rejected = LotSampleRepo.total_reject_qty(so_number, sku_code, line_item)
        return actual - sampled - rejected

    @staticmethod
    def all_for_entity(entity_type: str,
                       entity_code: str) -> List[Dict]:
        with get_connection() as conn:
            return _rows_to_dicts(conn.execute(
                "SELECT * FROM lot_sample "
                "WHERE entity_type=? AND entity_code=? "
                "ORDER BY entered_at",
                (entity_type, entity_code)).fetchall())

    @staticmethod
    def update(sample_id: int, sample_qty: int, reject_qty: int = 0,
               note: str = None):
        with get_connection() as conn:
            conn.execute(
                "UPDATE lot_sample SET sample_qty=?, reject_qty=?, note=? "
                "WHERE sample_id=?",
                (sample_qty, reject_qty, note, sample_id))

    @staticmethod
    def delete(sample_id: int):
        with get_connection() as conn:
            conn.execute(
                "DELETE FROM lot_sample WHERE sample_id=?", (sample_id,))


# ─── Inventory ───────────────────────────────────────────────────────────────

class InventoryRepo:
    """
    Manages pre-existing stock lots uploaded by the planner.
    qty_remaining = qty_available - SUM(so_inventory_allocation.qty_allocated)
    """

    @staticmethod
    def all(sku_code: str = None, status: str = None) -> List[Dict]:
        with get_connection() as conn:
            clauses, params = [], []
            if sku_code:
                clauses.append("i.sku_code=?"); params.append(sku_code)
            if status:
                clauses.append("i.status=?"); params.append(status)
            where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
            rows = conn.execute(f"""
                SELECT i.*,
                       COALESCE(SUM(a.qty_allocated),0) AS qty_allocated,
                       i.qty_available - COALESCE(SUM(a.qty_allocated),0)
                           AS qty_remaining
                FROM inventory i
                LEFT JOIN so_inventory_allocation a ON a.inv_id = i.inv_id
                {where}
                GROUP BY i.inv_id
                ORDER BY i.sku_code, i.expiry_date, i.production_date
            """, params).fetchall()
        return _rows_to_dicts(rows)

    @staticmethod
    def get(inv_id: int) -> Optional[Dict]:
        with get_connection() as conn:
            row = conn.execute("""
                SELECT i.*,
                       COALESCE(SUM(a.qty_allocated),0) AS qty_allocated,
                       i.qty_available - COALESCE(SUM(a.qty_allocated),0)
                           AS qty_remaining
                FROM inventory i
                LEFT JOIN so_inventory_allocation a ON a.inv_id = i.inv_id
                WHERE i.inv_id=?
                GROUP BY i.inv_id
            """, (inv_id,)).fetchone()
        return dict(row) if row else None

    @staticmethod
    def available_for_sku(sku_code: str) -> List[Dict]:
        """FEFO-sorted available lots with remaining qty > 0."""
        rows = InventoryRepo.all(sku_code=sku_code, status="AVAILABLE")
        return [r for r in rows if r["qty_remaining"] > 0]

    @staticmethod
    def total_available(sku_code: str) -> int:
        rows = InventoryRepo.available_for_sku(sku_code)
        return sum(r["qty_remaining"] for r in rows)

    @staticmethod
    def upsert(data: Dict) -> int:
        now = _now()
        data.setdefault("status", "AVAILABLE")
        data.setdefault("note", None)
        data.setdefault("production_date", None)
        data.setdefault("expiry_date", None)
        data["updated_at"] = now
        with get_connection() as conn:
            # Try update first, then insert
            existing = conn.execute(
                "SELECT inv_id FROM inventory WHERE sku_code=? AND lot_number=?",
                (data["sku_code"], data["lot_number"])
            ).fetchone()
            if existing:
                conn.execute("""
                    UPDATE inventory SET
                        qty_available=:qty_available,
                        production_date=:production_date,
                        expiry_date=:expiry_date,
                        status=:status, note=:note, updated_at=:updated_at
                    WHERE sku_code=:sku_code AND lot_number=:lot_number
                """, data)
                return existing["inv_id"]
            else:
                data["created_at"] = now
                cur = conn.execute("""
                    INSERT INTO inventory
                        (sku_code, lot_number, qty_available,
                         production_date, expiry_date, status,
                         note, created_at, updated_at)
                    VALUES
                        (:sku_code, :lot_number, :qty_available,
                         :production_date, :expiry_date, :status,
                         :note, :created_at, :updated_at)
                """, data)
                return cur.lastrowid

    @staticmethod
    def bulk_upsert(rows: List[Dict]):
        for r in rows:
            InventoryRepo.upsert(r)

    @staticmethod
    def delete(inv_id: int):
        with get_connection() as conn:
            conn.execute(
                "DELETE FROM inventory WHERE inv_id=?", (inv_id,))

    @staticmethod
    def update_status(inv_id: int, status: str):
        with get_connection() as conn:
            conn.execute(
                "UPDATE inventory SET status=?, updated_at=? WHERE inv_id=?",
                (status, _now(), inv_id))

    @staticmethod
    def fefo_suggestion(sku_code: str, qty_needed: int) -> List[Dict]:
        """
        FEFO auto-suggestion: returns a list of (inv_id, lot_number,
        qty_to_allocate) covering qty_needed, sorted by expiry_date ASC.
        Lots without expiry_date go last.
        """
        available = InventoryRepo.available_for_sku(sku_code)
        suggestion, remaining = [], qty_needed
        for lot in available:
            if remaining <= 0:
                break
            alloc = min(lot["qty_remaining"], remaining)
            suggestion.append({
                "inv_id":          lot["inv_id"],
                "lot_number":      lot["lot_number"],
                "expiry_date":     lot["expiry_date"],
                "qty_remaining":   lot["qty_remaining"],
                "qty_to_allocate": alloc,
            })
            remaining -= alloc
        return suggestion


# ─── SO Inventory Allocation ──────────────────────────────────────────────────

class AllocationRepo:
    """
    Links inventory lots to SO-LineItems.
    Confirmed by planner after FEFO suggestion.
    """

    @staticmethod
    def for_so(so_number: str, sku_code: str,
               line_item: str) -> List[Dict]:
        with get_connection() as conn:
            return _rows_to_dicts(conn.execute("""
                SELECT a.*, i.expiry_date, i.production_date,
                       i.qty_available, i.status AS inv_status
                FROM so_inventory_allocation a
                JOIN inventory i ON i.inv_id = a.inv_id
                WHERE a.so_number=? AND a.sku_code=? AND a.line_item=?
                ORDER BY i.expiry_date
            """, (so_number, sku_code, line_item)).fetchall())

    @staticmethod
    def for_lot(inv_id: int) -> List[Dict]:
        with get_connection() as conn:
            return _rows_to_dicts(conn.execute(
                "SELECT * FROM so_inventory_allocation WHERE inv_id=? "
                "ORDER BY allocated_at",
                (inv_id,)).fetchall())

    @staticmethod
    def total_allocated(so_number: str, sku_code: str,
                        line_item: str) -> int:
        with get_connection() as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(qty_allocated),0) AS t "
                "FROM so_inventory_allocation "
                "WHERE so_number=? AND sku_code=? AND line_item=?",
                (so_number, sku_code, line_item)).fetchone()
        return int(row["t"])

    @staticmethod
    def production_needed(so_number: str, sku_code: str,
                          line_item: str) -> int:
        """SO qty minus allocated inventory = qty that needs to be produced."""
        with get_connection() as conn:
            so = conn.execute(
                "SELECT qty FROM sales_order "
                "WHERE so_number=? AND sku_code=? AND line_item=?",
                (so_number, sku_code, line_item)).fetchone()
        if not so:
            return 0
        allocated = AllocationRepo.total_allocated(
            so_number, sku_code, line_item)
        actual = ActualRepo.actual_qty(so_number, sku_code, line_item)
        return max(0, so["qty"] - allocated - actual)

    @staticmethod
    def allocate(so_number: str, sku_code: str, line_item: str,
                 inv_id: int, lot_number: str,
                 qty: int, note: str = None) -> int:
        now = _now()
        with get_connection() as conn:
            cur = conn.execute("""
                INSERT INTO so_inventory_allocation
                    (so_number, sku_code, line_item,
                     inv_id, lot_number, qty_allocated,
                     allocated_at, note)
                VALUES (?,?,?,?,?,?,?,?)
            """, (so_number, sku_code, line_item,
                  inv_id, lot_number, qty, now, note))
            alloc_id = cur.lastrowid
        # Refresh inventory status
        inv = InventoryRepo.get(inv_id)
        if inv and inv["qty_remaining"] <= 0:
            InventoryRepo.update_status(inv_id, "ALLOCATED")
        return alloc_id

    @staticmethod
    def deallocate(alloc_id: int):
        """Remove an allocation and restore inventory status if needed."""
        with get_connection() as conn:
            row = conn.execute(
                "SELECT inv_id FROM so_inventory_allocation "
                "WHERE alloc_id=?", (alloc_id,)).fetchone()
            conn.execute(
                "DELETE FROM so_inventory_allocation WHERE alloc_id=?",
                (alloc_id,))
        if row:
            InventoryRepo.update_status(row["inv_id"], "AVAILABLE")

    @staticmethod
    def confirm_fefo_suggestion(so_number: str, sku_code: str,
                                 line_item: str,
                                 suggestion: List[Dict],
                                 note: str = None) -> List[int]:
        """
        Bulk-confirm a FEFO suggestion list.
        suggestion = [{"inv_id":..., "lot_number":..., "qty_to_allocate":...}]
        Returns list of alloc_ids.
        """
        alloc_ids = []
        for s in suggestion:
            aid = AllocationRepo.allocate(
                so_number, sku_code, line_item,
                s["inv_id"], s["lot_number"],
                s["qty_to_allocate"], note)
            alloc_ids.append(aid)
        return alloc_ids

    @staticmethod
    def all_allocations() -> List[Dict]:
        with get_connection() as conn:
            return _rows_to_dicts(conn.execute("""
                SELECT a.*, i.expiry_date, i.production_date
                FROM so_inventory_allocation a
                JOIN inventory i ON i.inv_id = a.inv_id
                ORDER BY a.allocated_at DESC
            """).fetchall())


# ─── Legacy alias (keeps older imports working) ───────────────────────────────
# Old code referenced SKUProcessRepo — redirect to ProcessRoutingRepo
class SKUProcessRepo:
    @staticmethod
    def for_sku(sku_code: str) -> List[Dict]:
        return ProcessRoutingRepo.for_entity("SKU", sku_code)

    @staticmethod
    def all() -> List[Dict]:
        return [r for r in ProcessRoutingRepo.all() if r["entity_type"] == "SKU"]

    @staticmethod
    def upsert(data: Dict):
        data["entity_type"] = "SKU"
        data["entity_code"] = data.pop("sku_code", data.get("entity_code", ""))
        ProcessRoutingRepo.upsert(data)

    @staticmethod
    def delete(sku_code: str, process_seq: int):
        ProcessRoutingRepo.delete("SKU", sku_code, process_seq)

    @staticmethod
    def delete_all_for_sku(sku_code: str):
        ProcessRoutingRepo.delete_all_for_entity("SKU", sku_code)

    @staticmethod
    def bulk_upsert(rows: List[Dict]):
        for r in rows: SKUProcessRepo.upsert(r)

    @staticmethod
    def validate_routing(sku_code: str) -> Tuple[bool, str]:
        return ProcessRoutingRepo.validate("SKU", sku_code)
