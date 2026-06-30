"""
Database layer - SQLite backend with repository pattern.
Designed so storage backend can be swapped (SQLite <-> Excel) easily.
"""
import sqlite3
import os

# Absolute path — DB location never depends on CWD.
# planner.db lives in the project root (one level above data/).
_HERE   = os.path.dirname(os.path.abspath(__file__))  # .../data/
_ROOT   = os.path.dirname(_HERE)                       # project root
DB_PATH = os.path.join(_ROOT, "planner.db")

# Module-level persistent connection — opened once, reused for every query.
# check_same_thread=False is safe here: the GUI runs on the main thread and
# all repo calls happen on the same thread (or protected by the GIL in QThread).
_conn: sqlite3.Connection | None = None


def get_connection() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode = WAL")    # persistent; readers never block writers
        _conn.execute("PRAGMA synchronous = NORMAL")  # fsync only at WAL checkpoint, not every commit
        _conn.execute("PRAGMA cache_size = -32000")   # 32 MB page cache
        _conn.execute("PRAGMA foreign_keys = ON")
    return _conn


def init_db():
    conn = get_connection()
    c = conn.cursor()

    # ── SKU Master ───────────────────────────────────────────────────────────
    c.execute("""
    CREATE TABLE IF NOT EXISTS sku_master (
        sku_code        TEXT PRIMARY KEY,
        sku_name        TEXT NOT NULL,
        uom             INTEGER NOT NULL DEFAULT 1,
        post_lead_days  INTEGER NOT NULL DEFAULT 0,
        campaign_mode   INTEGER NOT NULL DEFAULT 1,
        note            TEXT,
        updated_at      TEXT
    )""")

    # Migration: add campaign_mode to existing DBs
    sku_cols = [r[1] for r in c.execute("PRAGMA table_info(sku_master)").fetchall()]
    if "campaign_mode" not in sku_cols:
        c.execute("ALTER TABLE sku_master ADD COLUMN campaign_mode INTEGER NOT NULL DEFAULT 1")

    # ── Material Master ──────────────────────────────────────────────────────
    # Semi-finished goods (반제품). No SO — demand derived from SKU plans.
    c.execute("""
    CREATE TABLE IF NOT EXISTS material_master (
        material_code   TEXT PRIMARY KEY,
        material_name   TEXT NOT NULL,
        uom             INTEGER NOT NULL DEFAULT 1,
        post_lead_days  INTEGER NOT NULL DEFAULT 0,  -- QC lead time before usable
        note            TEXT,
        updated_at      TEXT
    )""")

    # ── Process Routing (unified for SKU and MATERIAL) ───────────────────────
    # Replaces sku_process. entity_type = 'SKU' or 'MATERIAL'.
    # requires_material_code: if set, this step needs that material to be
    #   available before it can start (material must finish prod + post_lead).
    c.execute("""
    CREATE TABLE IF NOT EXISTS process_routing (
        entity_type             TEXT NOT NULL CHECK(entity_type IN ('SKU','MATERIAL')),
        entity_code             TEXT NOT NULL,   -- sku_code or material_code
        process_seq             INTEGER NOT NULL,
        process_name            TEXT NOT NULL,
        allowed_room_types      TEXT NOT NULL,   -- comma-separated
        is_final_seq            INTEGER NOT NULL DEFAULT 0,
        requires_material_code  TEXT,            -- material needed before this step
        min_gap_shifts          INTEGER NOT NULL DEFAULT 0,  -- empty shifts required between prev step end and this step start
        note                    TEXT,
        PRIMARY KEY (entity_type, entity_code, process_seq)
    )""")

    # Migration: add/rename gap column for existing DBs
    existing_cols = [r[1] for r in c.execute(
        "PRAGMA table_info(process_routing)").fetchall()]
    if "min_gap_shifts" not in existing_cols:
        c.execute(
            "ALTER TABLE process_routing ADD COLUMN min_gap_shifts INTEGER NOT NULL DEFAULT 0"
        )
    if "min_gap_hours" in existing_cols:
        try:
            c.execute("ALTER TABLE process_routing DROP COLUMN min_gap_hours")
        except Exception:
            pass  # SQLite < 3.35 — column stays but is unused
    if "room_type_priority" not in existing_cols:
        c.execute(
            "ALTER TABLE process_routing ADD COLUMN room_type_priority TEXT"
        )

    # ── Company Holiday (사내 휴무일) ─────────────────────────────────────────
    c.execute("""
    CREATE TABLE IF NOT EXISTS company_holiday (
        cal_date  TEXT PRIMARY KEY,
        name      TEXT NOT NULL DEFAULT ''
    )""")

    # ── Production Room Master ───────────────────────────────────────────────
    c.execute("""
    CREATE TABLE IF NOT EXISTS room_master (
        room_code           TEXT NOT NULL,
        process_name        TEXT NOT NULL,
        process_type        TEXT NOT NULL CHECK(process_type IN ('AUTO','MANUAL')),
        room_type           TEXT NOT NULL,
        upph                REAL,
        uph_fixed           REAL,
        hc_min              INTEGER,
        hc_max              INTEGER,
        hc_fixed            INTEGER,
        changeover_shifts   INTEGER NOT NULL DEFAULT 0,
        note                TEXT,
        PRIMARY KEY (room_code, process_name)
    )""")

    # Migration: add changeover_shifts to existing DBs
    room_cols = [r[1] for r in c.execute("PRAGMA table_info(room_master)").fetchall()]
    if "changeover_shifts" not in room_cols:
        c.execute("ALTER TABLE room_master ADD COLUMN changeover_shifts INTEGER NOT NULL DEFAULT 0")

    # ── Shift Config ─────────────────────────────────────────────────────────
    c.execute("""
    CREATE TABLE IF NOT EXISTS shift_config (
        shift_no        INTEGER PRIMARY KEY,
        shift_name      TEXT NOT NULL,
        start_time      TEXT NOT NULL,
        end_time        TEXT NOT NULL
    )""")

    # ── Calendar ─────────────────────────────────────────────────────────────
    c.execute("""
    CREATE TABLE IF NOT EXISTS calendar (
        cal_date        TEXT NOT NULL,
        shift_no        INTEGER NOT NULL,
        room_code       TEXT NOT NULL,
        is_open         INTEGER NOT NULL DEFAULT 1,
        is_hold         INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (cal_date, shift_no, room_code),
        FOREIGN KEY (shift_no) REFERENCES shift_config(shift_no)
    )""")

    # Migration: add deduct_minutes to existing DBs
    cal_cols = [r[1] for r in c.execute("PRAGMA table_info(calendar)").fetchall()]
    if "deduct_minutes" not in cal_cols:
        c.execute("ALTER TABLE calendar ADD COLUMN deduct_minutes INTEGER NOT NULL DEFAULT 0")

    # ── Sales Orders ─────────────────────────────────────────────────────────
    c.execute("""
    CREATE TABLE IF NOT EXISTS sales_order (
        so_number           TEXT NOT NULL,
        sku_code            TEXT NOT NULL,
        line_item           TEXT NOT NULL,
        customer_name       TEXT,
        qty                 INTEGER NOT NULL,
        due_date            TEXT NOT NULL,
        committed_due_date  TEXT,
        priority            INTEGER,
        received_at         TEXT NOT NULL,
        status              TEXT NOT NULL DEFAULT 'OPEN',
        start_no_earlier    TEXT,
        note                TEXT,
        split_from          TEXT,
        order_type          TEXT NOT NULL DEFAULT 'CUSTOMER',
        department          TEXT,
        purpose             TEXT,
        requester           TEXT,
        PRIMARY KEY (so_number, sku_code, line_item),
        FOREIGN KEY (sku_code) REFERENCES sku_master(sku_code)
    )""")

    # Migration: add columns to existing DBs
    so_cols = [r[1] for r in c.execute("PRAGMA table_info(sales_order)").fetchall()]
    if "committed_due_date" not in so_cols:
        c.execute("ALTER TABLE sales_order ADD COLUMN committed_due_date TEXT")
    if "split_from" not in so_cols:
        c.execute("ALTER TABLE sales_order ADD COLUMN split_from TEXT")
    if "order_type" not in so_cols:
        c.execute("ALTER TABLE sales_order ADD COLUMN order_type TEXT NOT NULL DEFAULT 'CUSTOMER'")
    if "department" not in so_cols:
        c.execute("ALTER TABLE sales_order ADD COLUMN department TEXT")
    if "purpose" not in so_cols:
        c.execute("ALTER TABLE sales_order ADD COLUMN purpose TEXT")
    if "requester" not in so_cols:
        c.execute("ALTER TABLE sales_order ADD COLUMN requester TEXT")

    # ── SO Change History ────────────────────────────────────────────────────
    c.execute("""
    CREATE TABLE IF NOT EXISTS so_history (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        upload_batch    TEXT NOT NULL,
        so_number       TEXT,
        sku_code        TEXT,
        line_item       TEXT,
        change_type     TEXT NOT NULL,
        old_value       TEXT,
        new_value       TEXT,
        changed_at      TEXT NOT NULL
    )""")

    # ── SO Snapshots ─────────────────────────────────────────────────────────
    c.execute("""
    CREATE TABLE IF NOT EXISTS so_snapshot (
        batch_id        TEXT NOT NULL,
        snapshot_data   TEXT NOT NULL,
        created_at      TEXT NOT NULL,
        PRIMARY KEY (batch_id)
    )""")

    # ── Production Plan ───────────────────────────────────────────────────────
    # Covers both SKU plans (so_number set) and Material plans (so_number = '').
    # entity_type: SKU or MATERIAL
    # entity_code: sku_code or material_code
    # material_group_id: links material plan to its demand group (merged due dates)
    c.execute("""
    CREATE TABLE IF NOT EXISTS production_plan (
        plan_id             INTEGER PRIMARY KEY AUTOINCREMENT,
        entity_type         TEXT NOT NULL DEFAULT 'SKU',
        entity_code         TEXT NOT NULL,
        so_number           TEXT NOT NULL DEFAULT '',
        sku_code            TEXT NOT NULL DEFAULT '',
        line_item           TEXT NOT NULL DEFAULT '',
        process_name        TEXT NOT NULL,
        process_seq         INTEGER NOT NULL DEFAULT 1,
        is_final_seq        INTEGER NOT NULL DEFAULT 0,
        room_code           TEXT NOT NULL,
        plan_date           TEXT NOT NULL,
        shift_no            INTEGER NOT NULL,
        qty_planned         INTEGER NOT NULL,
        qty_produced        INTEGER DEFAULT 0,
        is_locked           INTEGER NOT NULL DEFAULT 0,
        is_consolidated     INTEGER NOT NULL DEFAULT 0,
        consolidation_group TEXT,
        material_group_id   TEXT,
        block_type          TEXT,
        memo                TEXT,
        created_at          TEXT,
        updated_at          TEXT
    )""")

    plan_cols = [r[1] for r in c.execute("PRAGMA table_info(production_plan)").fetchall()]
    if "is_closing_shift" not in plan_cols:
        c.execute(
            "ALTER TABLE production_plan ADD COLUMN is_closing_shift INTEGER NOT NULL DEFAULT 0"
        )
    if "stack_order" not in plan_cols:
        c.execute(
            "ALTER TABLE production_plan ADD COLUMN stack_order INTEGER NOT NULL DEFAULT 0"
        )
    if "plan_level" not in plan_cols:
        c.execute(
            "ALTER TABLE production_plan ADD COLUMN plan_level TEXT NOT NULL DEFAULT 'SCHEDULE'"
        )
    if "room_type" not in plan_cols:
        c.execute(
            "ALTER TABLE production_plan ADD COLUMN room_type TEXT"
        )

    # ── Material Demand Groups ────────────────────────────────────────────────
    # Records which SO-LineItems contributed to a merged material plan.
    c.execute("""
    CREATE TABLE IF NOT EXISTS material_demand_group (
        group_id        TEXT NOT NULL,
        material_code   TEXT NOT NULL,
        so_number       TEXT NOT NULL,
        sku_code        TEXT NOT NULL,
        line_item       TEXT NOT NULL,
        due_date        TEXT NOT NULL,
        qty_required    INTEGER NOT NULL,  -- material qty needed from this SO
        created_at      TEXT NOT NULL,
        PRIMARY KEY (group_id, so_number, sku_code, line_item)
    )""")

    # ── Plan Change History ───────────────────────────────────────────────────
    c.execute("""
    CREATE TABLE IF NOT EXISTS plan_history (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        plan_id         INTEGER,
        action          TEXT NOT NULL,
        old_value       TEXT,
        new_value       TEXT,
        reason          TEXT,
        changed_at      TEXT NOT NULL
    )""")

    # ── Production Actuals ────────────────────────────────────────────────────
    # entity_type: SKU or MATERIAL
    c.execute("""
    CREATE TABLE IF NOT EXISTS production_actual (
        actual_id       INTEGER PRIMARY KEY AUTOINCREMENT,
        entity_type     TEXT NOT NULL DEFAULT 'SKU',
        entity_code     TEXT NOT NULL DEFAULT '',
        plan_id         INTEGER,
        so_number       TEXT NOT NULL DEFAULT '',
        sku_code        TEXT NOT NULL DEFAULT '',
        line_item       TEXT NOT NULL DEFAULT '',
        lot_number      TEXT,
        room_code       TEXT NOT NULL,
        process_name    TEXT NOT NULL,
        actual_date     TEXT NOT NULL,
        shift_no        INTEGER NOT NULL,
        qty_actual      INTEGER NOT NULL,
        entered_at      TEXT NOT NULL,
        note            TEXT
    )""")

    # ── LOT Sample Quantities ─────────────────────────────────────────────────
    # Planner manually enters QC sample qty per lot after production.
    # net_qty = actual qty - sample qty → used for SO fulfillment check.
    c.execute("""
    CREATE TABLE IF NOT EXISTS lot_sample (
        sample_id       INTEGER PRIMARY KEY AUTOINCREMENT,
        actual_id       INTEGER NOT NULL,  -- links to production_actual
        entity_type     TEXT NOT NULL DEFAULT 'SKU',
        entity_code     TEXT NOT NULL,
        lot_number      TEXT NOT NULL,
        so_number       TEXT NOT NULL DEFAULT '',
        sku_code        TEXT NOT NULL DEFAULT '',
        line_item       TEXT NOT NULL DEFAULT '',
        sample_qty      INTEGER NOT NULL DEFAULT 0,
        reject_qty      INTEGER NOT NULL DEFAULT 0,  -- QC 부적합 수량
        entered_at      TEXT NOT NULL,
        note            TEXT,
        FOREIGN KEY (actual_id) REFERENCES production_actual(actual_id)
    )""")

    # ── Inventory ─────────────────────────────────────────────────────────────
    # Pre-existing stock (가용 재고). No SO linkage at creation time.
    # Sources: excess production, unplanned production, cancelled SO stock.
    # qty_available = original qty uploaded by planner (already net of QC).
    # qty_allocated = sum of so_inventory_allocation.qty_allocated for this lot.
    # qty_remaining (computed) = qty_available - qty_allocated.
    c.execute("""
    CREATE TABLE IF NOT EXISTS inventory (
        inv_id          INTEGER PRIMARY KEY AUTOINCREMENT,
        sku_code        TEXT NOT NULL,
        lot_number      TEXT NOT NULL,
        qty_available   INTEGER NOT NULL,
        production_date TEXT,            -- YYYY-MM-DD
        expiry_date     TEXT,            -- YYYY-MM-DD  (FEFO 기준)
        status          TEXT NOT NULL DEFAULT 'AVAILABLE',
                                         -- AVAILABLE / ALLOCATED / CONSUMED / EXPIRED
        note            TEXT,
        created_at      TEXT NOT NULL,
        updated_at      TEXT,
        UNIQUE (sku_code, lot_number),
        FOREIGN KEY (sku_code) REFERENCES sku_master(sku_code)
    )""")

    # ── SO Inventory Allocation ───────────────────────────────────────────────
    # Links inventory lots to SO-LineItems.
    # One SO-LineItem can pull from multiple lots; one lot can supply multiple SOs.
    # allocated_at: timestamp of planner confirmation.
    c.execute("""
    CREATE TABLE IF NOT EXISTS so_inventory_allocation (
        alloc_id        INTEGER PRIMARY KEY AUTOINCREMENT,
        so_number       TEXT NOT NULL,
        sku_code        TEXT NOT NULL,
        line_item       TEXT NOT NULL,
        inv_id          INTEGER NOT NULL,
        lot_number      TEXT NOT NULL,
        qty_allocated   INTEGER NOT NULL,
        allocated_at    TEXT NOT NULL,
        note            TEXT,
        FOREIGN KEY (so_number, sku_code, line_item)
            REFERENCES sales_order(so_number, sku_code, line_item),
        FOREIGN KEY (inv_id) REFERENCES inventory(inv_id)
    )""")

    # ── App Config ────────────────────────────────────────────────────────────
    c.execute("""
    CREATE TABLE IF NOT EXISTS app_config (
        config_key      TEXT PRIMARY KEY,
        config_value    TEXT NOT NULL,
        description     TEXT
    )""")

    defaults = [
        ("max_pull_days",          "45",       "Max days to pull production earlier than due date"),
        ("plan_horizon_weeks",     "4",         "Weeks shown in Gantt view"),
        ("crp_excel_path",         "",          "Path to CRP Excel file"),
        ("room_assign_mode",       "CAPACITY",  "Room auto-assign mode: CAPACITY or UPH"),
        ("material_due_merge_days","21",        "Merge material demand within this many days of due date"),
        ("max_consolidation_days", "7",         "Max due-date gap (days) for campaign consolidation of same SKU orders"),
    ]
    for key, val, desc in defaults:
        c.execute("""
            INSERT OR IGNORE INTO app_config(config_key, config_value, description)
            VALUES (?,?,?)
        """, (key, val, desc))

    shifts = [
        (1, "Day",   "08:00", "20:00"),
        (2, "Night", "20:00", "08:00"),
    ]
    for s in shifts:
        c.execute("""
            INSERT OR IGNORE INTO shift_config(shift_no, shift_name, start_time, end_time)
            VALUES (?,?,?,?)
        """, s)

    # ── Scenario Planner ─────────────────────────────────────────────────────
    c.execute("""
    CREATE TABLE IF NOT EXISTS scenario (
        scenario_id   INTEGER PRIMARY KEY AUTOINCREMENT,
        name          TEXT NOT NULL DEFAULT 'Scenario',
        date_from     TEXT NOT NULL,
        date_to       TEXT NOT NULL,
        max_hc_add    INTEGER NOT NULL DEFAULT 0,
        hc_step       INTEGER NOT NULL DEFAULT 5,
        bottlenecks   TEXT,
        created_at    TEXT NOT NULL
    )""")

    c.execute("""
    CREATE TABLE IF NOT EXISTS scenario_result (
        result_id     INTEGER PRIMARY KEY AUTOINCREMENT,
        scenario_id   INTEGER NOT NULL,
        hc_added      INTEGER NOT NULL,
        late_before   INTEGER NOT NULL DEFAULT 0,
        late_after    INTEGER NOT NULL DEFAULT 0,
        resolved_sos  TEXT,
        detail        TEXT,
        created_at    TEXT NOT NULL,
        FOREIGN KEY (scenario_id) REFERENCES scenario(scenario_id)
    )""")

    # ── Plan Snapshots ───────────────────────────────────────────────────────
    c.execute("""
    CREATE TABLE IF NOT EXISTS plan_snapshot (
        batch_id        TEXT PRIMARY KEY,
        label           TEXT NOT NULL,
        snapshot_data   TEXT NOT NULL,
        created_at      TEXT NOT NULL
    )""")

    # ── MB51 Processed Documents ─────────────────────────────────────────────
    # Dedup table: each uploaded material document is recorded here so that
    # re-uploading the same MB51 file does not double-count movements.
    c.execute("""
    CREATE TABLE IF NOT EXISTS mb51_processed_docs (
        doc_id          INTEGER PRIMARY KEY AUTOINCREMENT,
        material_document TEXT NOT NULL,
        posting_date    TEXT,
        material        TEXT,
        batch           TEXT,
        qty             REAL,
        movement_type   TEXT,
        processed_at    TEXT NOT NULL,
        UNIQUE (material_document, movement_type)
    )""")

    # ── Performance indices ───────────────────────────────────────────────────
    c.execute("CREATE INDEX IF NOT EXISTS idx_plan_date "
              "ON production_plan(plan_date)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_plan_room "
              "ON production_plan(room_code)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_so_status "
              "ON sales_order(status)")

    conn.commit()
    # No conn.close() — persistent singleton stays open for the process lifetime.


if __name__ == "__main__":
    init_db()
    print("DB initialized at", os.path.abspath(DB_PATH))
