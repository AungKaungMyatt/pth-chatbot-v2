// src/api.js

// 1) Pick BASE from env, else fall back to localhost for dev.
//    Supports either VITE_API_BASE or VITE_API_BASE_URL.
const ENV_BASE =
  import.meta?.env?.VITE_API_BASE?.trim() ||
  import.meta?.env?.VITE_API_BASE_URL?.trim() ||
  "";

export const BASE =
  (ENV_BASE ? ENV_BASE.replace(/\/$/, "") : "") ||
  "http://localhost:8000";

// Simple helper to add a timeout to fetch
function withTimeout(ms, signal) {
  const ctrl = new AbortController();
  const id = setTimeout(() => ctrl.abort(), ms);
  // If a parent signal is supplied, abort our controller when parent aborts
  if (signal) signal.addEventListener("abort", () => ctrl.abort(), { once: true });
  return { signal: ctrl.signal, clear: () => clearTimeout(id) };
}

async function http(path, { method = "POST", body, headers } = {}) {
  const url = `${BASE}${path}`;
  const { signal, clear } = withTimeout(20000); // 20s safety
  try {
    const res = await fetch(url, {
      method,
      headers: { "Content-Type": "application/json", ...(headers || {}) },
      body: body != null ? JSON.stringify(body) : undefined,
      signal,
    });
    const text = await res.text();
    if (!res.ok) {
      // Try to parse JSON error if available
      let errMsg = text || res.statusText;
      try {
        const js = JSON.parse(text);
        errMsg = js?.detail || js?.error || text || res.statusText;
      } catch {}
      throw new Error(`HTTP ${res.status} ${res.statusText} — ${errMsg}`);
    }
    return text ? JSON.parse(text) : {};
  } catch (e) {
    // Normalize AbortError into a friendly message
    if (e?.name === "AbortError") throw new Error("Request timed out");
    throw e;
  } finally {
    clear();
  }
}

// ---- Public API ----

export async function chat({ message, language, allow_ai_fallback }) {
  return http("/chat", { body: { message, language, allow_ai_fallback } });
}

export async function trace({ message, language }) {
  return http("/admin/trace", { body: { message, language } });
}

export async function health() {
  // Prefer GET /health (FastAPI route)
  const { signal, clear } = withTimeout(8000);
  try {
    const res = await fetch(`${BASE}/health`, { signal });
    if (!res.ok) return { ok: false, status: res.status };
    const data = await res.json().catch(() => ({}));
    return { ok: true, ...data };
  } catch (e) {
    if (e?.name === "AbortError") return { ok: false, error: "timeout" };
    return { ok: false, error: String(e) };
  } finally {
    clear();
  }
}

// Backwards-compatible status probe used by UI
export async function aiStatus() {
  const h = await health();
  return h.ok ? { ok: true } : { ok: false, error: h.error || `HTTP ${h.status || "?"}` };
}