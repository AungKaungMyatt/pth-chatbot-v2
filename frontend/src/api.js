// src/api.js
const BASE =
  import.meta?.env?.VITE_API_BASE_URL?.replace(/\/$/, "") || "http://localhost:8000";

async function http(path, body) {
  const res = await fetch(`${BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const err = await res.text().catch(() => "");
    throw new Error(`HTTP ${res.status}: ${err || res.statusText}`);
  }
  return res.json();
}

export async function chat({ message, language, allow_ai_fallback }) {
  return http("/chat", { message, language, allow_ai_fallback });
}

export async function trace({ message, language }) {
  return http("/admin/trace", { message, language });
}

export async function aiStatus() {
  // quick probe: use trace to check server is alive
  try {
    await trace({ message: "ping" });
    return { ok: true };
  } catch (e) {
    return { ok: false, error: String(e) };
  }
}