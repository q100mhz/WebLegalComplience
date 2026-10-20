"use client";

import { useEffect, useState } from "react";
import { adminFetch, fmtDate } from "@/lib/admin";

interface Meta { status?: string; rows?: number; updated?: string; source?: string; started_at?: string; finished_at?: string; error?: string }
interface Lookup { loaded: boolean; updated?: string; matches?: Record<string, { name: string; reg_number: string; reg_date: string }[]> }

export default function Rkn() {
  const [meta, setMeta] = useState<Meta>({});
  const [inn, setInn] = useState("");
  const [res, setRes] = useState<Lookup | null>(null);
  const [msg, setMsg] = useState("");
  const load = () => adminFetch<Meta>("/rkn").then(setMeta).catch(() => {});
  useEffect(() => { load(); const t = setInterval(load, 10000); return () => clearInterval(t); }, []);

  async function runImport() {
    const r = await adminFetch<{ queued: boolean }>("/rkn/import", { method: "POST" });
    setMsg(r.queued ? "Импорт поставлен в очередь" : "Импорт уже выполняется");
  }
  async function search(e: React.FormEvent) {
    e.preventDefault();
    setRes(null);
    try { setRes(await adminFetch<Lookup>(`/rkn/search?inn=${inn}`)); setMsg(""); } catch (err) { setMsg(err instanceof Error ? err.message : "Ошибка"); }
  }

  return (
    <>
      <h1>Реестр операторов ПДн (Роскомнадзор)</h1>
      <p className="meta">
        Источник — открытые данные РКН. Реестр обновляется автоматически каждую ночь, в отчётах сайт проверяется по ИНН,
        найденному на сайте. Если оператора нет в открытых данных, клиент может сообщить номер уведомления кнопкой в отчёте —
        такие обращения появляются в разделе «Обратная связь».
      </p>
      <div className="card">
        <div className="row"><span>Статус</span><b>{meta.status || "не загружен"}</b></div>
        <div className="row"><span>Записей</span><b>{meta.rows?.toLocaleString("ru-RU") ?? "—"}</b></div>
        <div className="row"><span>Данные РКН на</span><b>{meta.updated || "—"}</b></div>
        <div className="row"><span>Последний импорт</span><b>{fmtDate(meta.finished_at)}</b></div>
        {meta.error && <p className="form-error">{meta.error}</p>}
        <div className="fb-actions">
          <button className="btn small" onClick={runImport} disabled={meta.status === "running"}>Обновить сейчас</button>
          {msg && <span className="meta">{msg}</span>}
        </div>
      </div>
      <form className="card" onSubmit={search}>
        <h3>Поиск по ИНН</h3>
        <div className="toolbar">
          <input className="input" placeholder="ИНН (10 или 12 цифр)" value={inn} onChange={(e) => setInn(e.target.value.trim())} />
          <button className="btn small" type="submit">Найти</button>
        </div>
        {res && !res.loaded && <p className="meta">Реестр ещё не загружен</p>}
        {res?.matches && Object.entries(res.matches).map(([k, recs]) => (
          <div key={k}>
            {!recs.length ? <p className="txt-bad">ИНН {k} не найден</p> : recs.map((r, i) => (
              <p key={i}><b>{r.name}</b><br /><span className="meta">рег. № {r.reg_number || "—"} · {r.reg_date || "—"}</span></p>
            ))}
          </div>
        ))}
      </form>
    </>
  );
}
