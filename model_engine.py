from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

from PIL import Image
import torch
from torch import nn
from torch.utils.data import DataLoader, WeightedRandomSampler
from torchvision import datasets, transforms

IMAGE_SIZE = 128
MODEL_NAME = "image_classifier.pt"
LABELS_NAME = "labels.json"
SUPPORTED_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif"}


def device() -> torch.device:
    return torch.device("cuda" if torch.cuda.is_available() else "cpu")


def list_images(folder: Path) -> list[Path]:
    if not folder.exists():
        return []
    files: list[Path] = []
    for path in folder.rglob("*"):
        if path.is_file() and path.suffix.lower() in SUPPORTED_EXTS:
            files.append(path)
    return sorted(files)


class SmallCNN(nn.Module):
    def __init__(self, num_classes: int = 3) -> None:
        super().__init__()
        self.features = nn.Sequential(
            nn.Conv2d(3, 32, 3, padding=1),
            nn.BatchNorm2d(32),
            nn.ReLU(inplace=True),
            nn.MaxPool2d(2),
            nn.Conv2d(32, 64, 3, padding=1),
            nn.BatchNorm2d(64),
            nn.ReLU(inplace=True),
            nn.MaxPool2d(2),
            nn.Conv2d(64, 128, 3, padding=1),
            nn.BatchNorm2d(128),
            nn.ReLU(inplace=True),
            nn.MaxPool2d(2),
            nn.Conv2d(128, 256, 3, padding=1),
            nn.BatchNorm2d(256),
            nn.ReLU(inplace=True),
            nn.AdaptiveAvgPool2d(1),
        )
        self.classifier = nn.Sequential(
            nn.Flatten(),
            nn.Dropout(0.35),
            nn.Linear(256, num_classes),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.classifier(self.features(x))


def train_transforms() -> transforms.Compose:
    return transforms.Compose(
        [
            transforms.Resize((IMAGE_SIZE + 24, IMAGE_SIZE + 24)),
            transforms.RandomCrop(IMAGE_SIZE),
            transforms.RandomHorizontalFlip(),
            transforms.ColorJitter(0.2, 0.2, 0.2, 0.05),
            transforms.RandomRotation(12),
            transforms.ToTensor(),
            transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225]),
        ]
    )


def eval_transforms() -> transforms.Compose:
    return transforms.Compose(
        [
            transforms.Resize((IMAGE_SIZE, IMAGE_SIZE)),
            transforms.ToTensor(),
            transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225]),
        ]
    )


