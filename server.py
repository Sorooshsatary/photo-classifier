from __future__ import annotations

import base64
import os
import random
import shutil
import threading
import uuid
from io import BytesIO
from pathlib import Path

from fastapi import Body, Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image
from starlette.middleware.sessions import SessionMiddleware

import competition
from accounts import USER_RE, load_users
from model_engine import SUPPORTED_EXTS, ImageClassifier, list_images
from competition import ALL_PHOTO, GROUP_DATASET, TEST_DIR, parse_numbers, parse_photo_number

ROOT = Path(__file__).resolve().parent
DATASET = ROOT / "dataset"
MODELS = ROOT / "models"
STATIC = ROOT / "static"
USERDATA = ROOT / "userdata"
USERS = load_users()

DATASET.mkdir(parents=True, exist_ok=True)
MODELS.mkdir(parents=True, exist_ok=True)
USERDATA.mkdir(parents=True, exist_ok=True)
ALL_PHOTO.mkdir(parents=True, exist_ok=True)
TEST_DIR.mkdir(parents=True, exist_ok=True)
GROUP_DATASET.mkdir(parents=True, exist_ok=True)
for i in range(3):
    (DATASET / f"class_{i}").mkdir(parents=True, exist_ok=True)

SEED_RANGES = {
    "1": (1, 20),
    "2": (21, 80),
    "3": (81, 100),
}

classifier = ImageClassifier(MODELS)
train_lock = threading.Lock()
train_state = {
    "running": False,
    "epoch": 0,
    "total": 0,
    "message": "هنوز آموزشی شروع نشده.",
    "error": None,
    "done": False,
    "metrics": None,
}
group_train_states: dict[str, dict] = {}

app = FastAPI(title="دسته‌بند گروهی عکس — مسابقه خرید و فروش عکس")
app.add_middleware(
    SessionMiddleware,
    secret_key=os.environ.get("SESSION_SECRET", "batch-photo-classifier-local"),
    same_site="lax",
    https_only=False,
)

# مرجع داده مسابقه: SQLite — بدون گوگل‌شیت. گروه‌ها از حساب‌ها ساخته می‌شوند.
competition.init_db()
competition.ensure_groups(list(USERS.keys()))
try:
    competition.rescan_photos()
except Exception:
    pass


def current_user(request: Request) -> str | None:
    username = request.session.get("user")
    if username and username in USERS and USER_RE.match(username):
        return username
    return None


def require_user(request: Request) -> str:
    username = current_user(request)
    if not username:
        raise HTTPException(401, "برای ذخیره عکس اول وارد شو.")
    return username


def user_photos_dir(username: str) -> Path:
    path = USERDATA / username / "photos"
    path.mkdir(parents=True, exist_ok=True)
    return path


def class_dir(index: int) -> Path:
    if index not in (0, 1, 2):
        raise HTTPException(400, "فقط سه دسته وجود دارد.")
    path = DATASET / f"class_{index}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def save_upload(dest: Path, raw: bytes, filename: str) -> Path:
    suffix = Path(filename).suffix.lower()
    if suffix not in SUPPORTED_EXTS:
        raise HTTPException(400, f"فرمت پشتیبانی نمی‌شود: {filename}")
    dest.mkdir(parents=True, exist_ok=True)
    name = f"{Path(filename).stem}_{uuid.uuid4().hex[:8]}{suffix}"
    path = dest / name
    path.write_bytes(raw)
    return path


def snapshot() -> dict:
    counts = [len(list_images(class_dir(i))) for i in range(3)]
    previews = []
    for i in range(3):
        files = list_images(class_dir(i))[:10]
        previews.append([f"/api/image/{i}/{p.name}" for p in files])
    return {
        "classes": classifier.class_names,
        "counts": counts,
        "previews": previews,
        "ready": classifier.is_ready(),
        "train": train_state,
    }


def album_payload(username: str) -> dict:
    files = list_images(user_photos_dir(username))
    return {
        "username": username,
        "count": len(files),
        "photos": [{"name": p.name, "url": f"/api/my/photos/{p.name}"} for p in files],
    }


def _thumb_data_url(image: Image.Image) -> str:
    preview = image.copy()
    preview.thumbnail((160, 160))
    buf = BytesIO()
    preview.save(buf, format="JPEG", quality=82)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


@app.post("/api/login")
def login(request: Request, username: str = Form(...), password: str = Form(...)) -> dict:
    record = USERS.get(username.strip())
    if not record or record["password"] != str(password).strip():
        raise HTTPException(401, "نام کاربری یا رمز اشتباه است.")
    request.session["user"] = username.strip()
    return {"ok": True, "username": username.strip()}


