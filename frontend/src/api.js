// src/api.js
const ENV_BASE =
  (import.meta?.env?.VITE_API_BASE_URL || "").trim() ||
  (import.meta?.env?.VITE_API_BASE || "").trim();

export const BASE = (ENV_BASE || "http://localhost:8000").replace(/\/$/, "");

function withTimeout(ms){const c=new AbortController();const id=setTimeout(()=>c.abort(),ms);return{signal:c.signal,clear:()=>clearTimeout(id)};}

async function http(path,{method="POST",body,headers}={}) {
  const url = `${BASE}${path}`;
  const {signal,clear}=withTimeout(20000);
  try{
    const res = await fetch(url,{
      method,
      headers:{ "Content-Type":"application/json", ...(headers||{}) },
      body: body!=null ? JSON.stringify(body) : undefined,
      signal
    });
    const text = await res.text();
    if(!res.ok){ let err=text||res.statusText; try{err=JSON.parse(text)?.detail||err;}catch{}
      throw new Error(`HTTP ${res.status} ${res.statusText} — ${err}`);}
    return text ? JSON.parse(text) : {};
  } finally { clear(); }
}

export const chat    = (p) => http("/api/chat",    { body: p });
export const analyze = (p) => http("/api/analyze", { body: p });
export async function health(){
  const {signal,clear}=withTimeout(8000);
  try{
    const r = await fetch(`${BASE}/healthz`,{signal});
    if(!r.ok) return {ok:false,status:r.status};
    return {ok:true, ...(await r.json().catch(()=>({})))};
  }catch(e){ return {ok:false,error:e?.name==="AbortError"?"timeout":String(e)} }
  finally{clear();}
}
