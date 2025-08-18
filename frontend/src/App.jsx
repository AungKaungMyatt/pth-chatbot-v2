import React, { useEffect, useRef, useState } from "react";
import { chat, aiStatus } from "./api";

export default function App() {
  const [messages, setMessages] = useState([
    { role: "assistant", text: "Hi! Ask me about banking cyber safety. မြန်မာလိုလည်း မေးနိုင်ပါတယ်။" }
  ]);
  const [input, setInput] = useState("");
  const [lang, setLang] = useState("auto"); // auto | en | my
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState(null);
  const endRef = useRef(null);

  useEffect(() => { endRef.current?.scrollIntoView({ behavior: "smooth" }); }, [messages]);

  useEffect(() => {
    aiStatus().then(setStatus).catch(()=>setStatus({enabled:false}));
  }, []);

  async function send() {
    const text = input.trim();
    if (!text || busy) return;
    setInput("");
    setMessages(m => [...m, { role: "user", text }]);
    setBusy(true);
    try {
      const resp = await chat({
        message: text,
        allow_ai_fallback: true,
        lang_hint: lang === "auto" ? undefined : lang
      });
      setMessages(m => [...m, { role: "assistant", text: resp.reply }]);
    } catch (e) {
      setMessages(m => [...m, { role: "assistant", text: "Server error: " + (e.message || "") }]);
    } finally {
      setBusy(false);
    }
  }

  function onKeyDown(e) {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); }
  }

  return (
    <div className="layout">
      <aside className="sidebar">
        <div className="brand">Pyit Tine Htaung</div>
        <div className="divider" />
        <div className="box">
          <div className="label">Language</div>
          <select value={lang} onChange={(e)=>setLang(e.target.value)}>
            <option value="auto">Auto</option>
            <option value="en">English</option>
            <option value="my">မြန်မာ</option>
          </select>
        </div>
        <div className="box small muted">
          AI: {status ? (status.enabled ? `enabled (${status.model || "model"})` : "disabled") : "…"}
        </div>
        <div className="spacer" />
        <div className="foot muted small">Education only—no account access.</div>
      </aside>

      <main className="content">
        <div className="messages">
          {messages.map((m, i) => (
            <div key={i} className={`msg ${m.role}`}>
              <div className="bubble"><pre>{m.text}</pre></div>
            </div>
          ))}
          <div ref={endRef} />
        </div>

        <div className="composer">
          <input
            value={input}
            onChange={(e)=>setInput(e.target.value)}
            onKeyDown={onKeyDown}
            placeholder="Type a message…"
            disabled={busy}
          />
          <button onClick={send} disabled={busy}>{busy ? "…" : "Send"}</button>
        </div>
      </main>
    </div>
  );
}