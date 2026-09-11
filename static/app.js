const toFa = (n) => String(n).replace(/\d/g, (d) => "۰۱۲۳۴۵۶۷۸۹"[d]);
const $ = (sel) => document.querySelector(sel);

const state = {
  data: null,
  testFiles: [],
  lastResults: [],
  user: null,
  questions: [],
  search: "",
  standings: [],
  standingSearch: "",
};

function isImage(file) {
  const name = (file.name || "").toLowerCase();
  return /\.(jpe?g|png|webp|bmp|gif)$/.test(name) || (file.type || "").startsWith("image/");
}

async function api(url, options) {
  const res = await fetch(url, options);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || "خطای سرور");
  return data;
}

async function refresh() {
  state.data = await api("/api/state");
  renderClasses();
  const ready = state.data.ready;
  $("#status").textContent = ready
    ? "مدل آماده است. برو تب تست و عکس‌ها را گروهی وارد کن."
    : "هنوز مدلی نیست. برای هر سه دسته عکس بریز و آموزش بده.";
  const t = state.data.train;
  if (t) {
    const pct = t.total ? Math.round((100 * t.epoch) / t.total) : 0;
    $("#progress").style.width = `${pct}%`;
    $("#train-log").textContent = t.message;
    $("#train-btn").disabled = !!t.running;
  }
}

function renderClasses() {
  const root = $("#classes");
  root.innerHTML = "";
  state.data.classes.forEach((name, i) => {
    const card = document.createElement("article");
    card.className = "class-card";
    card.innerHTML = `
      <h2>دسته ${toFa(i + 1)}</h2>
      <input type="text" value="${name}" data-i="${i}" />
      <div class="count">${toFa(state.data.counts[i])} عکس</div>
      <div class="drop" data-drop="${i}">
        <strong>اینجا رها کن</strong>
        <p>چند عکس با هم، یا کل پوشه</p>
        <div class="row">
          <label class="mini">فایل‌ها<input type="file" accept="image/*" multiple hidden data-files="${i}" /></label>
          <label class="mini">پوشه<input type="file" webkitdirectory directory multiple hidden data-folder="${i}" /></label>
          <button class="mini danger" type="button" data-clear="${i}">خالی کردن</button>
        </div>
      </div>
      <div class="thumbs">${(state.data.previews[i] || [])
        .map((src) => `<img src="${src}" alt="" />`)
        .join("")}</div>
    `;
    root.appendChild(card);
  });

  root.querySelectorAll("input[type=text]").forEach((el) => {
    el.addEventListener("change", saveNames);
  });
  root.querySelectorAll("[data-files]").forEach((el) => {
    el.addEventListener("change", (e) => uploadTo(+el.dataset.files, [...e.target.files]));
  });
  root.querySelectorAll("[data-folder]").forEach((el) => {
    el.addEventListener("change", (e) => uploadTo(+el.dataset.folder, [...e.target.files]));
  });
  root.querySelectorAll("[data-clear]").forEach((el) => {
    el.addEventListener("click", async () => {
      if (!confirm("عکس‌های این دسته پاک شوند؟")) return;
      await api(`/api/clear/${el.dataset.clear}`, { method: "POST" });
      refresh();
    });
  });
  root.querySelectorAll("[data-drop]").forEach((zone) => bindDrop(zone, (files) => uploadTo(+zone.dataset.drop, files)));
}

async function saveNames() {
  const names = [...document.querySelectorAll(".class-card input[type=text]")].map((el) => el.value.trim());
  state.data = await api("/api/classes", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(names),
  });
}

async function collectDropped(event) {
  const items = [...(event.dataTransfer?.items || [])];
  const files = [];
  const jobs = items.map((item) => {
    const entry = item.webkitGetAsEntry?.();
    if (entry) return walkEntry(entry, files);
    const file = item.getAsFile?.();
    if (file) files.push(file);
    return Promise.resolve();
  });
  await Promise.all(jobs);
  if (!files.length) files.push(...(event.dataTransfer?.files || []));
  return files.filter(isImage);
}

