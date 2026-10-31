"use client";

import { useEffect, useState } from "react";
import { adminFetch } from "@/lib/admin";

interface Point {
  t: string; started: number; started_batch: number; max_running: number; max_waiting: number;
  avg_wait_s: number | null; max_wait_s: number | null; avg_duration_s: number | null;
}
interface Load {
  hours: number; bucket_s: number; series: Point[]; peaks: Point[];
  now: { running: number; waiting: number; pending_batch: number; limit: number; avg_duration_s: number };
}
interface Runtime { scan_concurrency: number; max: number; recommended: number; cpus: number; mem_gb: number | null }

const RANGES: [number, string][] = [[6, "6 часов"], [24, "24 часа"], [168, "7 дней"]];

const time = (iso: string, hours: number) => {
  const d = new Date(iso);
  return hours > 24
    ? d.toLocaleString("ru-RU", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" })
    : d.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
};
const dur = (s: number | null) => (s == null ? "—" : s < 60 ? `${s} с` : `${Math.floor(s / 60)} мин ${s % 60} с`);

export default function LoadPanel() {
  const [hours, setHours] = useState(24);
  const [load, setLoad] = useState<Load | null>(null);
  const [rt, setRt] = useState<Runtime | null>(null);
  const [limit, setLimit] = useState<number>(1);
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState("");

  useEffect(() => {
    const get = () => adminFetch<Load>(`/load?hours=${hours}`).then(setLoad).catch(() => {});
    get();
    const t = setInterval(get, 30000);
    return () => clearInterval(t);
  }, [hours]);
  useEffect(() => {
    adminFetch<Runtime>("/settings").then((r) => { setRt(r); setLimit(r.scan_concurrency); }).catch(() => {});
  }, []);

  async function save() {
    setSaving(true); setMsg("");
    try {
      const r = await adminFetch<Runtime>("/settings", { method: "PUT", body: JSON.stringify({ scan_concurrency: limit }) });
      setRt(r);
      setMsg("Сохранено — применится в течение 5 секунд");
    } catch (e) { setMsg(e instanceof Error ? e.message : "Ошибка"); }
    setSaving(false);
  }

  const top = load ? Math.max(1, load.now.limit, ...load.series.map((p) => p.max_running + p.max_waiting)) : 1;
  const labelEvery = load ? Math.max(1, Math.ceil(load.series.length / 8)) : 1;

  return (
    <div className="card">
      <div className="row">
        <h3 style={{ margin: 0 }}>Нагрузка</h3>
        <div className="filters" style={{ margin: 0 }}>
          {RANGES.map(([h, label]) => (
            <button key={h} className={hours === h ? "active" : ""} onClick={() => setHours(h)}>{label}</button>
          ))}
        </div>
      </div>

      {load && (
        <>
          <div className="load-now">
            <span>Сейчас выполняется: <b>{load.now.running}</b> из {load.now.limit}</span>
            <span className={load.now.waiting ? "txt-bad" : ""}>Посетителей ждут старта: <b>{load.now.waiting}</b></span>
            <span>В пакетах ожидают: <b>{load.now.pending_batch}</b></span>
            <span>Средняя проверка: <b>{dur(load.now.avg_duration_s)}</b></span>
          </div>

          <div className="load-chart" role="img" aria-label="Одновременные проверки и ожидание по времени">
            <div className="load-limit" style={{ bottom: `${(load.now.limit / top) * 100}%` }}>
              <span>лимит {load.now.limit}</span>
            </div>
            {load.series.map((p, i) => (
              <div
                key={p.t}
                className="load-col"
                title={`${time(p.t, load.hours)}\nвыполнялось одновременно: ${p.max_running}\nждали старта: ${p.max_waiting}\n` +
                  `стартовало проверок: ${p.started}${p.started_batch ? ` (из них пакетных ${p.started_batch})` : ""}\n` +
                  `ожидание: среднее ${dur(p.avg_wait_s)}, максимум ${dur(p.max_wait_s)}\nсредняя проверка: ${dur(p.avg_duration_s)}`}
              >
                <div className="seg wait" style={{ height: `${(p.max_waiting / top) * 100}%` }} />
                <div className="seg run" style={{ height: `${(p.max_running / top) * 100}%` }} />
                {i % labelEvery === 0 && <span className="lbl">{time(p.t, load.hours)}</span>}
              </div>
            ))}
          </div>
          <div className="legend">
            <span><i className="run" /> выполнялось одновременно (пик)</span>
            <span><i className="wait" /> посетители ждали старта (пик)</span>
            <span className="meta">Наведите на столбец, чтобы увидеть подробности</span>
          </div>

          {load.peaks.some((p) => p.max_waiting > 0) ? (
            <>
              <h3>Пиковые моменты</h3>
              <table className="table">
                <thead><tr><th>Когда</th><th>Выполнялось</th><th>Ждали</th><th>Макс. ожидание</th><th>Стартовало</th></tr></thead>
                <tbody>
                  {load.peaks.filter((p) => p.max_waiting > 0).map((p) => (
                    <tr key={p.t}>
                      <td>{time(p.t, 168)}</td><td>{p.max_running}</td><td className="txt-bad">{p.max_waiting}</td>
                      <td>{dur(p.max_wait_s)}</td><td>{p.started}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          ) : (
            <p className="meta">За выбранный период посетителям не приходилось ждать начала проверки.</p>
          )}
        </>
      )}

      {rt && (
        <div className="concurrency">
          <h3>Одновременные проверки</h3>
          <p className="meta">
            Сервер: {rt.cpus} ядер{rt.mem_gb ? `, ${rt.mem_gb} ГБ памяти` : ""}. Каждая проверка — отдельный браузер
            (~0,8 ГБ). Рекомендуется не больше <b>{rt.recommended}</b>: при нехватке памяти падают все проверки сразу.
          </p>
          <div className="fb-actions">
            <select className="input" style={{ width: "auto" }} value={limit} onChange={(e) => setLimit(Number(e.target.value))}>
              {Array.from({ length: rt.max }, (_, i) => i + 1).map((n) => (
                <option key={n} value={n}>{n}{n === rt.recommended ? " (рекомендуется)" : n > rt.recommended ? " — выше рекомендуемого" : ""}</option>
              ))}
            </select>
            <button className="btn small" disabled={saving || limit === rt.scan_concurrency} onClick={save}>Сохранить</button>
            {msg && <span className="meta">{msg}</span>}
          </div>
          {limit > rt.recommended && <p className="txt-bad">Выше рекомендуемого для этого сервера — следите за падениями проверок.</p>}
        </div>
      )}
    </div>
  );
}
