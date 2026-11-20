const toFa = (n) => String(n).replace(/\d/g, (d) => "۰۱۲۳۴۵۶۷۸۹"[d]);
const $ = (sel) => document.querySelector(sel);

const state = {
  data: null,
  testFiles: [],
  lastResults: [],
  user: null,
  comp: null,
  compPhotos: [],
  compGroups: [],
  photoSearch: "",
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
    document.querySelectorAll(".panel").forEach((panel) => {
      panel.classList.toggle("on", panel.id === `panel-${btn.dataset.tab}`);
    });
  });
});

async function rollSeed(option) {
  const err = $("#seed-error");
  err.hidden = true;
  const body = new FormData();
  body.append("option", option);
  try {
    const data = await api("/api/seed", { method: "POST", body });
    $("#seed-result").textContent =
      `نتیجه: ${toFa(data.number)}  ·  گزینه ${toFa(data.option)}  ·  ${toFa(data.low)} تا ${toFa(data.high)}`;
  } catch (ex) {
    err.hidden = false;
    err.textContent = ex.message;
  }
}

document.querySelectorAll("[data-seed]").forEach((btn) => {
  btn.addEventListener("click", () => rollSeed(btn.dataset.seed));
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
Promise.all([refresh(), checkSession(), loadCompetition()]).catch((err) => {
  $("#status").textContent = err.message;
});

// ---------------- مسابقه ----------------
async function loadCompetition() {
  const data = await api("/api/competition/state");
  state.comp = data;
  $("#comp-summary").textContent =
    `${toFa(data.photos_total)} عکس در all-photo · ${toFa(data.photos_available)} آزاد · ${toFa(data.photos_sold)} فروخته‌شده · ${toFa(data.test_total)} عکس در پوشه test · ${toFa(data.groups_count)} گروه`;
  $("#set-capital").value = data.settings.starting_capital;
  $("#set-price").value = data.settings.default_photo_price;
  $("#set-sellback").value = data.settings.sellback_rate;
  const groups = await api("/api/competition/groups");
  state.compGroups = groups.groups || [];
  const opts = state.compGroups.map((g) => `<option value="${g.id}">${escapeHtml(g.id)}</option>`).join("");
  $("#buy-group").innerHTML = opts;
  $("#detail-group").innerHTML = opts;
  $("#test-group").innerHTML = `<option value="">مدل سراسری</option>` + opts;
  $("#buy-class").innerHTML = (data.classes || []).map((n, i) => `<option value="${i}">${escapeHtml(n)}</option>`).join("");
  renderLeaderboard();
  const photos = await api("/api/competition/photos");
  state.compPhotos = photos.photos || [];
  renderPhotos();
  const ledger = await api("/api/competition/ledger?limit=100");
  renderLedger(ledger.items || []);
}

function renderLeaderboard() {
  $("#leader-table").innerHTML = `
    <thead><tr><th>رتبه</th><th>گروه</th><th>موجودی نقد</th><th>عکس‌ها</th><th>ارزش دارایی</th><th>ارزش خالص</th><th>بهترین دقت تست</th></tr></thead>
    <tbody>${state.compGroups.map((g) => `<tr>
      <td>${toFa(g.rank)}</td><td>${escapeHtml(g.id)}</td>
      <td>${toFa(Math.round(g.cash))}</td><td>${toFa(g.holdings_count)}</td>
      <td>${toFa(Math.round(g.holdings_value))}</td><td>${toFa(Math.round(g.net_worth))}</td>
      <td>${g.best_accuracy == null ? "—" : toFa(Math.round(g.best_accuracy * 100)) + "٪"}</td>
    </tr>`).join("") || `<tr><td colspan="7">هنوز گروهی نیست.</td></tr>`}</tbody>`;
}

function renderPhotos() {
  const q = (state.photoSearch || "").trim();
  const items = state.compPhotos.filter((p) => !q || String(p.number).includes(q) || String(p.owner || "").includes(q));
  $("#photo-table").innerHTML = `
    <thead><tr><th>شماره</th><th>عکس</th><th>قیمت</th><th>وضعیت</th><th>مالک</th><th>دسته</th></tr></thead>
    <tbody>${items.map((p) => `<tr>
      <td>${toFa(p.number)}</td>
      <td><img src="/api/competition/photos/${p.number}/image" alt="" style="width:56px;height:56px;object-fit:cover;border-radius:10px" /></td>
      <td>${toFa(Math.round(p.price))}</td>
      <td>${p.status === "sold" ? "فروخته‌شده" : "آزاد"}</td>
      <td>${escapeHtml(p.owner || "—")}</td><td>${toFa(p.class_index + 1)}</td>
    </tr>`).join("") || `<tr><td colspan="6">عکسی در all-photo نیست.</td></tr>`}</tbody>`;
}

function renderLedger(items) {
  $("#ledger-table").innerHTML = `
    <thead><tr><th>گروه</th><th>شماره عکس</th><th>نوع</th><th>قیمت</th><th>موجودی بعد</th><th>تاریخ</th></tr></thead>
    <tbody>${items.map((t) => `<tr>
      <td>${escapeHtml(t.group_id)}</td><td>${toFa(t.photo_number)}</td>
      <td>${t.type === "buy" ? "خرید" : "فروش"}</td><td>${toFa(Math.round(t.price))}</td>
      <td>${toFa(Math.round(t.balance_after))}</td><td>${escapeHtml((t.created_at || "").slice(0, 19))}</td>
    </tr>`).join("") || `<tr><td colspan="6">هنوز خریدی ثبت نشده.</td></tr>`}</tbody>`;
}

async function showGroupDetail() {
  const id = $("#detail-group").value;
  if (!id) return;
  const g = await api(`/api/competition/groups/${encodeURIComponent(id)}`);
  $("#detail-summary").textContent =
    `${g.id} · موجودی ${toFa(Math.round(g.cash))} · ${toFa(g.holdings_count)} عکس · ارزش دارایی ${toFa(Math.round(g.holdings_value))} · ارزش خالص ${toFa(Math.round(g.net_worth))} · کل خرید ${toFa(Math.round(g.total_spent))}`;
  $("#detail-table").innerHTML = `
    <thead><tr><th>شماره عکس</th><th>قیمت خرید</th><th>دسته</th><th>فایل</th></tr></thead>
    <tbody>${g.holdings.map((h) => `<tr>
      <td>${toFa(h.number)}</td><td>${toFa(Math.round(h.bought_price || 0))}</td>
      <td>${toFa(h.class_index + 1)}</td><td>${escapeHtml(h.filename)}</td>
    </tr>`).join("") || `<tr><td colspan="4">این گروه هنوز عکسی ندارد.</td></tr>`}</tbody>`;
}

async function doBuyOrSell(kind) {
  const err = $("#buy-error");
  err.hidden = true;
  $("#buy-msg").textContent = "";
  const body = new FormData();
  body.append("group_id", $("#buy-group").value);
  body.append("numbers", $("#buy-numbers").value);
  if (kind === "buy") {
    body.append("class_index", $("#buy-class").value);
    if ($("#buy-price").value) body.append("price", $("#buy-price").value);
  }
  try {
    const data = await api(kind === "buy" ? "/api/competition/buy" : "/api/competition/sell", { method: "POST", body });
    $("#buy-msg").textContent = kind === "buy"
      ? `${toFa(data.bought.length)} عکس خریده شد · مجموع ${toFa(Math.round(data.total))} · موجودی بعد ${toFa(Math.round(data.cash_after))}`
      : `${toFa(data.sold.length)} عکس فروخته شد · مجموع ${toFa(Math.round(data.total))} · موجودی بعد ${toFa(Math.round(data.cash_after))}`;
    await loadCompetition();
    await showGroupDetail();
  } catch (ex) {
    err.hidden = false;
    err.textContent = ex.message;
  }
}

$("#save-settings").addEventListener("click", async () => {
  const body = new FormData();
  body.append("starting_capital", $("#set-capital").value);
  body.append("default_photo_price", $("#set-price").value);
  body.append("sellback_rate", $("#set-sellback").value);
  await api("/api/competition/settings", { method: "POST", body });
  await loadCompetition();
  alert("تنظیمات ذخیره شد.");
});
$("#rescan-btn").addEventListener("click", async () => { await api("/api/competition/rescan", { method: "POST" }); await loadCompetition(); });
$("#buy-btn").addEventListener("click", () => doBuyOrSell("buy"));
$("#sell-btn").addEventListener("click", () => doBuyOrSell("sell"));
$("#detail-btn").addEventListener("click", showGroupDetail);
$("#photo-search").addEventListener("input", (e) => { state.photoSearch = e.target.value; renderPhotos(); });
$("#train-group-btn").addEventListener("click", async () => {
  const id = $("#test-group").value;
  if (!id) { alert("برای آموزش مدل گروه، اول یک گروه انتخاب کن."); return; }
  const body = new FormData(); body.append("epochs", "12"); body.append("batch_size", "8");
  try { await api(`/api/competition/train/${encodeURIComponent(id)}`, { method: "POST", body }); $("#comp-test-summary").textContent = "آموزش مدل گروه شروع شد؛ چند دقیقه صبر کن و بعد تست را بزن."; }
  catch (ex) { alert(ex.message); }
});
$("#test-folder-btn").addEventListener("click", async () => {
  const body = new FormData();
  if ($("#test-group").value) body.append("group_id", $("#test-group").value);
  try {
    const data = await api("/api/competition/test-folder", { method: "POST", body });
    const acc = data.accuracy == null ? "" : ` · دقت ${toFa(Math.round(data.accuracy * 100))}٪ روی ${toFa(data.scored)} عکس دارای برچسب واقعی`;
    $("#comp-test-summary").textContent = `${data.model} · ${toFa(data.results.length)} عکس تست${acc}`;
    $("#comp-test-results").innerHTML = data.results.map((item) => `<article class="result">
      <img src="${item.thumb || ""}" alt="" />
      <div class="meta"><b>${item.label} · ${toFa(Math.round(item.confidence * 100))}٪</b>
      <div>عکس ${item.number == null ? "؟" : toFa(item.number)} · ${escapeHtml(item.file || "")}</div></div>
    </article>`).join("");
    await loadCompetition();
  } catch (ex) { alert(ex.message); }
});
