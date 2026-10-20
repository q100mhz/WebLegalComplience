"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { adminFetch, fmtDate, STATUS_RU } from "@/lib/admin";

interface Fb { id: number; scan_id: string; host: string | null; finding_id: string; kind: string; comment: string; contact: string; status: string; admin_note: string; created_at: string }

export default function FeedbackPage() {
  const [status, setStatus] = useState("new");
  const [items, setItems] = useState<Fb[]>([]);
  const load = () => adminFetch<{ items: Fb[] }>(`/feedback?status=${status}`).then((d) => setItems(d.items)).catch(() => {});
  useEffect(() => { load(); }, [status]); // eslint-disable-line react-hooks/exhaustive-deps

  async function update(f: Fb, newStatus: string, note: string) {
    await adminFetch(`/feedback/${f.id}`, { method: "PATCH", body: JSON.stringify({ status: newStatus, admin_note: note }) });
    load();
  }

  return (
    <>
      <h1>Обратная связь</h1>
      <div className="filters">
        {["new", "accepted", "fixed", "rejected", ""].map((s) => (
          <button key={s} className={status === s ? "active" : ""} onClick={() => setStatus(s)}>{s ? STATUS_RU[s] : "Все"}</button>
        ))}
      </div>
      {!items.length && <p className="meta">Нет обращений</p>}
      {items.map((f) => <FbCard key={f.id} f={f} onUpdate={update} />)}
    </>
  );
}

function FbCard({ f, onUpdate }: { f: Fb; onUpdate: (f: Fb, s: string, note: string) => void }) {
  const [note, setNote] = useState(f.admin_note);
  return (
    <div className="card fb-card">
      <div className="row">
        <span><b>{STATUS_RU[f.kind]}</b> · правило <code>{f.finding_id}</code> · <Link href={`/admin/scans/${f.scan_id}`}>{f.host}</Link></span>
        <span className="meta">{fmtDate(f.created_at)}</span>
      </div>
      <p>{f.comment || <span className="meta">без комментария</span>}</p>
      {f.contact && <p className="meta">Контакт: {f.contact}</p>}
      <input className="input" placeholder="Заметка (что исправлено в правиле)" value={note} onChange={(e) => setNote(e.target.value)} />
      <div className="fb-actions">
        {["accepted", "fixed", "rejected"].map((s) => (
          <button key={s} className={`btn small ${f.status === s ? "" : "secondary"}`} onClick={() => onUpdate(f, s, note)}>{STATUS_RU[s]}</button>
        ))}
      </div>
    </div>
  );
}
