"use client";

import { useEffect, useState } from "react";
import { adminFetch, fmtDate } from "@/lib/admin";

interface Rule {
  id: string; category: string; title: string; law: string; koap: string;
  fine_citizen: string; fine_official: string; fine_ip: string; fine_legal: string; fine_repeat: string;
  confidence: string; note: string; verified: boolean; verified_at: string | null; updated_at: string | null;
}

const FINES: [keyof Rule, string][] = [
  ["fine_citizen", "Граждане"], ["fine_official", "Должностные лица"], ["fine_ip", "ИП"],
  ["fine_legal", "Юрлица"], ["fine_repeat", "Повторно"],
];
const CONF: Record<string, string> = { high: "высокая", medium: "средняя", low: "низкая" };

export default function Rules() {
  const [rules, setRules] = useState<Rule[]>([]);
  const [filter, setFilter] = useState<"all" | "unverified">("unverified");
  useEffect(() => { adminFetch<Rule[]>("/rules").then(setRules).catch(() => {}); }, []);
  const shown = rules.filter((r) => filter === "all" || !r.verified);

  return (
    <>
      <h1>Реестр нарушений</h1>
      <p className="meta">
        Суммы штрафов из этого реестра сразу подставляются во все отчёты. «Уверенность» — оценка составителя реестра,
        насколько вероятно, что суммы актуальны. После сверки с действующей редакцией КоАП отметьте «Сверено».
      </p>
      <div className="toolbar">
        <div className="filters">
          <button className={filter === "unverified" ? "active" : ""} onClick={() => setFilter("unverified")}>Не сверено ({rules.filter((r) => !r.verified).length})</button>
          <button className={filter === "all" ? "active" : ""} onClick={() => setFilter("all")}>Все ({rules.length})</button>
        </div>
        <a className="btn secondary small" href="/api/admin/rules.csv">Скачать CSV</a>
      </div>
      {shown.map((r) => <RuleCard key={r.id} rule={r} onSaved={(nr) => setRules((rs) => rs.map((x) => (x.id === nr.id ? nr : x)))} />)}
    </>
  );
}

function RuleCard({ rule, onSaved }: { rule: Rule; onSaved: (r: Rule) => void }) {
  const [r, setR] = useState(rule);
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState("");
  const dirty = JSON.stringify(r) !== JSON.stringify(rule);
  const set = (k: keyof Rule, v: string | boolean) => setR({ ...r, [k]: v });

  async function save(verified = r.verified) {
    setSaving(true);
    try {
      const saved = await adminFetch<Rule>(`/rules/${r.id}`, { method: "PUT", body: JSON.stringify({ ...r, verified }) });
      setR(saved); onSaved(saved); setMsg("Сохранено");
    } catch (e) { setMsg(e instanceof Error ? e.message : "Ошибка"); }
    setSaving(false);
  }

  return (
    <div className={`card rule ${r.verified ? "verified" : ""}`}>
      <div className="row">
        <span className="cat">{r.category} · <code>{r.id}</code></span>
        <span>
          <span className={`pill ${r.confidence === "high" ? "pass" : r.confidence === "medium" ? "warn" : "fail"}`}>уверенность: {CONF[r.confidence]}</span>{" "}
          {r.verified && <span className="pill pass">сверено {fmtDate(r.verified_at)}</span>}
        </span>
      </div>
      <input className="input ttl-input" value={r.title} onChange={(e) => set("title", e.target.value)} />
      <div className="form-grid">
        <label>Норма закона<input className="input" value={r.law} onChange={(e) => set("law", e.target.value)} /></label>
        <label>Статья КоАП<input className="input" value={r.koap} onChange={(e) => set("koap", e.target.value)} /></label>
        {FINES.map(([k, label]) => (
          <label key={k}>{label}, ₽<input className="input" value={r[k] as string} onChange={(e) => set(k, e.target.value)} /></label>
        ))}
        <label>Уверенность
          <select className="input" value={r.confidence} onChange={(e) => set("confidence", e.target.value)}>
            {Object.entries(CONF).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
        </label>
      </div>
      <label className="note-label">Комментарий для сверки<textarea className="input" rows={2} value={r.note} onChange={(e) => set("note", e.target.value)} /></label>
      <div className="fb-actions">
        <button className="btn small" disabled={saving || !dirty} onClick={() => save()}>Сохранить</button>
        {!r.verified ? (
          <button className="btn small secondary" disabled={saving} onClick={() => save(true)}>Сохранить и отметить «Сверено»</button>
        ) : (
          <button className="link-btn" disabled={saving} onClick={() => save(false)}>Снять отметку</button>
        )}
        {msg && <span className="meta">{msg}</span>}
      </div>
    </div>
  );
}
