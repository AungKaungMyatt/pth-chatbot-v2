import React, { useEffect, useRef, useState } from "react";
import { chat, uploadImage } from "./api";   // ⬅️ added upload helper
import "./styles.css";

/* ---- constants / helpers ---- */
const THEME_KEY = "pth_theme";
const SESS_KEY = "pth_sessions";
const AUTH_KEY = "pth_auth";
const PROFILE_KEY = "pth_profile";

const SENSITIVE_INTENTS = new Set([
  "vishing_call",
  "customer_phishing",
  "employee_phishing",
  "urgent_phishing",
  "wire_fraud",
  "sim_swapping",
  "update_personal_info_requests",
  "identity_theft_breach",
  "password_breach",
]);

const NOTE_EN =
  "**Note:** I’m an educational assistant, not your bank. Never share OTP/PIN. For account matters, contact your bank directly.";
const NOTE_MY =
  "**မှတ်ချက်:** ဤကိရိယာသည် ပညာပေးအတွက်သာ ဖြစ်ပြီး သင့်ဘဏ်မဟုတ်ပါ။ OTP/PIN မမျှဝေပါနှင့်။ အကောင့်ဆိုင်ရာအတွက် တရားဝင် App/Website သို့မဟုတ် Hotline ကိုသာ သုံးပါ။";

function stripBackendNote(md = "") {
  return md
    .replace(/\*\*Note:\*\*[\s\S]*$/i, "")
    .replace(/\*\*မှတ်ချက်:\*\*[\s\S]*$/i, "")
    .trim();
}
function maybeAttachNote(text, intent, lang = "en") {
  const clean = stripBackendNote(text);
  if (SENSITIVE_INTENTS.has(intent)) {
    return clean + "\n\n" + (lang === "my" ? NOTE_MY : NOTE_EN);
  }
  return clean;
}

function newSession(title = "New chat") {
  return {
    id: crypto.randomUUID(),
    title,
    messages: [
      {
        role: "assistant",
        text: "Hi! Ask me about banking cyber safety. မြန်မာလိုလည်း မေးနိုင်ပါတယ်။",
      },
    ],
    createdAt: Date.now(),
  };
}

/* ======================================================================== */

