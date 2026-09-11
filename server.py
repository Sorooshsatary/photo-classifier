from __future__ import annotations

import base64
import os
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

from accounts import USER_RE, load_users
from model_engine import SUPPORTED_EXTS, ImageClassifier, list_images
from sheet_feed import all_purchases, all_standings

ROOT = Path(__file__).resolve().parent
DATASET = ROOT / "dataset"
MODELS = ROOT / "models"
STATIC = ROOT / "static"
USERDATA = ROOT / "userdata"
USERS = load_users()

DATASET.mkdir(parents=True, exist_ok=True)
MODELS.mkdir(parents=True, exist_ok=True)
USERDATA.mkdir(parents=True, exist_ok=True)
for i in range(3):
    (DATASET / f"class_{i}").mkdir(parents=True, exist_ok=True)

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

app = FastAPI(title="دسته‌بند گروهی عکس")
app.add_middleware(
    SessionMiddleware,
    secret_key=os.environ.get("SESSION_SECRET", "batch-photo-classifier-local"),
    same_site="lax",
    https_only=False,
)


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


@app.get("/api/questions")
def list_questions() -> dict:
    return all_purchases()


@app.get("/api/standings")
def list_standings() -> dict:
    return all_standings()


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
