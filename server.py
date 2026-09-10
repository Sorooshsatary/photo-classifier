from __future__ import annotations

import base64
import shutil
import threading
import uuid
from io import BytesIO
from pathlib import Path

from fastapi import Body, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image

from model_engine import SUPPORTED_EXTS, ImageClassifier, list_images

ROOT = Path(__file__).resolve().parent
DATASET = ROOT / "dataset"
MODELS = ROOT / "models"
STATIC = ROOT / "static"

DATASET.mkdir(parents=True, exist_ok=True)
MODELS.mkdir(parents=True, exist_ok=True)
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

app = FastAPI(title="دسته‌بند تصاویر")


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
async def upload_class(index: int, files: list[UploadFile] = File(...)) -> dict:
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
def start_train(epochs: int = Form(12), batch_size: int = Form(8)) -> dict:
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
