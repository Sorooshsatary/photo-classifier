from __future__ import annotations

import csv
import shutil
import threading
import tkinter as tk
import uuid
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk
from PIL import Image, ImageOps, ImageTk

from model_engine import SUPPORTED_EXTS, ImageClassifier, list_images

ROOT = Path(__file__).resolve().parent
DATASET = ROOT / "dataset"
MODELS = ROOT / "models"
PREVIEWS_PER_CLASS = 8
THUMB = 72


def unique_copy(src: Path, dest_dir: Path) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    name = f"{src.stem}_{uuid.uuid4().hex[:8]}{src.suffix.lower()}"
    dest = dest_dir / name
    shutil.copy2(src, dest)
    return dest


def collect_from_selection(paths: list[str]) -> list[Path]:
    found: list[Path] = []
    for raw in paths:
        path = Path(raw)
        if path.is_dir():
            found.extend(list_images(path))
        elif path.is_file() and path.suffix.lower() in SUPPORTED_EXTS:
            found.append(path)
    # keep order, drop duplicates
    seen: set[Path] = set()
    unique: list[Path] = []
    for item in found:
        resolved = item.resolve()
        if resolved not in seen:
            seen.add(resolved)
            unique.append(item)
    return unique


class ClassCard(ctk.CTkFrame):
    def __init__(self, master, index: int, default_name: str, on_change) -> None:
        super().__init__(master, corner_radius=16, fg_color=("#ece7df", "#241f1c"))
        self.index = index
        self.on_change = on_change
        self.folder = DATASET / f"class_{index}"
        self.folder.mkdir(parents=True, exist_ok=True)
        self._thumbs: list[ImageTk.PhotoImage] = []

        pad = {"padx": 14, "pady": 8}
        ctk.CTkLabel(self, text=f"دسته {index + 1}", font=ctk.CTkFont(size=13)).pack(
            anchor="e", **pad
        )
        self.name_var = tk.StringVar(value=default_name)
        self.name_entry = ctk.CTkEntry(
            self,
            textvariable=self.name_var,
            justify="right",
            height=36,
            font=ctk.CTkFont(size=16),
        )
        self.name_entry.pack(fill="x", padx=14)
        self.name_entry.bind("<KeyRelease>", lambda _e: on_change())

        self.count_label = ctk.CTkLabel(self, text="۰ عکس", font=ctk.CTkFont(size=14))
        self.count_label.pack(anchor="e", padx=14, pady=(10, 4))

        btns = ctk.CTkFrame(self, fg_color="transparent")
        btns.pack(fill="x", padx=10, pady=6)
        ctk.CTkButton(btns, text="افزودن عکس‌ها", command=self.import_files, height=34).pack(
            fill="x", pady=3
        )
        ctk.CTkButton(
            btns,
            text="افزودن کل پوشه",
            command=self.import_folder,
            height=34,
            fg_color=("#7a5c3a", "#8b5e34"),
            hover_color=("#634a2e", "#6f4a28"),
        ).pack(fill="x", pady=3)
        ctk.CTkButton(
            btns,
            text="خالی کردن این دسته",
            command=self.clear,
            height=32,
            fg_color="transparent",
            border_width=1,
            text_color=("#6b3b32", "#e8b4aa"),
        ).pack(fill="x", pady=3)

        self.preview = ctk.CTkFrame(self, fg_color="transparent")
        self.preview.pack(fill="both", expand=True, padx=10, pady=(4, 12))
        self.refresh()

    def import_files(self) -> None:
        files = filedialog.askopenfilenames(
            title="چند عکس را با هم انتخاب کن",
            filetypes=[
                ("Images", "*.jpg *.jpeg *.png *.webp *.bmp *.gif"),
                ("All files", "*.*"),
            ],
        )
        self._ingest(list(files))

    def import_folder(self) -> None:
        folder = filedialog.askdirectory(title="یک پوشه پر از عکس انتخاب کن")
        if folder:
            self._ingest([folder])

    def _ingest(self, selected: list[str]) -> None:
        images = collect_from_selection(selected)
        if not images:
            messagebox.showwarning("بدون عکس", "در انتخاب‌ها هیچ عکس معتبری پیدا نشد.")
            return
        for src in images:
            unique_copy(src, self.folder)
        self.refresh()
        self.on_change()

    def clear(self) -> None:
        if not list_images(self.folder):
            return
        if not messagebox.askyesno("حذف", "همه عکس‌های این دسته پاک شوند؟"):
            return
        shutil.rmtree(self.folder, ignore_errors=True)
        self.folder.mkdir(parents=True, exist_ok=True)
        self.refresh()
        self.on_change()

    def refresh(self) -> None:
        for child in self.preview.winfo_children():
            child.destroy()
        self._thumbs.clear()
        images = list_images(self.folder)
        self.count_label.configure(text=f"{len(images)} عکس")
        row = ctk.CTkFrame(self.preview, fg_color="transparent")
        row.pack(fill="x")
        for path in images[:PREVIEWS_PER_CLASS]:
            try:
                img = Image.open(path)
                img = ImageOps.exif_transpose(img)
                img.thumbnail((THUMB, THUMB))
                canvas = Image.new("RGB", (THUMB, THUMB), (40, 36, 32))
                x = (THUMB - img.width) // 2
                y = (THUMB - img.height) // 2
                canvas.paste(img.convert("RGB"), (x, y))
                photo = ImageTk.PhotoImage(canvas)
                self._thumbs.append(photo)
                lbl = tk.Label(row, image=photo, bd=0)
                lbl.pack(side="right", padx=3, pady=2)
            except Exception:
                continue
        extra = len(images) - PREVIEWS_PER_CLASS
        if extra > 0:
            ctk.CTkLabel(row, text=f"+{extra}").pack(side="right", padx=6)


