"""مسابقه خرید و فروش عکس — مرجع داده: SQLite (بدون گوگل‌شیت).

همه‌چیز از صفر داخل سایت ساخته و خوانده می‌شود:
- گروه‌ها: همان حساب‌های boy-1..20 و girl-1..20 (از passwords.xlsx) + گروه‌های
  سفارشی که ادمین می‌سازد. هر گروه موجودی نقد، دارایی و دفتر خرید/فروش دارد.
- عکس‌ها: پوشه all-photo/ اسکن می‌شود؛ شماره عکس از اسم فایل خوانده می‌شود
  (اولین عدد داخل اسم، مثلا 12.jpg یا photo-12.jpg -> 12).
- تست: پوشه test/ اسکن می‌شود؛ اگر عکس تست داخل زیرپوشه‌ای به اسم یکی از
  دسته‌ها باشد، دقت هم حساب می‌شود، وگرنه فقط برچسب + اطمینان برمی‌گردد.
- خرید: در یک تراکنش دیتابیس انجام می‌شود؛ عکس فقط به یک گروه فروخته می‌شود،
  پول گروه کم می‌شود، عکس به دیتاست آموزشی آن گروه (به دسته انتخاب‌شده) و به
  دیتاست سراسری کپی می‌شود و ردیف دفتر ثبت می‌شود.
- فروش: برگشت به بانک با نرخ sellback_rate ضربدر قیمت خرید.
"""
from __future__ import annotations

import json
import re
import shutil
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from model_engine import SUPPORTED_EXTS, list_images

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
ALL_PHOTO = ROOT / "all-photo"
TEST_DIR = ROOT / "test"
GROUP_DATASET = ROOT / "dataset_groups"
DB_PATH = DATA_DIR / "competition.db"

DEFAULT_SETTINGS = {
    "starting_capital": "1000",
    "default_photo_price": "100",
    "sellback_rate": "0.8",
}

_lock = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_photo_number(filename: str) -> int | None:
    match = re.search(r"(\d+)", Path(filename).stem)
    return int(match.group(1)) if match else None


def parse_numbers(text: str) -> list[int]:
    """«5, 12-15, 20» -> [5, 12, 13, 14, 15, 20] (مرتب، بدون تکرار)."""
    out: list[int] = []
    for part in str(text or "").replace("،", ",").split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, _, b = part.partition("-")
            try:
                lo, hi = int(a), int(b)
            except ValueError:
                raise ValueError(f"شماره نامعتبر: {part}")
            if lo > hi or lo < 0:
                raise ValueError(f"بازه نامعتبر: {part}")
            out.extend(range(lo, hi + 1))
        else:
            try:
                out.append(int(part))
            except ValueError:
                raise ValueError(f"شماره نامعتبر: {part}")
    return sorted(set(out))