@app.post("/api/logout")
def logout(request: Request) -> dict:
    request.session.clear()
    return {"ok": True}


@app.get("/api/me")
def me(request: Request) -> dict:
    username = current_user(request)
    if not username:
        return {"username": None}
    return {"username": username}


@app.get("/api/my/photos")
def list_my_photos(username: str = Depends(require_user)) -> dict:
    return album_payload(username)


@app.post("/api/my/photos")
async def upload_my_photos(
    files: list[UploadFile] = File(...),
    username: str = Depends(require_user),
) -> dict:
    folder = user_photos_dir(username)
    saved = 0
    for item in files:
        data = await item.read()
        if not data:
            continue
        name = item.filename or "image.jpg"
        suffix = Path(name).suffix.lower()
        if suffix not in SUPPORTED_EXTS:
            continue
        save_upload(folder, data, name)
        saved += 1
    if saved == 0:
        raise HTTPException(400, "هیچ عکس معتبری آپلود نشد.")
    payload = album_payload(username)
    payload["saved"] = saved
    return payload


@app.post("/api/my/photos/clear")
def clear_my_photos(username: str = Depends(require_user)) -> dict:
    folder = user_photos_dir(username)
    shutil.rmtree(folder, ignore_errors=True)
    folder.mkdir(parents=True, exist_ok=True)
    return album_payload(username)


@app.post("/api/my/photos/delete/{name}")
def delete_my_photo(name: str, username: str = Depends(require_user)) -> dict:
    path = user_photos_dir(username) / Path(name).name
    if path.exists():
        path.unlink()
    return album_payload(username)


@app.get("/api/my/photos/{name}")
def my_photo_file(name: str, username: str = Depends(require_user)):
    path = user_photos_dir(username) / Path(name).name
    if not path.exists() or not path.is_file():
        raise HTTPException(404, "عکس پیدا نشد.")
    return FileResponse(path)


@app.post("/api/seed")
def roll_seed(option: str = Form(...)) -> dict:
    key = option.strip()
    bounds = SEED_RANGES.get(key)
    if not bounds:
        raise HTTPException(400, "فقط گزینه ۱، ۲ یا ۳ مجاز است.")
    low, high = bounds
    return {
        "option": key,
        "low": low,
        "high": high,
        "number": random.randint(low, high),
    }


# ---------------- مسابقه: خرید و فروش عکس (SQLite) ----------------

@app.get("/api/competition/state")
def competition_state() -> dict:
    photos = competition.list_photos()
    return {
        "settings": competition.get_settings(),
        "photos_total": len(photos),
        "photos_available": sum(1 for p in photos if p["status"] == "available"),
        "photos_sold": sum(1 for p in photos if p["status"] == "sold"),
        "test_total": len(list_images(TEST_DIR)),
        "groups_count": len(competition.leaderboard()),
        "classes": classifier.class_names,
    }


@app.post("/api/competition/rescan")
def competition_rescan() -> dict:
    return competition.rescan_photos() | competition_state()


@app.get("/api/competition/photos")
def competition_photos(status: str | None = None) -> dict:
    return {"photos": competition.list_photos(status)}


@app.get("/api/competition/photos/{number}/image")
def competition_photo_image(number: int):
    path = competition.photo_file(number)
    if not path:
        raise HTTPException(404, "عکس پیدا نشد.")
    return FileResponse(path)


@app.get("/api/competition/groups")
def competition_groups() -> dict:
    return {"groups": competition.leaderboard()}


@app.get("/api/competition/groups/{group_id}")
def competition_group(group_id: str) -> dict:
    try:
        return competition.group_portfolio(group_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc))


@app.post("/api/competition/groups")
def competition_create_group(group_id: str = Form(...), name: str = Form(""),
                             capital: float | None = Form(None)) -> dict:
    try:
        group = competition.ensure_group(group_id, name or None)
        if capital is not None:
            # سرمایه شروع گروه تازه‌ساخته را تنظیم کن
            import sqlite3 as _s
            with _s.connect(competition.DB_PATH) as conn:
                conn.execute("UPDATE groups SET cash=?, starting_capital=? WHERE id=?",
                             (float(capital), float(capital), group["id"]))
            group = competition.group_portfolio(group["id"])
        return group
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.post("/api/competition/settings")
def competition_settings(starting_capital: float | None = Form(None),
                         default_photo_price: float | None = Form(None),
                         sellback_rate: float | None = Form(None)) -> dict:
    return competition.set_settings(starting_capital=starting_capital,
                                    default_photo_price=default_photo_price,
                                    sellback_rate=sellback_rate)


