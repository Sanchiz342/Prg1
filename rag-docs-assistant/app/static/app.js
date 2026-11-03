"use strict";

const $ = (id) => document.getElementById(id);
const selectedDocs = new Set();

function escapeHtml(text) {
  return String(text).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function storedKey() {
  try { return sessionStorage.getItem("docurag-key") || ""; } catch { return ""; }
}

async function api(path, options = {}, retried = false) {
  const headers = { ...(options.headers || {}) };
  if (storedKey()) headers["X-API-Key"] = storedKey();
  const response = await fetch(path, { ...options, headers });
  if (response.status === 401 && !retried) {
    // The server was started with RAG_API_KEY: ask once, keep it for this tab only.
    const key = prompt("Цей сервер вимагає API-ключ (RAG_API_KEY):");
    if (key) {
      try { sessionStorage.setItem("docurag-key", key); } catch { /* storage unavailable */ }
      return api(path, options, true);
    }
  }
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.detail ? JSON.stringify(body.detail) : `HTTP ${response.status}`);
  return body;
}

// ---- documents ------------------------------------------------------------------------------------------
async function loadDocuments() {
  const docs = await api("/api/documents");
  $("docCount").textContent = `(${docs.length})`;
  const list = $("docList");
  list.innerHTML = "";
  for (const doc of docs) {
    const li = document.createElement("li");
    li.innerHTML = `
      <input type="checkbox" ${selectedDocs.has(doc.id) ? "checked" : ""} aria-label="Шукати в ${escapeHtml(doc.name)}">
      <span class="name" title="${escapeHtml(doc.name)}">${escapeHtml(doc.name)}</span>
      <span class="muted small">${doc.n_chunks}</span>
      <button class="del" title="Видалити">✕</button>`;
    li.querySelector("input").addEventListener("change", (e) => {
      e.target.checked ? selectedDocs.add(doc.id) : selectedDocs.delete(doc.id);
    });
    li.querySelector(".del").addEventListener("click", async () => {
      if (!confirm(`Видалити «${doc.name}»?`)) return;
      await api(`/api/documents/${doc.id}`, { method: "DELETE" });
      selectedDocs.delete(doc.id);
      refresh();
    });
    list.appendChild(li);
  }
}

async function loadHealth() {
  const h = await api("/api/health");
  $("health").textContent = `${h.documents} док. · ${h.chunks} фрагм. · ${h.provider} · ${h.embedder}`;
}

function refresh() {
  loadDocuments().catch(console.error);
  loadHealth().catch(console.error);
}

async function upload(files) {
  if (!files.length) return;
  const form = new FormData();
  for (const f of files) form.append("files", f);
  $("uploadStatus").textContent = "Індексую…";
  try {
    const { results } = await api("/api/documents", { method: "POST", body: form });
    $("uploadStatus").innerHTML = results
      .map((r) => r.status === "error"
        ? `<div class="warn">✗ ${escapeHtml(r.filename)}: ${escapeHtml(r.error)}</div>`
        : `<div>✓ ${escapeHtml(r.filename)} — ${r.status}, ${r.document.n_chunks} фрагм.</div>`)
      .join("");
  } catch (e) {
    $("uploadStatus").textContent = `Помилка: ${e.message}`;
  }
  refresh();
}

const dz = $("dropzone");
$("fileInput").addEventListener("change", (e) => { upload([...e.target.files]); e.target.value = ""; });
dz.addEventListener("dragover", (e) => { e.preventDefault(); dz.classList.add("drag"); });
dz.addEventListener("dragleave", () => dz.classList.remove("drag"));
dz.addEventListener("drop", (e) => { e.preventDefault(); dz.classList.remove("drag"); upload([...e.dataTransfer.files]); });

// ---- chat -----------------------------------------------------------------------------------------------
function addMessage(cls, html) {
  $("empty")?.remove();
  const div = document.createElement("div");
  div.className = `msg ${cls}`;
  div.innerHTML = html;
  $("messages").appendChild(div);
  div.scrollIntoView({ behavior: "smooth", block: "end" });
  return div;
}