def _connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    with _lock, _connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS groups (
                id TEXT PRIMARY KEY, name TEXT NOT NULL,
                cash REAL NOT NULL, starting_capital REAL NOT NULL,
                created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS photos (
                number INTEGER PRIMARY KEY, filename TEXT NOT NULL,
                rel_path TEXT NOT NULL, price REAL NOT NULL,
                status TEXT NOT NULL DEFAULT 'available',
                owner TEXT, bought_price REAL, bought_at TEXT,
                class_index INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                group_id TEXT NOT NULL, photo_number INTEGER NOT NULL,
                type TEXT NOT NULL, price REAL NOT NULL,
                balance_after REAL NOT NULL, note TEXT DEFAULT '',
                created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS test_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                group_id TEXT, model TEXT NOT NULL, total INTEGER NOT NULL,
                avg_confidence REAL, counts TEXT NOT NULL DEFAULT '{}',
                accuracy REAL, created_at TEXT NOT NULL);
            """
        )
        for key, value in DEFAULT_SETTINGS.items():
            conn.execute(
                "INSERT OR IGNORE INTO settings(key, value) VALUES(?, ?)", (key, value)
            )


def get_settings() -> dict:
    init_db()
    with _connect() as conn:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
    out = dict(DEFAULT_SETTINGS)
    out.update({r["key"]: r["value"] for r in rows})
    return {
        "starting_capital": float(out["starting_capital"]),
        "default_photo_price": float(out["default_photo_price"]),
        "sellback_rate": float(out["sellback_rate"]),
    }


def set_settings(**kwargs) -> dict:
    init_db()
    allowed = {"starting_capital", "default_photo_price", "sellback_rate"}
    with _lock, _connect() as conn:
        for key, value in kwargs.items():
            if key in allowed and value is not None:
                conn.execute(
                    "INSERT INTO settings(key, value) VALUES(?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (key, str(float(value))),
                )
    return get_settings()


def ensure_group(group_id: str, name: str | None = None) -> dict:
    init_db()
    group_id = str(group_id).strip()
    if not group_id:
        raise ValueError("شناسه گروه خالی است.")
    settings = get_settings()
    with _lock, _connect() as conn:
        row = conn.execute("SELECT * FROM groups WHERE id=?", (group_id,)).fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO groups(id, name, cash, starting_capital, created_at) "
                "VALUES(?, ?, ?, ?, ?)",
                (group_id, name or group_id, settings["starting_capital"],
                 settings["starting_capital"], _now()),
            )
            row = conn.execute("SELECT * FROM groups WHERE id=?", (group_id,)).fetchone()
    return dict(row)


def ensure_groups(group_ids) -> None:
    for gid in group_ids:
        ensure_group(gid)


def _group_row(conn, group_id: str):
    row = conn.execute("SELECT * FROM groups WHERE id=?", (group_id,)).fetchone()
    if row is None:
        raise ValueError(f"گروه پیدا نشد: {group_id}")
    return row


def rescan_photos() -> dict:
    """all-photo/ را اسکن کن؛ عکس جدید را با قیمت پیش‌فرض اضافه کن."""
    init_db()
    settings = get_settings()
    ALL_PHOTO.mkdir(parents=True, exist_ok=True)
    TEST_DIR.mkdir(parents=True, exist_ok=True)
    added, updated, skipped = 0, 0, 0
    with _lock, _connect() as conn:
        for path in list_images(ALL_PHOTO):
            number = parse_photo_number(path.name)
            if number is None:
                skipped += 1
                continue
            rel = path.relative_to(ALL_PHOTO).as_posix()
            row = conn.execute("SELECT * FROM photos WHERE number=?", (number,)).fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO photos(number, filename, rel_path, price) "
                    "VALUES(?, ?, ?, ?)",
                    (number, path.name, rel, settings["default_photo_price"]),
                )
                added += 1
            elif row["filename"] != path.name or row["rel_path"] != rel:
                conn.execute(
                    "UPDATE photos SET filename=?, rel_path=? WHERE number=?",
                    (path.name, rel, number),
                )
                updated += 1
    total = len(list_images(ALL_PHOTO))
    return {"added": added, "updated": updated, "skipped_no_number": skipped,
            "files_in_folder": total}


def list_photos(status: str | None = None) -> list[dict]:
    init_db()
    with _connect() as conn:
        if status in ("available", "sold"):
            rows = conn.execute(
                "SELECT * FROM photos WHERE status=? ORDER BY number", (status,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM photos ORDER BY number").fetchall()
    return [dict(r) for r in rows]


def get_photo(number: int) -> dict | None:
    init_db()
    with _connect() as conn:
        row = conn.execute("SELECT * FROM photos WHERE number=?", (int(number),)).fetchone()
    return dict(row) if row else None


def photo_file(number: int) -> Path | None:
    photo = get_photo(number)
    if not photo:
        return None
    path = ALL_PHOTO / photo["rel_path"]
    return path if path.is_file() else None


def set_photo_price(number: int, price: float) -> dict:
    if float(price) < 0:
        raise ValueError("قیمت نمی‌تواند منفی باشد.")
    init_db()
    with _lock, _connect() as conn:
        if conn.execute("SELECT 1 FROM photos WHERE number=?", (int(number),)).fetchone() is None:
            raise ValueError(f"عکس شماره {number} در all-photo پیدا نشد.")
        conn.execute("UPDATE photos SET price=? WHERE number=?", (float(price), int(number)))
    return get_photo(number)


def _copy_to_datasets(group_id: str, number: int, source: Path, class_index: int) -> None:
    for dest_root in (GROUP_DATASET / group_id / f"class_{class_index}",
                      ROOT / "dataset" / f"class_{class_index}"):
        dest_root.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, dest_root / f"{group_id}_{number}{source.suffix.lower()}")


def _remove_from_datasets(group_id: str, number: int, class_index: int) -> None:
    for dest_root in (GROUP_DATASET / group_id / f"class_{class_index}",
                      ROOT / "dataset" / f"class_{class_index}"):
        for path in dest_root.glob(f"{group_id}_{number}.*"):
            path.unlink(missing_ok=True)


def buy_photos(group_id: str, numbers_text: str, class_index: int = 0,
               price_override: float | None = None) -> dict:
    """خرید چند عکس در یک تراکنش؛ اگر یکی نشد، هیچ‌کدام انجام نمی‌شود."""
    if class_index not in (0, 1, 2):
        raise ValueError("دسته باید ۰، ۱ یا ۲ باشد.")
    numbers = parse_numbers(numbers_text)
    if not numbers:
        raise ValueError("هیچ شماره عکسی ندادی.")
    init_db()
    with _lock, _connect() as conn:
        group = _group_row(conn, str(group_id).strip())
        cash = float(group["cash"])
        plan = []
        total = 0.0
        for number in numbers:
            row = conn.execute("SELECT * FROM photos WHERE number=?", (number,)).fetchone()
            if row is None:
                raise ValueError(f"عکس شماره {number} در all-photo نیست.")
            if row["status"] != "available":
                raise ValueError(f"عکس شماره {number} قبلاً به {row['owner']} فروخته شده.")
            price = float(price_override) if price_override is not None else float(row["price"])
            if price < 0:
                raise ValueError("قیمت نمی‌تواند منفی باشد.")
            total += price
            plan.append((dict(row), price))
        if cash < total:
            raise ValueError(
                f"موجودی گروه {cash:.0f} است ولی مجموع خرید {total:.0f} است.")
        bought = []
        for photo, price in plan:
            source = ALL_PHOTO / photo["rel_path"]
            if not source.is_file():
                raise ValueError(f"فایل عکس شماره {photo['number']} پیدا نشد.")
            cash -= price
            conn.execute(
                "UPDATE photos SET status='sold', owner=?, bought_price=?, "
                "bought_at=?, class_index=? WHERE number=?",
                (group["id"], price, _now(), class_index, photo["number"]),
            )
            conn.execute(
                "UPDATE groups SET cash=? WHERE id=?", (cash, group["id"])
            )
            conn.execute(
                "INSERT INTO transactions(group_id, photo_number, type, price, "
                "balance_after, created_at) VALUES(?, ?, 'buy', ?, ?, ?)",
                (group["id"], photo["number"], price, cash, _now()),
            )
            _copy_to_datasets(group["id"], photo["number"], source, class_index)
            bought.append({**photo, "price_paid": price, "class_index": class_index})
        return {"group": group["id"], "bought": bought, "total": total,
                "cash_after": cash}


def sell_photos(group_id: str, numbers_text: str) -> dict:
    """فروش برگشتی به بانک با نرخ sellback_rate."""
    numbers = parse_numbers(numbers_text)
    if not numbers:
        raise ValueError("هیچ شماره عکسی ندادی.")
    settings = get_settings()
    rate = settings["sellback_rate"]
    init_db()
    with _lock, _connect() as conn:
        group = _group_row(conn, str(group_id).strip())
        cash = float(group["cash"])
        sold = []
        total = 0.0
        for number in numbers:
            row = conn.execute("SELECT * FROM photos WHERE number=?", (number,)).fetchone()
            if row is None or row["status"] != "sold" or row["owner"] != group["id"]:
                raise ValueError(f"عکس شماره {number} مال این گروه نیست.")
            price = float(row["bought_price"] or 0) * rate
            total += price
            cash += price
            conn.execute(
                "UPDATE photos SET status='available', owner=NULL, bought_price=NULL, "
                "bought_at=NULL WHERE number=?", (number,)
            )
            conn.execute("UPDATE groups SET cash=? WHERE id=?", (cash, group["id"]))
            conn.execute(
                "INSERT INTO transactions(group_id, photo_number, type, price, "
                "balance_after, created_at) VALUES(?, ?, 'sell', ?, ?, ?)",
                (group["id"], number, price, cash, _now()),
            )
            _remove_from_datasets(group["id"], number, int(row["class_index"]))
            sold.append({"number": number, "price": price})
        return {"group": group["id"], "sold": sold, "total": total,
                "cash_after": cash, "sellback_rate": rate}


def group_portfolio(group_id: str) -> dict:
    init_db()
    with _connect() as conn:
        group = _group_row(conn, str(group_id).strip())
        holdings = conn.execute(
            "SELECT * FROM photos WHERE owner=? ORDER BY number", (group["id"],)
        ).fetchall()
        ledger = conn.execute(
            "SELECT * FROM transactions WHERE group_id=? ORDER BY id DESC LIMIT 200",
            (group["id"],),
        ).fetchall()
        spent = conn.execute(
            "SELECT COALESCE(SUM(price),0) FROM transactions "
            "WHERE group_id=? AND type='buy'", (group["id"],)
        ).fetchone()[0]
        earned = conn.execute(
            "SELECT COALESCE(SUM(price),0) FROM transactions "
            "WHERE group_id=? AND type='sell'", (group["id"],)
        ).fetchone()[0]
        run = conn.execute(
            "SELECT accuracy FROM test_runs WHERE group_id=? AND accuracy IS NOT NULL "
            "ORDER BY id DESC LIMIT 1", (group["id"],)
        ).fetchone()
    holdings = [dict(h) for h in holdings]
    holdings_value = sum(float(h["bought_price"] or 0) for h in holdings)
    return {
        "id": group["id"], "name": group["name"],
        "cash": float(group["cash"]),
        "starting_capital": float(group["starting_capital"]),
        "holdings": holdings, "holdings_count": len(holdings),
        "holdings_value": holdings_value,
        "net_worth": float(group["cash"]) + holdings_value,
        "total_spent": float(spent), "total_earned_sell": float(earned),
        "best_accuracy": float(run["accuracy"]) if run else None,
        "ledger": [dict(r) for r in ledger],
    }


def leaderboard() -> list[dict]:
    init_db()
    with _connect() as conn:
        groups = conn.execute("SELECT id FROM groups ORDER BY id").fetchall()
    boards = [group_portfolio(g["id"]) for g in groups]
    for rank, item in enumerate(
            sorted(boards, key=lambda x: x["net_worth"], reverse=True), start=1):
        item["rank"] = rank
    return sorted(boards, key=lambda x: x["rank"])


def ledger(group_id: str | None = None, limit: int = 200) -> list[dict]:
    init_db()
    with _connect() as conn:
        if group_id:
            rows = conn.execute(
                "SELECT * FROM transactions WHERE group_id=? ORDER BY id DESC LIMIT ?",
                (group_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM transactions ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
    return [dict(r) for r in rows]


def record_test_run(group_id: str | None, model: str, results: list[dict],
                    accuracy: float | None) -> dict:
    init_db()
    counts: dict[str, int] = {}
    confs = [r["confidence"] for r in results if r.get("confidence")]
    for r in results:
        counts[r.get("label", "?")] = counts.get(r.get("label", "?"), 0) + 1
    with _lock, _connect() as conn:
        cur = conn.execute(
            "INSERT INTO test_runs(group_id, model, total, avg_confidence, counts, "
            "accuracy, created_at) VALUES(?, ?, ?, ?, ?, ?, ?)",
            (group_id, model, len(results),
             sum(confs) / len(confs) if confs else None,
             json.dumps(counts, ensure_ascii=False),
             accuracy, _now()),
        )
        run_id = cur.lastrowid
    return {"id": run_id, "group_id": group_id, "model": model,
            "total": len(results), "counts": counts, "accuracy": accuracy}


def list_test_runs(limit: int = 20) -> list[dict]:
    init_db()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM test_runs ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["counts"] = json.loads(d["counts"] or "{}")
        out.append(d)
    return out


def export_xlsx(path: Path) -> Path:
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill

    boards = leaderboard()
    wb = Workbook()
    header_fill = PatternFill("solid", fgColor="E8C07A")
    header_font = Font(bold=True)

    def sheet(title: str, headers: list[str], rows: list[list]):
        ws = wb.active if title == "گروه‌ها" else wb.create_sheet(title)
        ws.title = title
        ws.append(headers)
        for cell in ws[1]:
            cell.font = header_font
            cell.fill = header_fill
        for row in rows:
            ws.append(row)
        for col in ws.columns:
            ws.column_dimensions[col[0].column_letter].width = 18

    sheet("گروه‌ها",
          ["گروه", "موجودی نقد", "تعداد عکس", "ارزش دارایی", "ارزش خالص",
           "کل خرید", "کل فروش", "بهترین دقت تست"],
          [[b["id"], b["cash"], b["holdings_count"], b["holdings_value"],
            b["net_worth"], b["total_spent"], b["total_earned_sell"],
            b["best_accuracy"]] for b in boards])
    sheet("دارایی‌ها", ["شماره عکس", "مالک", "قیمت خرید", "دسته", "فایل"],
          [[h["number"], h["owner"], h["bought_price"], h["class_index"],
            h["filename"]]
           for b in boards for h in b["holdings"]])
    sheet("دفتر خرید و فروش",
          ["گروه", "شماره عکس", "نوع", "قیمت", "موجودی بعد", "تاریخ"],
          [[t["group_id"], t["photo_number"], t["type"], t["price"],
            t["balance_after"], t["created_at"]] for t in ledger(limit=5000)])
    wb.save(path)
    return path
