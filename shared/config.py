"""共用設定：全系統只有一個庫存資料庫，所有模組都讀寫同一個檔案。"""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 共用資料庫：B1 品項與食譜、B2 庫存、C1 銷售與預訂都放在這裡。
# 可用環境變數 BENTO_DB 改路徑（舊名 B2_DB_PATH 仍可使用）。
INVENTORY_DB = os.environ.get("BENTO_DB") or os.environ.get("B2_DB_PATH") or str(ROOT / "bento.db")

# A1 預測、C2 報表、A1 匯出都用同一組時段與分界。
SLOTS = ["午餐", "晚餐", "宵夜"]


def slot_of(local_time):
    """'HH:MM' → 時段。17:00 前算午餐，21:00 前算晚餐，之後算宵夜。"""
    hhmm = str(local_time)[:5]
    if hhmm < "17:00":
        return "午餐"
    if hhmm < "21:00":
        return "晚餐"
    return "宵夜"