class ImageClassifier:
    def __init__(self, work_dir: Path) -> None:
        self.work_dir = work_dir
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.model_path = work_dir / MODEL_NAME
        self.labels_path = work_dir / LABELS_NAME
        self.class_names: list[str] = ["دسته ۱", "دسته ۲", "دسته ۳"]
        self.model: SmallCNN | None = None
        self._load_labels()
        self.load()

    def _load_labels(self) -> None:
        if self.labels_path.exists():
            data = json.loads(self.labels_path.read_text(encoding="utf-8"))
            names = data.get("classes") or []
            if len(names) == 3:
                self.class_names = [str(n) for n in names]

    def save_labels(self) -> None:
        self.labels_path.write_text(
            json.dumps({"classes": self.class_names}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def set_class_names(self, names: list[str]) -> None:
        if len(names) != 3:
            raise ValueError("Exactly 3 class names are required.")
        self.class_names = [name.strip() or f"دسته {i + 1}" for i, name in enumerate(names)]
        self.save_labels()

    def load(self) -> bool:
        if not self.model_path.exists():
            return False
        try:
            payload = torch.load(self.model_path, map_location=device(), weights_only=False)
            names = payload.get("class_names")
            if names and len(names) == 3:
                self.class_names = list(names)
                self.save_labels()
            self.model = SmallCNN(num_classes=3)
            self.model.load_state_dict(payload["state_dict"])
            self.model.to(device())
            self.model.eval()
            return True
        except Exception:
            self.model = None
            return False

    def save(self) -> None:
        if self.model is None:
            return
        torch.save(
            {"state_dict": self.model.state_dict(), "class_names": self.class_names},
            self.model_path,
        )

    def is_ready(self) -> bool:
        return self.model is not None

    def train(
        self,
        dataset_root: Path,
        epochs: int,
        batch_size: int,
        progress: Callable[[int, int, str], None] | None = None,
        should_stop: Callable[[], bool] | None = None,
    ) -> dict[str, float]:
        if not dataset_root.exists():
            raise ValueError("پوشه داده پیدا نشد.")

        transform = train_transforms()
        dataset = datasets.ImageFolder(str(dataset_root), transform=transform)
        if len(dataset.classes) != 3:
            raise ValueError("باید دقیقاً ۳ دسته عکس داشته باشی.")
        if len(dataset) < 6:
            raise ValueError("حداقل ۲ عکس در هر دسته لازم است.")

        counts = [0, 0, 0]
        for _, label in dataset.samples:
            counts[label] += 1
        if min(counts) < 2:
            raise ValueError("حداقل ۲ عکس در هر دسته لازم است.")

        weights = [1.0 / counts[label] for _, label in dataset.samples]
        sampler = WeightedRandomSampler(weights, num_samples=len(dataset), replacement=True)
        loader = DataLoader(
            dataset,
            batch_size=max(1, min(batch_size, len(dataset))),
            sampler=sampler,
            num_workers=0,
        )

        model = SmallCNN(num_classes=3).to(device())
        criterion = nn.CrossEntropyLoss()
        optimizer = torch.optim.AdamW(model.parameters(), lr=1e-3, weight_decay=1e-4)

        last_acc = 0.0
        last_loss = 0.0
        for epoch in range(1, epochs + 1):
            if should_stop and should_stop():
                raise RuntimeError("آموزش متوقف شد.")
            model.train()
            running_loss = 0.0
            correct = 0
            seen = 0
            for images, labels in loader:
                if should_stop and should_stop():
                    raise RuntimeError("آموزش متوقف شد.")
                images = images.to(device())
                labels = labels.to(device())
                optimizer.zero_grad()
                logits = model(images)
                loss = criterion(logits, labels)
                loss.backward()
                optimizer.step()
                running_loss += float(loss.item()) * images.size(0)
                preds = logits.argmax(dim=1)
                correct += int((preds == labels).sum().item())
                seen += images.size(0)
            last_loss = running_loss / max(seen, 1)
            last_acc = correct / max(seen, 1)
            if progress:
                progress(
                    epoch,
                    epochs,
                    f"دور {epoch}/{epochs}  |  دقت آموزش: {last_acc:.0%}  |  خطا: {last_loss:.3f}",
                )

        model.eval()
        self.model = model
        self.save()
        self.save_labels()
        return {"accuracy": last_acc, "loss": last_loss}

    def predict_paths(self, paths: list[Path]) -> list[dict]:
        if self.model is None:
            raise ValueError("اول مدل را آموزش بده.")
        tfm = eval_transforms()
        self.model.eval()
        results: list[dict] = []
        with torch.no_grad():
            for path in paths:
                try:
                    image = Image.open(path).convert("RGB")
                    results.append(self._predict_image(str(path), image, tfm))
                except Exception as exc:
                    results.append(self._error_result(str(path), str(exc)))
        return results

    def predict_images(self, items: list[tuple[str, Image.Image]]) -> list[dict]:
        if self.model is None:
            raise ValueError("اول مدل را آموزش بده.")
        tfm = eval_transforms()
        self.model.eval()
        results: list[dict] = []
        with torch.no_grad():
            for name, image in items:
                try:
                    results.append(self._predict_image(name, image.convert("RGB"), tfm))
                except Exception as exc:
                    results.append(self._error_result(name, str(exc)))
        return results

    def _predict_image(self, name: str, image: Image.Image, tfm) -> dict:
        tensor = tfm(image).unsqueeze(0).to(device())
        logits = self.model(tensor)
        probs = torch.softmax(logits, dim=1)[0]
        conf, idx = torch.max(probs, dim=0)
        return {
            "path": name,
            "label": self.class_names[int(idx)],
            "index": int(idx),
            "confidence": float(conf),
            "probs": [float(p) for p in probs],
            "error": None,
        }

    def _error_result(self, name: str, error: str) -> dict:
        return {
            "path": name,
            "label": "خطا",
            "index": -1,
            "confidence": 0.0,
            "probs": [0.0, 0.0, 0.0],
            "error": error,
        }