function walkEntry(entry, bucket) {
  return new Promise((resolve) => {
    if (entry.isFile) {
      entry.file((file) => {
        bucket.push(file);
        resolve();
      }, () => resolve());
    } else if (entry.isDirectory) {
      const reader = entry.createReader();
      const read = () => {
        reader.readEntries(async (entries) => {
          if (!entries.length) return resolve();
          await Promise.all(entries.map((child) => walkEntry(child, bucket)));
          read();
        }, () => resolve());
      };
      read();
    } else resolve();
  });
}

function bindDrop(zone, onFiles) {
  zone.addEventListener("dragover", (e) => {
    e.preventDefault();
    zone.classList.add("over");
  });
  zone.addEventListener("dragleave", () => zone.classList.remove("over"));
  zone.addEventListener("drop", async (e) => {
    e.preventDefault();
    zone.classList.remove("over");
    onFiles(await collectDropped(e));
  });
}

async function uploadTo(index, files) {
  const images = [...files].filter(isImage);
  if (!images.length) {
    alert("عکس معتبری پیدا نشد.");
    return;
  }
  const body = new FormData();
  images.forEach((file) => body.append("files", file, file.name));
  $("#status").textContent = `در حال وارد کردن ${toFa(images.length)} عکس...`;
  state.data = await api(`/api/upload/${index}`, { method: "POST", body });
  renderClasses();
  $("#status").textContent = `${toFa(state.data.saved || images.length)} عکس به دسته اضافه شد.`;
}

async function startTrain() {
  const body = new FormData();
  body.append("epochs", $("#epochs").value);
  body.append("batch_size", $("#batch").value);
  await api("/api/train", { method: "POST", body });
  pollTrain();
}

async function pollTrain() {
  const tick = async () => {
    await refresh();
    if (state.data.train?.running) setTimeout(tick, 800);
    else if (state.data.train?.error) alert(state.data.train.error);
  };
  tick();
}

function addTestFiles(files) {
  const incoming = [...files].filter(isImage);
  const seen = new Set(state.testFiles.map((f) => `${f.name}-${f.size}`));
  incoming.forEach((file) => {
    const key = `${file.name}-${file.size}`;
    if (!seen.has(key)) {
      seen.add(key);
      state.testFiles.push(file);
    }
  });
  $("#test-summary").textContent = `${toFa(state.testFiles.length)} فایل تست آماده برچسب‌گذاری`;
  $("#results").innerHTML = state.testFiles
    .slice(0, 40)
    .map((f) => `<div class="result"><div class="meta"><b>${fileName(f)}</b><div>منتظر برچسب</div></div></div>`)
    .join("");
}

function fileName(file) {
  return file.webkitRelativePath || file.name;
}

async function labelTest() {
  if (!state.testFiles.length) {
    alert("اول عکس تست را گروهی وارد کن.");
    return;
  }
  const body = new FormData();
  state.testFiles.forEach((file) => body.append("files", file, fileName(file)));
  const data = await api("/api/predict", { method: "POST", body });
  state.lastResults = data.results;
  const summary = Object.entries(data.counts)
    .map(([k, v]) => `${k}: ${toFa(v)}`)
    .join("  |  ");
  $("#test-summary").textContent = `${toFa(data.results.length)} برچسب  ·  ${summary}`;
  $("#results").innerHTML = data.results
    .map((item) => {
      const probs = (item.probs || [])
        .map((p, i) => {
          const name = data.classes[i];
          return `<div class="prob"><span>${name} ${toFa(Math.round(p * 100))}٪</span><div class="mini-bar"><span style="width:${Math.round(p * 100)}%"></span></div></div>`;
        })
        .join("");
      return `<article class="result">
        <img src="${item.thumb || ""}" alt="" />
        <div class="meta">
          <b>${item.label} · ${toFa(Math.round(item.confidence * 100))}٪</b>
          <div>${item.path}</div>
          ${probs}
        </div>
      </article>`;
    })
    .join("");
}