function renderAnswer(container, data) {
  const byN = Object.fromEntries(data.sources.map((s) => [s.n, s]));
  const text = escapeHtml(data.answer).replace(/\[(\d+)\]/g, (m, n) =>
    byN[n] ? `<button class="cite" data-n="${n}" title="${escapeHtml(byN[n].title)}">${n}</button>` : m);

  const support = data.support === null ? "—" : `${Math.round(data.support * 100)}%`;
  const grounded = data.grounded
    ? `<span class="ok">✓ з джерелами</span>`
    : `<span class="warn">без джерел</span>`;
  const cited = data.citations.length
    ? `<ul class="cited-list">${data.citations
        .map((c) => `<li><button data-n="${c.n}">[${c.n}] ${escapeHtml(c.title)}</button></li>`)
        .join("")}</ul>`
    : "";
  const warnings = data.warnings.map((w) => `<div class="warning">⚠ ${escapeHtml(w)}</div>`).join("");

  container.innerHTML = `
    <div class="text">${text}</div>
    ${cited}
    <div class="meta">
      ${grounded}
      <span title="Частка речень, слова яких знайдено в процитованих джерелах (лексична евристика)">підкріпленість: ${support}</span>
      <span>${escapeHtml(data.provider)}${data.fallback_used ? " (резерв)" : ""}</span>
      <span>${data.timings_ms.total} мс</span>
    </div>
    ${warnings}`;

  container.querySelectorAll("[data-n]").forEach((el) =>
    el.addEventListener("click", () => {
      container.querySelectorAll(".cite").forEach((c) => c.classList.toggle("active", c.dataset.n === el.dataset.n));
      showSource(byN[el.dataset.n], data.question);
    }));
}

function highlight(text, question) {
  const words = [...new Set(question.toLowerCase().match(/[\p{L}\p{N}]{4,}/gu) || [])];
  let html = escapeHtml(text);
  for (const w of words) {
    const stem = w.slice(0, Math.max(4, w.length - 2)).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    html = html.replace(new RegExp(`(${stem}[\\p{L}\\p{N}]*)`, "giu"), "<mark>$1</mark>");
  }
  return html;
}

function showSource(src, question) {
  $("sourceBody").innerHTML = `
    <div class="source-title">[${src.n}] ${escapeHtml(src.title)}</div>
    <div class="scores">cosine ${src.dense.toFixed(3)} · BM25 ${src.sparse.toFixed(2)} · RRF ${src.score.toFixed(4)} · chunk #${src.chunk_id}</div>
    <div class="source-text">${highlight(src.text, question)}</div>`;
  $("sourcePanel").hidden = false;
  document.querySelector(".layout").classList.add("with-source");
}

$("closeSource").addEventListener("click", () => {
  $("sourcePanel").hidden = true;
  document.querySelector(".layout").classList.remove("with-source");
});

async function ask(question) {
  addMessage("user", escapeHtml(question));
  const bot = addMessage("bot", `<span class="typing">Шукаю в документах…</span>`);
  $("askBtn").disabled = true;
  try {
    const data = await api("/api/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question, doc_ids: selectedDocs.size ? [...selectedDocs] : null }),
    });
    renderAnswer(bot, data);
  } catch (e) {
    bot.innerHTML = `<span class="warn">Помилка: ${escapeHtml(e.message)}</span>`;
  } finally {
    $("askBtn").disabled = false;
    bot.scrollIntoView({ behavior: "smooth", block: "end" });
  }
}

const q = $("question");
$("askForm").addEventListener("submit", (e) => {
  e.preventDefault();
  const question = q.value.trim();
  if (!question) return;
  q.value = "";
  q.style.height = "";
  ask(question);
});
q.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); $("askForm").requestSubmit(); }
});
q.addEventListener("input", () => { q.style.height = "auto"; q.style.height = `${q.scrollHeight}px`; });
document.querySelectorAll(".example").forEach((b) => b.addEventListener("click", () => ask(b.textContent)));

refresh();
