from __future__ import annotations

import random
import re
from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill

ROOT = Path(__file__).resolve().parent
PASSWORDS_FILE = ROOT / "passwords.xlsx"
USER_RE = re.compile(r"^(boy|girl)-([1-9]|1[0-9]|20)$")


def random_password() -> str:
    return str(random.randint(1, 9)) + "".join(str(random.randint(0, 9)) for _ in range(7))


def write_passwords_file(path: Path = PASSWORDS_FILE) -> Path:
    wb = Workbook()
    ws = wb.active
    ws.title = "passwords"
    header = ["username", "password", "group"]
    ws.append(header)
    for cell in ws[1]:
        cell.font = Font(bold=True, color="1A0F0A")
        cell.fill = PatternFill("solid", fgColor="E8C07A")
        cell.alignment = Alignment(horizontal="center")

    row = 2
    for group in ("boy", "girl"):
        for n in range(1, 21):
            username = f"{group}-{n}"
            password = random_password()
            ws.append([username, password, group])
            ws.cell(row=row, column=2).number_format = "@"
            ws.cell(row=row, column=2).value = password
            row += 1

    ws.column_dimensions["A"].width = 16
    ws.column_dimensions["B"].width = 16
    ws.column_dimensions["C"].width = 12
    wb.save(path)
    return path


def load_users(path: Path = PASSWORDS_FILE) -> dict[str, dict[str, str]]:
    if not path.exists():
        write_passwords_file(path)
    wb = load_workbook(path, data_only=True)
    ws = wb.active
    users: dict[str, dict[str, str]] = {}
    for username, password, group in ws.iter_rows(min_row=2, values_only=True):
        if username is None or password is None:
            continue
        name = str(username).strip()
        if not USER_RE.match(name):
            continue
        secret = str(password).strip()
        if secret.endswith(".0"):
            secret = secret[:-2]
        users[name] = {"password": secret, "group": str(group or "").strip()}
    return users


def check_login(username: str, password: str, users: dict[str, dict[str, str]] | None = None) -> dict[str, str] | None:
    table = users if users is not None else load_users()
    name = username.strip()
    record = table.get(name)
    if not record:
        return None
    if record["password"] != str(password).strip():
        return None
    return {"username": name, "group": record["group"]}


if __name__ == "__main__":
    write_passwords_file()
    print(f"wrote {PASSWORDS_FILE}")