function exportCsv() {
  if (!state.lastResults.length) {
    alert("اول تست را اجرا کن.");
    return;
  }
  const names = state.data.classes;
  const rows = [["file", "label", "confidence", ...names]];
  state.lastResults.forEach((item) => {
    rows.push([item.path, item.label, item.confidence, ...(item.probs || [])]);
  });
  const csv = rows.map((r) => r.map((x) => `"${String(x).replaceAll('"', '""')}"`).join(",")).join("\n");
  const blob = new Blob(["\ufeff" + csv], { type: "text/csv;charset=utf-8" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = "labels.csv";
  a.click();
}

document.querySelectorAll(".tab").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((b) => b.classList.toggle("on", b === btn));
    $("#panel-train").classList.toggle("on", btn.dataset.tab === "train");
    $("#panel-test").classList.toggle("on", btn.dataset.tab === "test");
    $("#panel-status").classList.toggle("on", btn.dataset.tab === "status");
    $("#panel-standings").classList.toggle("on", btn.dataset.tab === "standings");
    $("#panel-save").classList.toggle("on", btn.dataset.tab === "save");
  });
});

$("#epochs").addEventListener("input", (e) => ($("#epochs-val").textContent = toFa(e.target.value)));
$("#batch").addEventListener("input", (e) => ($("#batch-val").textContent = toFa(e.target.value)));
$("#train-btn").addEventListener("click", () => startTrain().catch((err) => alert(err.message)));
$("#test-files").addEventListener("change", (e) => addTestFiles(e.target.files));
$("#test-folder").addEventListener("change", (e) => addTestFiles(e.target.files));
$("#label-btn").addEventListener("click", () => labelTest().catch((err) => alert(err.message)));
$("#export-btn").addEventListener("click", exportCsv);
$("#clear-test").addEventListener("click", () => {
  state.testFiles = [];
  state.lastResults = [];
  $("#test-summary").textContent = "۰ فایل تست";
  $("#results").innerHTML = "";
});
bindDrop($("#test-drop"), addTestFiles);

function showLoggedOut() {
  state.user = null;
  $("#login-box").hidden = false;
  $("#save-box").hidden = true;
}

function showLoggedIn(username) {
  state.user = username;
  $("#login-box").hidden = true;
  $("#save-box").hidden = false;
  $("#who").textContent = username;
}

async function loadAlbum() {
  if (!state.user) return;
  const data = await api("/api/my/photos");
  $("#album-summary").textContent = `${toFa(data.count)} عکس ذخیره‌شده`;
  $("#album-grid").innerHTML = data.photos
    .map(
      (p) => `<article class="album-item">
        <img src="${p.url}" alt="" />
        <button type="button" data-del="${p.name}">حذف</button>
      </article>`
    )
    .join("");
  $("#album-grid").querySelectorAll("[data-del]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      await api(`/api/my/photos/delete/${encodeURIComponent(btn.dataset.del)}`, { method: "POST" });
      await loadAlbum();
    });
  });
}

async function uploadAlbum(files) {
  const images = [...files].filter(isImage);
  if (!images.length) {
    alert("عکس معتبری پیدا نشد.");
    return;
  }
  const body = new FormData();
  images.forEach((file) => body.append("files", file, file.name));
  await api("/api/my/photos", { method: "POST", body });
  await loadAlbum();
}

function escapeHtml(text) {
  return String(text)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
}

async function loadQuestions() {
  try {
    const data = await api("/api/questions");
    if (data.error) {
      $("#question-summary").textContent = data.error;
      return;
    }
    state.questions = data.items || [];
    const when = data.fetched_at ? new Date(data.fetched_at).toLocaleTimeString("fa-IR") : "";
    $("#question-summary").textContent = state.questions.length
      ? `${toFa(state.questions.length)} ردیف از شیت · به‌روز ${when}`
      : "هنوز ردیفی در شیت خرید ثبت نشده.";
    renderQuestions();
  } catch (err) {
    $("#question-summary").textContent = err.message;
  }
}

