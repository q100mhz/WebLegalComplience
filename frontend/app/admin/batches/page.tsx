"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { adminFetch, type BatchView, fmtDate, fmtEta } from "@/lib/admin";

const MAX = 500;

export default function Batches() {
  const [name, setName] = useState("");
  const [urls, setUrls] = useState("");
  const [items, setItems] = useState<BatchView[]>([]);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const count = useMemo(() => urls.split(/[\s,;]+/).filter(Boolean).length, [urls]);

  const load = () => adminFetch<BatchView[]>("/batches").then(setItems).catch(() => {});
  useEffect(() => {
    load();
    const t = setInterval(load, 10000);
    return () => clearInterval(t);
  }, []);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true); setMsg(null);
    try {
      const r = await adminFetch<{ id: string; total: number; invalid: string[]; duplicates: number }>("/batches", {
        method: "POST", body: JSON.stringify({ name, urls }),
      });
      const notes = [
        r.duplicates ? `повторов убрано: ${r.duplicates}` : "",
        r.invalid.length ? `некорректных адресов пропущено: ${r.invalid.length} (${r.invalid.slice(0, 5).join(", ")}${r.invalid.length > 5 ? "…" : ""})` : "",
      ].filter(Boolean).join("; ");
      setMsg({ ok: true, text: `Пакет запущен, сайтов: ${r.total}.${notes ? " " + notes[0].toUpperCase() + notes.slice(1) + "." : ""}` });
      setUrls(""); setName("");
      load();
    } catch (err) {
      setMsg({ ok: false, text: err instanceof Error ? err.message : "Ошибка" });
    }
    setBusy(false);
  }

  return (
    <>
      <h1>Пакетная проверка</h1>
      <p className="meta">
        Сайты проверяются по очереди в фоне — можно закрыть страницу. Проверки посетителей сайта идут вне очереди
        пакета: они ждут не больше одной текущей пакетной проверки. На один сайт уходит 1–3 минуты, 100 сайтов —
        около 2–4 часов. Когда пакет готов, скачайте его архивом и передайте на аудит.
      </p>

      <form className="card" onSubmit={submit}>
        <label className="note-label">Название пакета (необязательно)
          <input className="input" value={name} onChange={(e) => setName(e.target.value)} placeholder="Например: клиники Москвы" maxLength={255} />
        </label>
        <label className="note-label" style={{ marginTop: 10 }}>Сайты — по одному на строку (можно через запятую или пробел)
          <textarea className="input mono" rows={10} value={urls} onChange={(e) => setUrls(e.target.value)}
            placeholder={"example.ru\nhttps://shop.example.ru\nклиника.рф"} />
        </label>
        <div className="fb-actions">
          <button className="btn small" type="submit" disabled={busy || !count || count > MAX}>
            {busy ? "Запускаем…" : `Запустить проверку${count ? ` (${count})` : ""}`}
          </button>
          {count > MAX && <span className="txt-bad">Не больше {MAX} сайтов в пакете</span>}
          {msg && <span className={msg.ok ? "txt-ok" : "txt-bad"}>{msg.text}</span>}
        </div>
      </form>

      {items.length > 0 && (
        <div className="table-wrap">
          <table className="table">
            <thead><tr><th>Пакет</th><th>Прогресс</th><th>Готово / ошибок</th><th>Средний балл</th><th>Создан</th><th></th></tr></thead>
            <tbody>
              {items.map((b) => (
                <tr key={b.id}>
                  <td><Link href={`/admin/batches/${b.id}`}>{b.name}</Link></td>
                  <td style={{ minWidth: 180 }}>
                    <div className="bar slim"><div style={{ width: `${(b.finished / Math.max(b.total, 1)) * 100}%` }} /></div>
                    <span className="meta">{b.finished} из {b.total}{b.state === "running" ? ` · осталось ${fmtEta(b.eta_minutes) || "…"}` : b.state === "paused" ? " · на паузе" : " · завершён"}</span>
                  </td>
                  <td>{b.counts.done || 0} / <span className={b.counts.failed ? "txt-bad" : ""}>{b.counts.failed || 0}</span></td>
                  <td>{b.avg_score ?? "—"}</td>
                  <td className="meta">{fmtDate(b.created_at)}</td>
                  <td><a className="btn secondary small" href={`/api/admin/export.zip?batch=${b.id}`}>ZIP</a></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