@app.post("/api/competition/photos/{number}/price")
def competition_set_price(number: int, price: float = Form(...)) -> dict:
    try:
        return competition.set_photo_price(number, price)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.post("/api/competition/buy")
def competition_buy(group_id: str = Form(...), numbers: str = Form(...),
                    class_index: int = Form(0),
                    price: float | None = Form(None)) -> dict:
    try:
        return competition.buy_photos(group_id, numbers, class_index, price)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.post("/api/competition/sell")
def competition_sell(group_id: str = Form(...), numbers: str = Form(...)) -> dict:
    try:
        return competition.sell_photos(group_id, numbers)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.get("/api/competition/ledger")
def competition_ledger(group_id: str | None = None, limit: int = 200) -> dict:
    return {"items": competition.ledger(group_id, limit)}


@app.get("/api/competition/leaderboard")
def competition_leaderboard() -> dict:
    return {"items": competition.leaderboard()}


@app.get("/api/competition/export.xlsx")
def competition_export():
    path = competition.DATA_DIR / "export.xlsx"
    competition.export_xlsx(path)
    return FileResponse(path, filename="competition.xlsx",
                        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@app.post("/api/competition/train/{group_id}")
def competition_train_group(group_id: str, epochs: int = Form(12),
                            batch_size: int = Form(8)) -> dict:
    epochs = max(3, min(40, int(epochs)))
    batch_size = max(2, min(16, int(batch_size)))
    dataset_root = GROUP_DATASET / group_id
    if not dataset_root.exists():
        raise HTTPException(400, "این گروه هنوز عکسی نخریده است.")
    state = group_train_states.setdefault(group_id, {
        "running": False, "epoch": 0, "total": 0,
        "message": "هنوز آموزشی شروع نشده.", "error": None, "done": False,
        "metrics": None,
    })
    with train_lock:
        if state["running"]:
            raise HTTPException(409, "آموزش این گروه در حال اجراست.")
        state.update({"running": True, "epoch": 0, "total": epochs,
                      "message": "شروع آموزش مدل گروه...", "error": None,
                      "done": False, "metrics": None})
    group_classifier = ImageClassifier(MODELS / f"group_{group_id}")
    group_classifier.class_names = classifier.class_names

    def work() -> None:
        try:
            metrics = group_classifier.train(
                dataset_root, epochs=epochs, batch_size=batch_size,
                progress=lambda e, total, msg: state.update(
                    {"epoch": e, "total": total, "message": msg}),
            )
            state.update({"metrics": metrics, "done": True, "message": "آموزش مدل گروه تمام شد."})
        except Exception as exc:
            state["error"] = str(exc)
            state["message"] = str(exc)
        finally:
            state["running"] = False

    threading.Thread(target=work, daemon=True).start()
    return {"group": group_id, "train": state}


@app.post("/api/competition/test-folder")
def competition_test_folder(group_id: str | None = Form(None)) -> dict:
    files = list_images(TEST_DIR)
    if not files:
        raise HTTPException(400, "پوشه test خالی است؛ عکس تست داخل آن بگذار.")
    if group_id:
        model = ImageClassifier(MODELS / f"group_{group_id}")
        model_label = f"مدل گروه {group_id}"
        if not model.is_ready():
            raise HTTPException(400, f"مدل گروه {group_id} هنوز آموزش ندیده است.")
    else:
        model = classifier
        model_label = "مدل سراسری"
        if not model.is_ready():
            raise HTTPException(400, "اول مدل سراسری را آموزش بده.")
    results = model.predict_paths(files)
    correct = 0
    scored = 0
    for path, row in zip(files, results):
        # برچسب واقعی فقط وقتی هست که عکس تست داخل زیرپوشه دسته باشد
        parent = path.parent.name
        truth = None
        if parent in model.class_names:
            truth = model.class_names.index(parent)
        elif parent.startswith("class_"):
            try:
                truth = int(parent.split("_", 1)[1])
            except ValueError:
                truth = None
        row["number"] = parse_photo_number(path.name)
        row["file"] = path.relative_to(TEST_DIR).as_posix()
        row["truth"] = truth
        if truth is not None and row.get("index", -1) >= 0:
            scored += 1
            if row["index"] == truth:
                correct += 1
        try:
            row["thumb"] = _thumb_data_url(Image.open(path).convert("RGB"))
        except Exception:
            row["thumb"] = ""
    accuracy = (correct / scored) if scored else None
    run = competition.record_test_run(group_id, model_label, results, accuracy)
    counts: dict[str, int] = {name: 0 for name in model.class_names}
    for row in results:
        if row.get("index", -1) >= 0:
            counts[row["label"]] = counts.get(row["label"], 0) + 1
    return {"results": results, "counts": counts, "classes": model.class_names,
            "model": model_label, "accuracy": accuracy,
            "scored": scored, "correct": correct, "run": run}


@app.get("/api/competition/test-runs")
def competition_test_runs() -> dict:
    return {"items": competition.list_test_runs()}


@app.get("/api/state")
def get_state() -> dict:
    return snapshot()


@app.post("/api/classes")
def set_classes(names: list[str] = Body(...)) -> dict:
    if len(names) != 3:
        raise HTTPException(400, "باید دقیقاً سه اسم بفرستی.")
    classifier.set_class_names(names)
    return snapshot()


@app.post("/api/upload/{index}")
async def upload_class(
    index: int,
    files: list[UploadFile] = File(...),
) -> dict:
    folder = class_dir(index)
    saved = 0
    for item in files:
        data = await item.read()
        if not data:
            continue
        name = item.filename or "image.jpg"
        suffix = Path(name).suffix.lower()
        if suffix not in SUPPORTED_EXTS:
            continue
        save_upload(folder, data, name)
        saved += 1
    if saved == 0:
        raise HTTPException(400, "هیچ عکس معتبری آپلود نشد.")
    return snapshot() | {"saved": saved}


@app.post("/api/clear/{index}")
def clear_class(index: int) -> dict:
    folder = class_dir(index)
    shutil.rmtree(folder, ignore_errors=True)
    folder.mkdir(parents=True, exist_ok=True)
    return snapshot()


@app.get("/api/image/{index}/{name}")
def class_image(index: int, name: str):
    path = class_dir(index) / name
    if not path.exists() or not path.is_file():
        raise HTTPException(404, "عکس پیدا نشد.")
    return FileResponse(path)


@app.post("/api/train")
def start_train(
    epochs: int = Form(12),
    batch_size: int = Form(8),
) -> dict:
    epochs = max(3, min(40, int(epochs)))
    batch_size = max(2, min(16, int(batch_size)))
    with train_lock:
        if train_state["running"]:
            raise HTTPException(409, "آموزش در حال اجراست.")
        train_state.update(
            {
                "running": True,
                "epoch": 0,
                "total": epochs,
                "message": "شروع آموزش...",
                "error": None,
                "done": False,
                "metrics": None,
            }
        )

    def work() -> None:
        try:
            metrics = classifier.train(
                DATASET,
                epochs=epochs,
                batch_size=batch_size,
                progress=lambda e, total, msg: train_state.update(
                    {"epoch": e, "total": total, "message": msg}
                ),
            )
            train_state.update({"metrics": metrics, "done": True, "message": "آموزش تمام شد."})
        except Exception as exc:
            train_state["error"] = str(exc)
            train_state["message"] = str(exc)
        finally:
            train_state["running"] = False

    threading.Thread(target=work, daemon=True).start()
    return snapshot()


@app.post("/api/predict")
async def predict(files: list[UploadFile] = File(...)) -> dict:
    if not classifier.is_ready():
        raise HTTPException(400, "اول مدل را آموزش بده.")
    items: list[tuple[str, Image.Image]] = []
    thumbs: list[str] = []
    for item in files:
        data = await item.read()
        name = item.filename or "image.jpg"
        try:
            image = Image.open(BytesIO(data))
            image = image.convert("RGB")
            items.append((name, image))
            preview = image.copy()
            preview.thumbnail((160, 160))
            buf = BytesIO()
            preview.save(buf, format="JPEG", quality=82)
            thumbs.append("data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode())
        except Exception:
            continue
    if not items:
        raise HTTPException(400, "عکس معتبری برای تست پیدا نشد.")
    results = classifier.predict_images(items)
    for row, thumb in zip(results, thumbs):
        row["thumb"] = thumb
    counts: dict[str, int] = {name: 0 for name in classifier.class_names}
    for row in results:
        if row.get("index", -1) >= 0:
            counts[row["label"]] = counts.get(row["label"], 0) + 1
    return {"results": results, "counts": counts, "classes": classifier.class_names}


app.mount("/", StaticFiles(directory=str(STATIC), html=True), name="static")