function renderQuestions() {
  const q = (state.search || "").trim().toLowerCase();
  const items = state.questions.filter((item) => {
    if (!q) return true;
    return [item.team, item.question, item.price, item.score]
      .map((v) => String(v || "").toLowerCase())
      .some((v) => v.includes(q));
  });
  $("#question-table").innerHTML = `
    <thead><tr>
      <th>آیدی تیم</th><th>شماره سوال</th><th>قیمت</th><th>نمره داده‌شده</th>
    </tr></thead>
    <tbody>
      ${items
        .map(
          (item) => `<tr>
            <td>${escapeHtml(item.team)}</td>
            <td>${escapeHtml(item.question)}</td>
            <td>${escapeHtml(item.price)}</td>
            <td>${escapeHtml(item.score)}</td>
          </tr>`
        )
        .join("") || `<tr><td colspan="4">موردی با این جستجو پیدا نشد.</td></tr>`}
    </tbody>`;
}

async function loadStandings() {
  try {
    const data = await api("/api/standings");
    if (data.error) {
      $("#standing-summary").textContent = data.error;
      return;
    }
    state.standings = data.items || [];
    const when = data.fetched_at ? new Date(data.fetched_at).toLocaleTimeString("fa-IR") : "";
    $("#standing-summary").textContent = state.standings.length
      ? `${toFa(state.standings.length)} تیم · به‌روز ${when}`
      : "استندینگ خالی است.";
    renderStandings();
  } catch (err) {
    $("#standing-summary").textContent = err.message;
  }
}

function renderStandings() {
  const q = (state.standingSearch || "").trim().toLowerCase();
  const items = state.standings.filter((item) => {
    if (!q) return true;
    return [item.rank, item.team, item.total, item.day, item.stock]
      .map((v) => String(v || "").toLowerCase())
      .some((v) => v.includes(q));
  });
  $("#standing-table").innerHTML = `
    <thead><tr>
      <th>رتبه</th><th>آیدی تیم</th><th>امتیاز کل</th><th>امتیاز روز</th><th>قیمت سهام</th>
    </tr></thead>
    <tbody>
      ${items
        .map(
          (item) => `<tr>
            <td>${escapeHtml(item.rank)}</td>
            <td>${escapeHtml(item.team)}</td>
            <td>${escapeHtml(item.total)}</td>
            <td>${escapeHtml(item.day)}</td>
            <td>${escapeHtml(item.stock)}</td>
          </tr>`
        )
        .join("") || `<tr><td colspan="5">موردی با این جستجو پیدا نشد.</td></tr>`}
    </tbody>`;
}

async function submitLogin(username, password, errorEl) {
  errorEl.hidden = true;
  const body = new FormData();
  body.append("username", username);
  body.append("password", password);
  try {
    const me = await api("/api/login", { method: "POST", body });
    showLoggedIn(me.username);
    await loadAlbum();
  } catch (ex) {
    errorEl.hidden = false;
    errorEl.textContent = ex.message;
  }
}

async function checkSession() {
  const me = await api("/api/me");
  if (me.username) {
    showLoggedIn(me.username);
    await loadAlbum();
  } else {
    showLoggedOut();
  }
}

$("#login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  await submitLogin($("#login-user").value.trim(), $("#login-pass").value.trim(), $("#login-error"));
});

$("#logout-btn").addEventListener("click", async () => {
  await fetch("/api/logout", { method: "POST" });
  showLoggedOut();
});
$("#album-files").addEventListener("change", (e) => uploadAlbum(e.target.files));
$("#album-folder").addEventListener("change", (e) => uploadAlbum(e.target.files));
$("#clear-album").addEventListener("click", async () => {
  if (!confirm("عکس‌های ذخیره‌شده پاک شوند؟")) return;
  await api("/api/my/photos/clear", { method: "POST" });
  await loadAlbum();
});
bindDrop($("#album-drop"), uploadAlbum);
$("#question-search").addEventListener("input", (e) => {
  state.search = e.target.value;
  renderQuestions();
});
$("#standing-search").addEventListener("input", (e) => {
  state.standingSearch = e.target.value;
  renderStandings();
});
setInterval(() => {
  loadQuestions();
  loadStandings();
}, 4000);

Promise.all([refresh(), checkSession(), loadQuestions(), loadStandings()]).catch((err) => {
  $("#status").textContent = err.message;
});
