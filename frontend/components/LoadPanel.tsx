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
interface Runtime {
  scan_concurrency: number; max: number; recommended: number; cpus: number; mem_gb: number | null;
  max_pages: number; pages_min: number; pages_max: number; pages_default: number;
}

const plural = (n: number, one: string, few: string, many: string) =>
  n % 10 === 1 && n % 100 !== 11 ? one : n % 10 >= 2 && n % 10 <= 4 && (n % 100 < 10 || n % 100 >= 20) ? few : many;

const PAGE_PRESETS = [10, 30, 50, 100, 200, 500];
// Оценка по замерам: главная ~15 с, дальше ~0,8 с/стр. для обычных сайтов и ~3 с/стр. для сайтов на JavaScript
const fmtMin = (m: number) => (m < 90 ? `${Math.max(m, 1)} мин` : `${Math.round(m / 6) / 10} ч`.replace(".", ","));
const estimate = (sites: number, pages: number, parallel = 1) => {
  const fast = (sites * (15 + pages * 0.8)) / 60 / parallel, slow = (sites * (15 + pages * 3)) / 60 / parallel;
  if (slow < 1) return "меньше минуты";
  const a = fmtMin(Math.round(fast)), b = fmtMin(Math.round(slow));
  return a === b ? `≈ ${b}` : `≈ ${a} – ${b}`;
};

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
  const [pages, setPages] = useState<number>(50);
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState("");

  useEffect(() => {
    const get = () => adminFetch<Load>(`/load?hours=${hours}`).then(setLoad).catch(() => {});
    get();
    const t = setInterval(get, 30000);
    return () => clearInterval(t);
  }, [hours]);
  useEffect(() => {
    adminFetch<Runtime>("/settings").then((r) => { setRt(r); setLimit(r.scan_concurrency); setPages(r.max_pages); }).catch(() => {});
  }, []);

  async function save() {
    setSaving(true); setMsg("");
    try {
      const r = await adminFetch<Runtime>("/settings", { method: "PUT", body: JSON.stringify({ scan_concurrency: limit, max_pages: pages }) });
      setRt(r); setLimit(r.scan_concurrency); setPages(r.max_pages);
      setMsg("Сохранено. Применится к новым проверкам; уже идущие доработают со старыми настройками.");
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
          <h3>Настройки проверки</h3>

          <label className="note-label">Одновременных проверок</label>
          <p className="meta">
            Сервер: {rt.cpus} {plural(rt.cpus, "ядро", "ядра", "ядер")}{rt.mem_gb ? `, ${rt.mem_gb} ГБ памяти` : ""}. Каждая проверка — отдельный браузер
            (~0,8 ГБ). Рекомендуется не больше <b>{rt.recommended}</b>: при нехватке памяти падают все проверки сразу.
          </p>
          <select className="input" style={{ width: "auto" }} value={limit} onChange={(e) => setLimit(Number(e.target.value))}>
            {Array.from({ length: rt.max }, (_, i) => i + 1).map((n) => (
              <option key={n} value={n}>{n}{n === rt.recommended ? " (рекомендуется)" : n > rt.recommended ? " — выше рекомендуемого" : ""}</option>
            ))}
          </select>
          {limit > rt.recommended && <p className="txt-bad">Выше рекомендуемого для этого сервера — следите за падениями проверок.</p>}

          <label className="note-label" style={{ marginTop: 16 }}>Глубина проверки — страниц сайта</label>
          <p className="meta">
            Больше страниц — точнее отчёт (найдутся формы и документы в глубине сайта), но дольше проверка и дольше
            ожидание посетителей. По умолчанию {rt.pages_default}. Юридически важные страницы (политика, согласие,
            контакты, оферта) всегда проверяются первыми — даже при небольшой глубине.
          </p>
          <div className="fb-actions" style={{ marginTop: 0 }}>
            <input className="input" type="number" style={{ width: 110 }} min={rt.pages_min} max={rt.pages_max} value={pages}
              onChange={(e) => setPages(Math.max(rt.pages_min, Math.min(rt.pages_max, Number(e.target.value) || rt.pages_min)))} />
            <div className="filters" style={{ margin: 0 }}>
              {PAGE_PRESETS.map((n) => (
                <button key={n} type="button" className={pages === n ? "active" : ""} onClick={() => setPages(n)}>{n}</button>
              ))}
            </div>
          </div>
          <p className="meta">
            Одна проверка: {estimate(1, pages)}. Пакет из 100 сайтов: {estimate(100, pages, limit)}.
            {pages > 100 && " Для глубоких проверок увеличен таймаут и уровень вложенности ссылок."}
          </p>

          <div className="fb-actions">
            <button className="btn small" disabled={saving || (limit === rt.scan_concurrency && pages === rt.max_pages)} onClick={save}>
              Сохранить настройки
            </button>
            {msg && <span className="meta">{msg}</span>}
          </div>
        </div>
      )}
    </div>
  );
}
