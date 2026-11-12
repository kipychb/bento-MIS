import sqlite3

conn = sqlite3.connect("bento.db")
cur = conn.cursor()

cur.execute("""
CREATE TABLE IF NOT EXISTS Item (
    item_id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    type TEXT NOT NULL,
    unit TEXT NOT NULL,
    shelf_life_hours INTEGER
)
""")

cur.execute("""
CREATE TABLE IF NOT EXISTS Recipe (
    recipe_id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER NOT NULL,
    material_id INTEGER NOT NULL,
    qty_per_unit REAL NOT NULL,
    loss_rate REAL DEFAULT 0
)
""")

conn.commit()
conn.close()
print("資料表建立完成!")
