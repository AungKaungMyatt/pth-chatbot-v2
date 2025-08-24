// Read API base from env (Netlify uses VITE_*) or fall back to localhost for dev.
const ENV_BASE =
  (import.meta?.env?.VITE_API_BASE_URL || "").trim() ||
  (import.meta?.env?.VITE_API_BASE || "").trim();

export const BASE = (ENV_BASE || "http://localhost:8000").replace(/\/$/, "");

// Small timeout helper
function withTimeout(ms) {
  const ctrl = new AbortController();
  const id = setTimeout(() => ctrl.abort(), ms);
  return { signal: ctrl.signal, clear: () => clearTimeout(id) };
}

async function http(path, { method = "POST", body, headers } = {}) {
  const url = `${BASE}${path}`;
  const { signal, clear } = withTimeout(20000);
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
      try {
        const js = JSON.parse(text);
        err = js?.detail || js?.error || err;
      } catch {}
      throw new Error(`HTTP ${res.status} ${res.statusText} — ${err}`);
    }
    return text ? JSON.parse(text) : {};
  } finally {
    clear();
  }
}

// ---- API wrappers (note the /api prefix) ----
export async function chat({ message, language, allow_ai_fallback }) {
  return http("/api/chat", { body: { message, language, allow_ai_fallback } });
}

export async function analyze({ text, language }) {
  return http("/api/analyze", { body: { text, language } });
}

export async function adminTrace({ message, language }) {
  return http("/api/admin/trace", { body: { message, language } });
}

// health probe used by UI
export async function health() {
  const { signal, clear } = withTimeout(8000);
  try {
    const res = await fetch(`${BASE}/healthz`, { signal });
    if (!res.ok) return { ok: false, status: res.status };
    const data = await res.json().catch(() => ({}));
    return { ok: true, ...data };
  } catch (e) {
    return { ok: false, error: e?.name === "AbortError" ? "timeout" : String(e) };
  } finally {
    clear();
  }
}

export async function aiStatus() {
  const h = await health();
  return h.ok ? { ok: true } : { ok: false, error: h.error || `HTTP ${h.status || "?"}` };
}
