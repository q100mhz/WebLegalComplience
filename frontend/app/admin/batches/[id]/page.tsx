"use client";

import Link from "next/link";
import { use, useEffect, useState } from "react";
import { adminFetch, type BatchView, fmtDate, fmtEta, STATUS_RU } from "@/lib/admin";

interface Row {
  id: string; host: string; status: string; score: number | null; grade: string | null;
  counts: Record<string, number> | null; pages_scanned: number; type: string | null; platform: string | null;
  error: string | null; finished_at: string | null;
}

export default function BatchDetail({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const [b, setB] = useState<(BatchView & { scans: Row[]; urls_text: string }) | null>(null);
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(false);
  const [filter, setFilter] = useState("");

  const load = () => adminFetch<BatchView & { scans: Row[]; urls_text: string }>(`/batches/${id}`).then(setB).catch(() => {});
  useEffect(() => {
    load();
    const t = setInterval(load, 8000);
    return () => clearInterval(t);
  }, [id]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!b) return <div className="spinner" />;

  async function act(path: string, confirmText?: string) {
    if (confirmText && !confirm(confirmText)) return;
    setBusy(true);
    try {
      const r = await adminFetch<{ id?: string }>(`/batches/${id}/${path}`, { method: "POST" });
      if (r.id && r.id !== id) location.href = `/admin/batches/${r.id}`; // «начать заново» — новый пакет
      else load();
    } finally {
      setBusy(false);
    }
  }

  async function copy() {
    await navigator.clipboard.writeText(b!.urls_text);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }

  const retryable = (b.counts.failed || 0) + (b.counts.cancelled || 0);

  const rows = filter ? b.scans.filter((s) => s.status === filter) : b.scans;
  const statuses = Object.entries(b.counts);

  return (
    <>
      <p className="meta"><Link href="/admin/batches">← Пакетная проверка</Link></p>
      <h1>{b.name}</h1>
      <div className="card">
        <div className="bar"><div style={{ width: `${(b.finished / Math.max(b.total, 1)) * 100}%` }} /></div>
        <div className="row">
          <span>
            Проверено {b.finished} из {b.total}
            {b.state === "running" ? ` · осталось ${fmtEta(b.eta_minutes) || "оценка появится после первых сайтов"}`
              : b.state === "paused" ? " · на паузе" : " · пакет завершён"}
            {b.avg_score != null && ` · средний балл ${b.avg_score}`}
          </span>
          <span className="fb-actions" style={{ marginTop: 0 }}>
            <a className="btn secondary small" href={`/api/admin/batches/${b.id}/documents.zip`} title="Политика, согласие, чекбоксы, cookie-баннер — папка на каждый сайт">
              Документы всех сайтов (ZIP)
            </a>
            <a className="btn small" href={`/api/admin/export.zip?batch=${b.id}`}>Архив для аудита</a>
          </span>
        </div>
        <div className="fb-actions">
          {b.state === "running" && (
            <button className="btn secondary small" disabled={busy} onClick={() => act("pause")}>⏸ Пауза</button>
          )}
          {b.state === "paused" && (
            <button className="btn small" disabled={busy} onClick={() => act("resume")}>▶ Продолжить</button>
          )}
          {retryable > 0 && (
            <button className="btn secondary small" disabled={busy} onClick={() => act("retry-failed")}>
              ↻ Повторить неудачные и отменённые ({retryable})
            </button>
          )}
          <button className="btn secondary small" disabled={busy}
            onClick={() => act("restart", "Запустить пакет заново? Будет создан новый пакет с тем же списком сайтов, текущий останется для сравнения.")}>
            ⟲ Начать заново
          </button>
          {b.state !== "done" && (
            <button className="link-btn" disabled={busy}
              onClick={() => act("cancel", "Отменить проверку сайтов, которые ещё не начались?")}>Отменить оставшиеся</button>
          )}
        </div>
        {b.state === "paused" && <p className="meta">Пакет на паузе: уже начатый сайт будет проверен до конца, следующие не начнутся, пока вы не нажмёте «Продолжить».</p>}
        {b.state !== "done" && <p className="meta">Архив можно скачать и до завершения — в него попадут уже проверенные сайты.</p>}
      </div>

      <details className="card">
        <summary className="row" style={{ cursor: "pointer" }}>
          <b>Список сайтов ({b.total})</b>
          <span className="meta">скопировать и вставить в новую пакетную проверку</span>
        </summary>
        <textarea className="input mono" readOnly rows={Math.min(12, b.total + 1)} value={b.urls_text} style={{ marginTop: 10 }}
          onFocus={(e) => e.target.select()} />
        <div className="fb-actions">
          <button className="btn small" onClick={copy}>{copied ? "Скопировано ✓" : "Скопировать список"}</button>
        </div>
      </details>

      <div className="filters">
        <button className={filter === "" ? "active" : ""} onClick={() => setFilter("")}>Все ({b.total})</button>
        {statuses.map(([st, n]) => (
          <button key={st} className={filter === st ? "active" : ""} onClick={() => setFilter(st)}>{STATUS_RU[st] || st} ({n})</button>
        ))}
      </div>

      <div className="table-wrap">
        <table className="table">
          <thead><tr><th>#</th><th>Сайт</th><th>Статус</th><th>Балл</th><th>Нарушения / риски</th><th>Тип · платформа</th><th>Стр.</th><th>Завершена</th></tr></thead>
          <tbody>
            {rows.map((s) => (
              <tr key={s.id}>
                <td className="meta">{b.scans.indexOf(s) + 1}</td>
                <td>{["done", "failed"].includes(s.status) ? <Link href={`/admin/scans/${s.id}`}>{s.host}</Link> : s.host}</td>
                <td><span className={`pill ${s.status === "done" ? "pass" : s.status === "failed" ? "fail" : s.status === "running" ? "warn" : "info"}`}>{STATUS_RU[s.status] || s.status}</span></td>
                <td>{s.score != null ? <span className={`score-mini ${s.grade}`}>{s.score}</span> : "—"}</td>
                <td>{s.counts ? <><span className="txt-bad">{s.counts.fail}</span> / {s.counts.warn}</> : <span className="meta">{s.error?.slice(0, 70) || "—"}</span>}</td>
                <td className="meta">{[s.type, s.platform].filter(Boolean).join(" · ") || "—"}</td>
                <td>{s.pages_scanned || "—"}</td>
                <td className="meta">{fmtDate(s.finished_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}