export default function App() {
  /* Theme */
  const [theme, setTheme] = useState(
    () => localStorage.getItem(THEME_KEY) || "dark"
  );
  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
    localStorage.setItem(THEME_KEY, theme);
  }, [theme]);

  /* Auth (header) */
  const [auth, setAuth] = useState(() => {
    const saved = localStorage.getItem(AUTH_KEY);
    return saved
      ? JSON.parse(saved)
      : { signedIn: false, name: "", email: "", avatar: "" };
  });
  useEffect(() => {
    localStorage.setItem(AUTH_KEY, JSON.stringify(auth));
  }, [auth]);

  // simple demo handlers (replace with real API later)
  function fakeSignIn() {
    setAuth({
      signedIn: true,
      name: "Demo User",
      email: "demo@example.com",
      avatar: "",
    });
  }
  function fakeSignUp() {
    setAuth({
      signedIn: true,
      name: "New User",
      email: "",
      avatar: "",
    });
    localStorage.setItem(
      PROFILE_KEY,
      JSON.stringify({ name: "New User", email: "", avatar: "" })
    );
  }
  function signOut() {
    setAuth({ signedIn: false, name: "", email: "", avatar: "" });
  }

  const initials = (auth.name || "U")
    .split(/\s+/)
    .slice(0, 2)
    .map((x) => x[0]?.toUpperCase())
    .join("");

  /* Sessions (history) */
  const [sessions, setSessions] = useState(() => {
    const saved = localStorage.getItem(SESS_KEY);
    return saved ? JSON.parse(saved) : [newSession()];
  });
  const [activeId, setActiveId] = useState(() => sessions[0].id);
  useEffect(
    () => localStorage.setItem(SESS_KEY, JSON.stringify(sessions)),
    [sessions]
  );
  const active = sessions.find((s) => s.id === activeId);

  /* Chat state */
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);

  // NEW: uploading state
  const [uploading, setUploading] = useState(false);
  const anyBusy = busy || uploading;

  const endRef = useRef(null);

  // guards against duplicate send
  const sendingRef = useRef(false);
  const lastSendRef = useRef({ text: "", t: 0 });

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [active?.messages]);

  /* -------- immutable helpers (NO mutation) -------- */
  function setActiveSession(updater) {
    setSessions((all) => all.map((s) => (s.id === activeId ? updater(s) : s)));
  }
  function appendUserMessage(text) {
    setActiveSession((s) => ({
      ...s,
      title: s.title === "New chat" ? text.slice(0, 32) || "New chat" : s.title,
      messages: [...s.messages, { role: "user", text }],
    }));
  }
  function appendAssistantMessage(text) {
    setActiveSession((s) => ({
      ...s,
      messages: [...s.messages, { role: "assistant", text }],
    }));
  }

  function onNewChat() {
    const s = newSession();
    setSessions((all) => [s, ...all]);
    setActiveId(s.id);
    setInput("");
    setDrawer(false); // close drawer on mobile
  }
  function onDeleteSession(id) {
    setSessions((all) => {
      const next = all.filter((s) => s.id !== id);
      if (id === activeId) {
        if (next.length) setActiveId(next[0].id);
        else {
          const ns = newSession();
          setActiveId(ns.id);
          return [ns];
        }
      }
      return next.length ? next : [newSession()];
    });
  }

  async function send() {
    const text = input.trim();
    if (!text || !active) return;

    const now = Date.now();
    if (lastSendRef.current.text === text && now - lastSendRef.current.t < 1200)
      return;
    lastSendRef.current = { text, t: now };

    if (sendingRef.current) return;
    sendingRef.current = true;

    setInput("");
    setBusy(true);
    appendUserMessage(text);

    try {
      const resp = await chat({
        message: text,
        allow_ai_fallback: true,
      });

      const intent = resp?.reasoning?.intent ?? resp?.intent ?? null;
      const replyLang = resp?.language || "en";
      const finalText = maybeAttachNote(resp?.reply || "", intent, replyLang);

      appendAssistantMessage(finalText);
    } catch (e) {
      appendAssistantMessage("Server error: " + (e?.message || ""));
    } finally {
      setBusy(false);
      setTimeout(() => {
        sendingRef.current = false;
      }, 0);
    }
  }

  function onKeyDown(e) {
    if (e.repeat) return;
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  }

  /* ===== NEW: file upload ===== */
  const fileRef = useRef(null);

  async function onPickFile(e) {
    const file = e.target.files?.[0];
    if (!file || !active) return;
    try {
      setUploading(true);
      appendUserMessage(`(uploaded: ${file.name})`);

      const report = await uploadImage(file);
      const lines = [
        `**Risk level:** ${report.risk_level} (score ${report.score})`,
        ...(report.findings || []).map(
          (f, i) => `${i + 1}. ${f.rule}${f.detail ? ` — ${f.detail}` : ""}`
        ),
      ];
      appendAssistantMessage(lines.join("\n"));
    } catch (err) {
      appendAssistantMessage(`Upload error: ${err?.message || String(err)}`);
    } finally {
      setUploading(false);
      // allow picking same file again
      e.target.value = "";
    }
  }

  /* ===== NEW: mobile drawer state ===== */
  const [drawer, setDrawer] = useState(false);
  useEffect(() => {
    const onEsc = (e) => e.key === "Escape" && setDrawer(false);
    window.addEventListener("keydown", onEsc);
    return () => window.removeEventListener("keydown", onEsc);
  }, []);

  /* -------------------- UI -------------------- */
  return (
    <div className="layout">
      {/* Sidebar becomes a drawer on small screens */}
      <aside className={"sidebar" + (drawer ? " open" : "")}>
        <div className="side-header">
          <div className="brand">
            <img
              src="/pth.png"
              alt="Pyit Tine Htaung"
              className="logo"
              style={{ width: 28, height: 28, borderRadius: 8, marginRight: 8 }}
            />
            Pyit Tine Htaung
          </div>

        <div className="side-actions">
            {/* mobile-only close button (hidden on desktop via CSS) */}
            <button
              className="icon-btn close-mobile"
              aria-label="Close menu"
              onClick={() => setDrawer(false)}
              title="Close"
            >
              ✕
            </button>

            {/* theme toggle */}
            <button
              className="icon-btn"
              aria-label="Toggle theme"
              onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
              title="Toggle theme"
            >
              {theme === "dark" ? "🌙" : "☀️"}
            </button>

            {/* auth area */}
            {!auth.signedIn ? (
              <div className="auth-actions">
                <button className="btn ghost small" onClick={fakeSignIn}>
                  Sign in
                </button>
              </div>
            ) : (
              <div className="user-menu">
                <button className="avatar-btn" aria-haspopup="menu">
                  {auth.avatar ? (
                    <img src={auth.avatar} alt="avatar" />
                  ) : (
                    <span>{initials}</span>
                  )}
                </button>
                <div className="menu">
                  <div className="menu-header">
                    <div className="menu-name">{auth.name || "User"}</div>
                    {auth.email ? (
                      <div className="menu-email">{auth.email}</div>
                    ) : null}
                  </div>
                  <button
                    className="menu-item"
                    onClick={() => alert("Profile settings coming soon")}
                  >
                    Profile
                  </button>
                  <button
                    className="menu-item"
                    onClick={() => alert("App settings coming soon")}
                  >
                    Settings
                  </button>
                  <div className="menu-divider" />
                  <button className="menu-item danger" onClick={signOut}>
                    Sign out
                  </button>
                </div>
              </div>
            )}
          </div>
        </div>

        <button className="btn new" onClick={onNewChat} disabled={anyBusy}>
          + New chat
        </button>

        <div className="history">
          {sessions.map((s) => (
            <div
              key={s.id}
              className={"history-item" + (s.id === activeId ? " active" : "")}
              onClick={() => {
                setActiveId(s.id);
                setDrawer(false); // close after choosing on mobile
              }}
            >
              <span className="title">{s.title}</span>
              <button
                className="x"
                onClick={(e) => {
                  e.stopPropagation();
                  onDeleteSession(s.id);
                }}
                title="Delete"
              >
                ×
              </button>
            </div>
          ))}
        </div>

        <div className="spacer" />
        <div className="foot muted small">
          Education only — no account access.
        </div>
      </aside>

      {/* mobile overlay */}
      {drawer && <div className="backdrop" onClick={() => setDrawer(false)} />}

      <main className="content">
        {/* mobile top bar (hamburger) */}
        <div className="mobile-bar">
          <button
            className="hamburger"
            aria-label="Open menu"
            onClick={() => setDrawer(true)}
          >
            ☰
          </button>
          <div className="title">Pyit Tine Htaung</div>
          <button
            className="icon-btn"
            aria-label="Toggle theme"
            onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
            title="Toggle theme"
          >
            {theme === "dark" ? "🌞" : "🌙"}
          </button>
        </div>

        <div className="messages">
          {active?.messages.map((m, i) => (
            <div key={i} className={`msg ${m.role}`}>
              <div className="bubble">
                <pre>{m.text}</pre>
              </div>
            </div>
          ))}
          {busy &&
            active?.messages?.[active.messages.length - 1]?.role === "user" && (
              <div className="msg assistant">
                <div className="bubble typing">
                  <span className="dot" />
                  <span className="dot" />
                  <span className="dot" />
                </div>
              </div>
            )}
          <div ref={endRef} />
        </div>

        <div className="composer">
          {/* text area (auto column) */}
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={onKeyDown}
            placeholder="Type a message…"
            rows={2}
            disabled={anyBusy}
          />

          {/* actions column (Upload + Send) */}
          <div style={{ display: "flex", gap: 8 }}>
            <button
              type="button"
              className="btn"
              onClick={() => fileRef.current?.click()}
              disabled={anyBusy}
              title="Upload screenshot"
            >
              {uploading ? "Uploading…" : "📎 Upload"}
            </button>

            <input
              ref={fileRef}
              type="file"
              accept="image/*"
              style={{ display: "none" }}
              onChange={onPickFile}
            />

            <button type="button" onClick={send} disabled={anyBusy}>
              {busy ? "…" : "Send"}
            </button>
          </div>
        </div>
      </main>
    </div>
  );
}