class ResultRow(ctk.CTkFrame):
    def __init__(self, master, item: dict, class_names: list[str]) -> None:
        super().__init__(master, corner_radius=12, fg_color=("#efeae3", "#2a241f"))
        self._photo = None
        path = Path(item["path"])
        try:
            img = Image.open(path)
            img = ImageOps.exif_transpose(img)
            img.thumbnail((88, 88))
            self._photo = ImageTk.PhotoImage(img.convert("RGB"))
            tk.Label(self, image=self._photo, bd=0).pack(side="right", padx=10, pady=8)
        except Exception:
            ctk.CTkLabel(self, text="بدون پیش‌نمایش", width=88).pack(side="right", padx=10)

        info = ctk.CTkFrame(self, fg_color="transparent")
        info.pack(side="right", fill="both", expand=True, padx=8, pady=8)
        title = item["label"] if not item.get("error") else f"خطا: {item['error']}"
        conf = item.get("confidence") or 0.0
        ctk.CTkLabel(
            info,
            text=f"{title}   ·   {conf:.0%}",
            font=ctk.CTkFont(size=16, weight="bold"),
            anchor="e",
        ).pack(anchor="e")
        ctk.CTkLabel(info, text=path.name, anchor="e").pack(anchor="e")
        probs = item.get("probs") or []
        if len(probs) == 3:
            bars = ctk.CTkFrame(info, fg_color="transparent")
            bars.pack(fill="x", pady=(6, 0))
            for name, p in zip(class_names, probs):
                line = ctk.CTkFrame(bars, fg_color="transparent")
                line.pack(fill="x", pady=1)
                bar = ctk.CTkProgressBar(line, height=8)
                bar.set(max(0.0, min(1.0, float(p))))
                bar.pack(side="left", fill="x", expand=True, padx=(0, 8))
                ctk.CTkLabel(line, text=f"{name} {p:.0%}", width=120, anchor="e").pack(
                    side="right"
                )


