const BASE = import.meta.env.VITE_API_BASE || "http://127.0.0.1:8000";

export async function chat(payload) {
  const res = await fetch(`${BASE}/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) throw new Error(`chat failed: ${res.status}`);
  return res.json();
}

export async function aiStatus() {
  const res = await fetch(`${BASE}/admin/ai_status`);
  if (!res.ok) throw new Error(`ai_status failed: ${res.status}`);
  return res.json();
}