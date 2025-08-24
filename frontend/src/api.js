// frontend/src/api.js

// ---- API base ---------------------------------------------------------------
const ENV_BASE = (import.meta?.env?.VITE_API_BASE_URL || "").trim();

const AUTO_BASE =
  typeof window !== "undefined" &&
  !/^(localhost|127\.0\.0\.1)$/.test(window.location.hostname)
    ? "https://pth-chatbot-v2.onrender.com"
    : "http://localhost:8000";

export const BASE = (ENV_BASE || AUTO_BASE).replace(/\/$/, "");

// ---- Timeouts ---------------------------------------------------------------
// Frontend request timeout (ms). Set this in Netlify as VITE_REQUEST_TIMEOUT_MS
// Example: 90000 for 90s, or 135000 for 135s if your backend LLM_TIMEOUT is 120s.
const REQ_TIMEOUT_MS =
  Number(import.meta?.env?.VITE_REQUEST_TIMEOUT_MS) || 45000;

// Optional separate timeout for uploads (falls back to request timeout).
const UPLOAD_TIMEOUT_MS =
  Number(import.meta?.env?.VITE_UPLOAD_TIMEOUT_MS) || REQ_TIMEOUT_MS;

// Debug helper so we can see values in the browser console
if (typeof window !== "undefined") {
  console.log("[API BASE]", BASE);
  console.log("[TIMEOUT] request =", REQ_TIMEOUT_MS, "ms ; upload =", UPLOAD_TIMEOUT_MS, "ms");
  window.__API_BASE__ = BASE;
  window.__TIMEOUT_MS__ = REQ_TIMEOUT_MS;
  window.__UPLOAD_TIMEOUT_MS__ = UPLOAD_TIMEOUT_MS;
}

// ---- Helpers ---------------------------------------------------------------
function withTimeout(ms) {
  const controller = new AbortController();
  const id = setTimeout(() => controller.abort(), ms);
  return { signal: controller.signal, clear: () => clearTimeout(id) };
}

async function http(path, { method = "POST", body, headers } = {}) {
  const url = `${BASE}${path}`;
  const { signal, clear } = withTimeout(REQ_TIMEOUT_MS);
  try {
    const res = await fetch(url, {
      method,
      headers: { "Content-Type": "application/json", ...(headers || {}) },
      body: body != null ? JSON.stringify(body) : undefined,
      signal,
    });
    const text = await res.text();
    if (!res.ok) {
      let err = text || res.statusText;
      try { err = JSON.parse(text)?.detail || err; } catch {}
      throw new Error(`HTTP ${res.status} ${res.statusText} — ${err}`);
    }
    return text ? JSON.parse(text) : {};
  } finally {
    clear();
  }
}

// ---- Public API -------------------------------------------------------------
export const chat    = (p) => http("/api/chat",    { body: p });
export const analyze = (p) => http("/api/analyze", { body: p });

export async function health() {
  // Keep health fast; don’t use the long app timeout here.
  const { signal, clear } = withTimeout(8000);
  try {
    const r = await fetch(`${BASE}/healthz`, { signal });
    if (!r.ok) return { ok: false, status: r.status };
    return { ok: true, ...(await r.json().catch(() => ({}))) };
  } catch (e) {
    return { ok: false, error: e?.name === "AbortError" ? "timeout" : String(e) };
  } finally {
    clear();
  }
}

export async function uploadImage(file) {
  const fd = new FormData();
  fd.append("file", file); // server expects field name "file"

  const { signal, clear } = withTimeout(UPLOAD_TIMEOUT_MS);
  try {
    const res = await fetch(`${BASE}/api/upload`, {
      method: "POST",
      body: fd,       // DO NOT set Content-Type; browser will set it (multipart/form-data)
      signal,
    });
    if (!res.ok) {
      const txt = await res.text().catch(() => res.statusText);
      throw new Error(`Upload failed: ${res.status} ${txt}`);
    }
    return res.json(); // { risk_level, score, findings, language }
  } finally {
    clear();
  }
}
