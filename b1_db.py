def init_b1_tables(conn):
    """建立 B1 的四張表。conn 是 sqlite3 連線。"""
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS Item (
        item_id INTEGER PRIMARY KEY AUTOINCREMENT,
        code TEXT NOT NULL UNIQUE,   -- 全系統共用的品項代號，跟 B2 的 item_id 一樣
        name TEXT NOT NULL,
        type TEXT NOT NULL CHECK(type IN ('ingredient','bento')),
        unit TEXT NOT NULL,          -- 固定庫存單位(原料 g、便當 份)
        shelf_life_hours INTEGER,
        price REAL,                  -- 便當售價(C1 POS 使用)
        made_to_order INTEGER NOT NULL DEFAULT 0  -- 1 = 先預訂後製作
    );
    CREATE TABLE IF NOT EXISTS Recipe (
        recipe_id INTEGER PRIMARY KEY AUTOINCREMENT,
        product_code TEXT NOT NULL,
        material_code TEXT NOT NULL,
        qty_per_unit REAL NOT NULL,  -- 每份便當的用量(單位同原料庫存單位)
        loss_rate REAL NOT NULL DEFAULT 0 CHECK(loss_rate>=0 AND loss_rate<1),  -- 0.1 = 前處理損耗 10%
        UNIQUE(product_code, material_code)
    );
    CREATE TABLE IF NOT EXISTS ProductionBatch (
        batch_id INTEGER PRIMARY KEY AUTOINCREMENT,
        product_code TEXT NOT NULL,
        output_batch_code TEXT NOT NULL UNIQUE,
        planned_qty REAL,
        actual_qty REAL NOT NULL,
        total_cost REAL NOT NULL,
        unit_cost REAL NOT NULL,
        produced_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
    );
    CREATE TABLE IF NOT EXISTS BatchMaterialUsage (
        usage_id INTEGER PRIMARY KEY AUTOINCREMENT,
        production_batch_id INTEGER NOT NULL REFERENCES ProductionBatch(batch_id),
        material_code TEXT NOT NULL,
        material_batch_code TEXT NOT NULL,
        qty_used REAL NOT NULL,
        unit_cost REAL NOT NULL
    );
    """)
    # 舊版資料庫沒有售價欄位時補上
    columns = {row[1] for row in conn.execute("PRAGMA table_info(Item)")}
    if "price" not in columns:
        conn.execute("ALTER TABLE Item ADD COLUMN price REAL")
    if "made_to_order" not in columns:
        conn.execute("ALTER TABLE Item ADD COLUMN made_to_order INTEGER NOT NULL DEFAULT 0")
    conn.commit()
