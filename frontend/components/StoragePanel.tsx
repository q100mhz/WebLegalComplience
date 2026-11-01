"use client";

import { useEffect, useState } from "react";
import { adminFetch, fmtDate } from "@/lib/admin";

interface Storage {
  data_mb: number; scans_with_data: number; oldest: string | null; disk_free_gb: number; disk_total_gb: number;
  keep_days: number; choices: number[];
}

export default function StoragePanel() {
  const [s, setS] = useState<Storage | null>(null);
  const [keep, setKeep] = useState(90);
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);
  const load = () => adminFetch<Storage>("/storage").then((r) => { setS(r); setKeep(r.keep_days); }).catch(() => {});
  useEffect(() => { load(); }, []);
  if (!s) return null;

  async function saveKeep() {
    setBusy(true);
    await adminFetch("/settings", { method: "PUT", body: JSON.stringify({ keep_days: keep }) });
    setMsg(`Срок хранения — ${keep} дн. Лишнее удаляется каждую ночь или кнопкой «Очистить сейчас».`);
    await load();
    setBusy(false);
  }
  async function cleanup() {
    if (!confirm(`Удалить скриншоты и снимки данных проверок старше ${s!.keep_days} дн.? Сами отчёты останутся.`)) return;
    setBusy(true);
    const r = await adminFetch<{ removed_scans: number; freed_mb: number }>("/storage/cleanup", { method: "POST" });
    setMsg(`Удалены данные ${r.removed_scans} проверок, освобождено ${r.freed_mb} МБ.`);
    await load();
    setBusy(false);
  }

  const lowDisk = s.disk_free_gb < 5;
  return (
    <div className="card">
      <h3>Хранилище</h3>
      <p className="meta">
        Для каждой проверки хранятся скриншоты и снимок собранных данных — они нужны для доказательств, документов и аудита.
        Архивы выгрузок на сервере не хранятся: они собираются на лету при скачивании.
      </p>
      <div className="row"><span>Данные проверок</span><b>{s.data_mb} МБ · {s.scans_with_data} проверок</b></div>
      <div className="row"><span>Самые старые данные</span><b>{fmtDate(s.oldest)}</b></div>
      <div className="row"><span>Свободно на диске сервера</span><b className={lowDisk ? "txt-bad" : ""}>{s.disk_free_gb} из {s.disk_total_gb} ГБ</b></div>
      <div className="fb-actions">
        <span className="meta">Хранить данные проверок</span>
        <select className="input" style={{ width: "auto" }} value={keep} onChange={(e) => setKeep(Number(e.target.value))}>
          {s.choices.map((d) => <option key={d} value={d}>{d} дн.</option>)}
        </select>
        <button className="btn small" disabled={busy || keep === s.keep_days} onClick={saveKeep}>Сохранить</button>
        <button className="btn secondary small" disabled={busy} onClick={cleanup}>Очистить сейчас</button>
      </div>
      <p className="meta">Отчёты проверок остаются в базе всегда; удаляются только скриншоты и снимки данных — у старых отчётов
        не будет скриншотов-доказательств, а документы под сайт по-прежнему генерируются.</p>
      {msg && <p className="txt-ok">{msg}</p>}
    </div>
  );
}