class App(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        DATASET.mkdir(parents=True, exist_ok=True)
        MODELS.mkdir(parents=True, exist_ok=True)
        self.classifier = ImageClassifier(MODELS)
        self.test_paths: list[Path] = []
        self.last_results: list[dict] = []
        self._stop = False
        self._busy = False

        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("dark-blue")
        self.title("دسته‌بند گروهی عکس — ۳ کلاس")
        self.geometry("1180x760")
        self.minsize(980, 680)

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=18, pady=(16, 8))
        ctk.CTkLabel(
            header,
            text="آموزش روی سه دسته، بعد برچسب‌گذاری گروهی داده‌های تست",
            font=ctk.CTkFont(size=22, weight="bold"),
        ).pack(anchor="e")
        self.status = ctk.CTkLabel(header, text=self._ready_text(), anchor="e")
        self.status.pack(anchor="e")

        self.tabs = ctk.CTkTabview(self, height=560)
        self.tabs.pack(fill="both", expand=True, padx=16, pady=8)
        self.tabs.add("آموزش")
        self.tabs.add("تست و برچسب")

        self._build_train_tab(self.tabs.tab("آموزش"))
        self._build_test_tab(self.tabs.tab("تست و برچسب"))

    def _ready_text(self) -> str:
        if self.classifier.is_ready():
            return "مدل آموزش‌دیده آماده است. می‌توانی عکس تست را گروهی وارد کنی."
        return "هنوز مدلی نیست. اول برای هر دسته عکس وارد کن و آموزش بده."

    def _build_train_tab(self, parent) -> None:
        grid = ctk.CTkFrame(parent, fg_color="transparent")
        grid.pack(fill="both", expand=True, pady=(8, 8))
        grid.grid_columnconfigure((0, 1, 2), weight=1)
        grid.grid_rowconfigure(0, weight=1)
        names = self.classifier.class_names
        self.cards: list[ClassCard] = []
        for i in range(3):
            card = ClassCard(grid, i, names[i], self._sync_names)
            card.grid(row=0, column=i, sticky="nsew", padx=8)
            self.cards.append(card)

        bar = ctk.CTkFrame(parent, corner_radius=14)
        bar.pack(fill="x", padx=4, pady=(0, 8))
        controls = ctk.CTkFrame(bar, fg_color="transparent")
        controls.pack(fill="x", padx=12, pady=10)

        self.epochs = ctk.CTkSlider(controls, from_=3, to=30, number_of_steps=27, width=180)
        self.epochs.set(12)
        self.epoch_label = ctk.CTkLabel(controls, text="دورها: ۱۲")
        self.epochs.configure(
            command=lambda v: self.epoch_label.configure(text=f"دورها: {int(round(v))}")
        )

        self.batch = ctk.CTkSlider(controls, from_=2, to=16, number_of_steps=14, width=140)
        self.batch.set(8)
        self.batch_label = ctk.CTkLabel(controls, text="بچ: ۸")
        self.batch.configure(
            command=lambda v: self.batch_label.configure(text=f"بچ: {int(round(v))}")
        )

        self.train_btn = ctk.CTkButton(
            controls, text="شروع آموزش", width=140, height=40, command=self.start_train
        )
        self.stop_btn = ctk.CTkButton(
            controls,
            text="توقف",
            width=80,
            height=40,
            fg_color="#5c3330",
            command=self.stop_train,
            state="disabled",
        )

        for widget in (
            self.epoch_label,
            self.epochs,
            self.batch_label,
            self.batch,
            self.train_btn,
            self.stop_btn,
        ):
            widget.pack(side="right", padx=8)

        self.progress = ctk.CTkProgressBar(bar)
        self.progress.set(0)
        self.progress.pack(fill="x", padx=16, pady=(0, 12))
        self.train_log = ctk.CTkLabel(bar, text="بعد از وارد کردن عکس‌ها، آموزش را بزن.", anchor="e")
        self.train_log.pack(anchor="e", padx=16, pady=(0, 12))

    def _build_test_tab(self, parent) -> None:
        tools = ctk.CTkFrame(parent, fg_color="transparent")
        tools.pack(fill="x", pady=(10, 6))
        ctk.CTkButton(tools, text="ایمپورت گروهی عکس", command=self.import_test_files, height=38).pack(
            side="right", padx=6
        )
        ctk.CTkButton(
            tools,
            text="ایمپورت پوشه تست",
            command=self.import_test_folder,
            height=38,
            fg_color=("#7a5c3a", "#8b5e34"),
        ).pack(side="right", padx=6)
        ctk.CTkButton(tools, text="برچسب بزن", command=self.run_test, height=38).pack(
            side="right", padx=6
        )
        ctk.CTkButton(
            tools,
            text="خروجی CSV",
            command=self.export_csv,
            height=38,
            fg_color="transparent",
            border_width=1,
        ).pack(side="right", padx=6)
        ctk.CTkButton(
            tools,
            text="پاک کردن لیست تست",
            command=self.clear_test,
            height=38,
            fg_color="transparent",
            border_width=1,
        ).pack(side="right", padx=6)

        self.test_count = ctk.CTkLabel(parent, text="۰ فایل تست انتخاب شده", anchor="e")
        self.test_count.pack(anchor="e", padx=8)

        self.results_box = ctk.CTkScrollableFrame(parent, corner_radius=12)
        self.results_box.pack(fill="both", expand=True, padx=4, pady=8)

    def _sync_names(self) -> None:
        names = [card.name_var.get() for card in self.cards]
        self.classifier.set_class_names(names)

    def start_train(self) -> None:
        if self._busy:
            return
        self._sync_names()
        counts = [len(list_images(card.folder)) for card in self.cards]
        if min(counts) < 2:
            messagebox.showerror(
                "داده کم است",
                "هر سه دسته باید حداقل ۲ عکس داشته باشند.\nایمپورت گروهی از فایل یا پوشه را استفاده کن.",
            )
            return
        self._stop = False
        self._busy = True
        self.train_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        epochs = int(round(self.epochs.get()))
        batch = int(round(self.batch.get()))

        def work() -> None:
            try:
                metrics = self.classifier.train(
                    DATASET,
                    epochs=epochs,
                    batch_size=batch,
                    progress=lambda e, total, msg: self.after(
                        0,
                        lambda e=e, total=total, msg=msg: self._on_progress(e, total, msg),
                    ),
                    should_stop=lambda: self._stop,
                )
                self.after(0, lambda m=metrics: self._train_done(m, None))
            except Exception as exc:
                self.after(0, lambda err=exc: self._train_done(None, err))

        threading.Thread(target=work, daemon=True).start()

    def stop_train(self) -> None:
        self._stop = True
        self.train_log.configure(text="در حال توقف...")

    def _on_progress(self, epoch: int, total: int, msg: str) -> None:
        self.progress.set(epoch / max(total, 1))
        self.train_log.configure(text=msg)

    def _train_done(self, metrics: dict | None, error: Exception | None) -> None:
        self._busy = False
        self.train_btn.configure(state="normal")
        self.stop_btn.configure(state="disabled")
        if error:
            self.train_log.configure(text=str(error))
            if str(error) != "آموزش متوقف شد.":
                messagebox.showerror("آموزش ناموفق", str(error))
            return
        acc = metrics["accuracy"] if metrics else 0
        self.train_log.configure(text=f"آموزش تمام شد. دقت روی داده آموزش: {acc:.0%}")
        self.status.configure(text=self._ready_text())
        messagebox.showinfo(
            "تمام",
            "مدل ذخیره شد. برو تب تست و عکس‌ها را گروهی وارد کن تا برچسب بخورند.",
        )

    def import_test_files(self) -> None:
        files = filedialog.askopenfilenames(
            title="عکس‌های تست را گروهی انتخاب کن",
            filetypes=[
                ("Images", "*.jpg *.jpeg *.png *.webp *.bmp *.gif"),
                ("All files", "*.*"),
            ],
        )
        self._add_test(list(files))

    def import_test_folder(self) -> None:
        folder = filedialog.askdirectory(title="پوشه تست")
        if folder:
            self._add_test([folder])

    def _add_test(self, selected: list[str]) -> None:
        incoming = collect_from_selection(selected)
        if not incoming:
            messagebox.showwarning("بدون عکس", "عکسی پیدا نشد.")
            return
        existing = {p.resolve() for p in self.test_paths}
        for path in incoming:
            if path.resolve() not in existing:
                self.test_paths.append(path)
                existing.add(path.resolve())
        self.test_count.configure(text=f"{len(self.test_paths)} فایل تست آماده برچسب‌گذاری")
        self._render_pending()

    def _render_pending(self) -> None:
        for child in self.results_box.winfo_children():
            child.destroy()
        ctk.CTkLabel(
            self.results_box,
            text="برای برچسب‌گذاری دکمه «برچسب بزن» را بزن.",
            anchor="e",
        ).pack(anchor="e", pady=8)
        shown = self.test_paths[:40]
        for path in shown:
            ctk.CTkLabel(self.results_box, text=path.name, anchor="e").pack(anchor="e", padx=8)
        extra = len(self.test_paths) - len(shown)
        if extra > 0:
            ctk.CTkLabel(self.results_box, text=f"... و {extra} فایل دیگر").pack(anchor="e")

    def clear_test(self) -> None:
        self.test_paths.clear()
        self.last_results.clear()
        self.test_count.configure(text="۰ فایل تست انتخاب شده")
        for child in self.results_box.winfo_children():
            child.destroy()

    def run_test(self) -> None:
        if not self.classifier.is_ready():
            messagebox.showerror("مدل نیست", "اول مدل را آموزش بده.")
            return
        if not self.test_paths:
            messagebox.showerror("بدون تست", "اول عکس‌های تست را گروهی وارد کن.")
            return
        try:
            self.last_results = self.classifier.predict_paths(self.test_paths)
        except Exception as exc:
            messagebox.showerror("خطا", str(exc))
            return
        self._show_results()

    def _show_results(self) -> None:
        for child in self.results_box.winfo_children():
            child.destroy()
        counts = {name: 0 for name in self.classifier.class_names}
        for item in self.last_results:
            if item.get("index", -1) >= 0:
                counts[item["label"]] = counts.get(item["label"], 0) + 1
            ResultRow(self.results_box, item, self.classifier.class_names).pack(
                fill="x", pady=5, padx=4
            )
        summary = "  |  ".join(f"{k}: {v}" for k, v in counts.items())
        self.test_count.configure(text=f"{len(self.last_results)} برچسب  ·  {summary}")

    def export_csv(self) -> None:
        if not self.last_results:
            messagebox.showerror("خروجی نیست", "اول تست را اجرا کن.")
            return
        dest = filedialog.asksaveasfilename(
            title="ذخیره نتایج",
            defaultextension=".csv",
            filetypes=[("CSV", "*.csv")],
        )
        if not dest:
            return
        names = self.classifier.class_names
        with open(dest, "w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["file", "label", "confidence", *names])
            for item in self.last_results:
                writer.writerow(
                    [
                        item["path"],
                        item["label"],
                        f"{item['confidence']:.4f}",
                        *[f"{p:.4f}" for p in item.get("probs", [])],
                    ]
                )
        messagebox.showinfo("ذخیره شد", dest)


def main() -> None:
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
