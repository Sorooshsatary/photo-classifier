from __future__ import annotations

import csv
import os
import re
from datetime import datetime, timezone
from io import StringIO
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen

DEFAULT_SHEET_URL = (
    "https://docs.google.com/spreadsheets/d/"
    "1UhT-Sat4iAo1E_hA0-g0HPPIzPGr0BH6QjKqltFV3Ck/edit?gid=339043678#gid=339043678"
)
STANDING_SHEET_URL = (
    "https://docs.google.com/spreadsheets/d/"
    "1UhT-Sat4iAo1E_hA0-g0HPPIzPGr0BH6QjKqltFV3Ck/edit?gid=1453406079#gid=1453406079"
)

TEAM_HEADERS = ("آیدی", "ایدی", "id", "team", "تیم")
QUESTION_HEADERS = ("شماره سوال", "سوال", "question")
PRICE_HEADERS = ("قیمت", "price")
SCORE_HEADERS = ("نمره گرفته شده", "نمره داده شده", "نمره", "score")


def csv_export_url(url: str) -> str:
    raw = url.strip()
    if "export?format=csv" in raw:
        return raw
    match = re.search(r"/spreadsheets/d/([a-zA-Z0-9-_]+)", raw)
    if not match:
        raise ValueError("لینک گوگل‌شیت معتبر نیست.")
    sheet_id = match.group(1)
    gid = "0"
    found = re.search(r"gid=(\d+)", raw)
    if found:
        gid = found.group(1)
    else:
        parsed = urlparse(raw)
        query = parse_qs(parsed.query)
        fragment = parse_qs(parsed.fragment)
        if "gid" in query:
            gid = query["gid"][0]
        elif "gid" in fragment:
            gid = fragment["gid"][0]
    return f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv&gid={gid}"


def _norm(value: str) -> str:
    return re.sub(r"[\s_\-]+", "", str(value).strip().lower())


def _find_col(headers: list[str], aliases: tuple[str, ...], exact: bool = False) -> int | None:
    wanted = {_norm(alias) for alias in aliases}
    for index, header in enumerate(headers):
        text = str(header).strip()
        if not text:
            continue
        if _norm(text) in wanted:
            return index
    if exact:
        return None
    for index, header in enumerate(headers):
        text = str(header).strip()
        if not text:
            continue
        for alias in aliases:
            if alias in text:
                return index
    return None


def _team_keys(username: str) -> set[str]:
    keys = {_norm(username)}
    match = re.match(r"^(boy|girl)-(\d+)$", username.strip(), re.I)
    if match:
        keys.add(_norm(match.group(0)))
        keys.add(match.group(2))
        keys.add(_norm(f"{match.group(1)}{match.group(2)}"))
    return {key for key in keys if key}


def _cell(row: list[str], index: int | None) -> str:
    if index is None or index >= len(row):
        return ""
    return str(row[index]).strip()


def fetch_rows(url: str | None = None) -> list[list[str]]:
    source = (url or os.environ.get("GOOGLE_SHEET_URL") or DEFAULT_SHEET_URL).strip()
    export = csv_export_url(source)
    req = Request(export, headers={"User-Agent": "Mozilla/5.0"})
    with urlopen(req, timeout=12) as resp:
        payload = resp.read().decode("utf-8-sig", errors="replace")
    return list(csv.reader(StringIO(payload)))


def _price_catalog(rows: list[list[str]]) -> dict[str, str]:
    catalog: dict[str, str] = {}
    for row in rows:
        if len(row) <= 22:
            continue
        question = str(row[21]).strip()
        price = str(row[22]).strip()
        if question.isdigit() and price:
            catalog[question] = price
    return catalog


def all_purchases() -> dict:
    try:
        rows = fetch_rows()
    except HTTPError as exc:
        msg = "گوگل شیت در دسترس نیست. Share را روی Anyone with the link بگذار."
        if exc.code == 404:
            msg = "شیت پیدا نشد."
        return {"items": [], "error": msg, "fetched_at": None}
    except (URLError, TimeoutError, ValueError) as exc:
        return {"items": [], "error": str(exc), "fetched_at": None}

    headers = rows[0] if rows else []
    team_i = _find_col(headers, TEAM_HEADERS) or 0
    question_i = _find_col(headers, QUESTION_HEADERS) or 1
    price_i = _find_col(headers, PRICE_HEADERS) or 3
    score_i = _find_col(headers, SCORE_HEADERS) or 4
    catalog = _price_catalog(rows)
    items = []
    for row in rows[1:]:
        team = _cell(row, team_i)
        if not team or _norm(team) in {_norm("آیدی"), "id", "team"}:
            continue
        question = _cell(row, question_i)
        price = _cell(row, price_i) or catalog.get(question, "")
        score = _cell(row, score_i)
        items.append(
            {
                "team": team,
                "question": question,
                "price": price,
                "score": score,
            }
        )

    return {
        "items": items,
        "error": None,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }


def _sheet_error(exc: Exception) -> dict:
    if isinstance(exc, HTTPError):
        msg = "گوگل شیت در دسترس نیست. Share را روی Anyone with the link بگذار."
        if exc.code == 404:
            msg = "شیت پیدا نشد."
        return {"items": [], "error": msg, "fetched_at": None}
    return {"items": [], "error": str(exc), "fetched_at": None}


def all_standings() -> dict:
    try:
        rows = fetch_rows(STANDING_SHEET_URL)
    except (HTTPError, URLError, TimeoutError, ValueError) as exc:
        return _sheet_error(exc)

    header_i = 0
    headers = rows[0] if rows else []
    for index, row in enumerate(rows[:6]):
        if _find_col(row, ("آیدی",), exact=True) is not None and _find_col(row, ("امتیاز کل",), exact=True) is not None:
            header_i = index
            headers = row
            break

    team_i = _find_col(headers, ("آیدی",), exact=True)
    total_i = _find_col(headers, ("امتیاز کل",), exact=True)
    stock_i = _find_col(headers, ("قیمت سهام",))
    day_team_i = _find_col(headers, ("تیم",), exact=True)
    day_score_i = _find_col(headers, ("امتیاز",), exact=True)

    day_scores: dict[str, str] = {}
    if day_team_i is not None and day_score_i is not None:
        for row in rows[header_i + 1 :]:
            team = _cell(row, day_team_i)
            score = _cell(row, day_score_i)
            if team:
                day_scores[team] = score

    by_team: dict[str, dict[str, str]] = {}
    for row in rows[header_i + 1 :]:
        team = _cell(row, team_i)
        if not team:
            continue
        by_team[team] = {
            "team": team,
            "total": _cell(row, total_i),
            "stock": _cell(row, stock_i),
            "day": day_scores.get(team, ""),
        }
    for team, score in day_scores.items():
        if team not in by_team:
            by_team[team] = {"team": team, "total": "", "stock": "", "day": score}

    def score_value(item: dict[str, str], key: str) -> float:
        try:
            return float(str(item.get(key) or "0").replace(",", ""))
        except ValueError:
            return 0.0

    items = sorted(
        by_team.values(),
        key=lambda item: (score_value(item, "total"), score_value(item, "day")),
        reverse=True,
    )
    for rank, item in enumerate(items, start=1):
        item["rank"] = str(rank)

    return {
        "items": items,
        "error": None,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }
