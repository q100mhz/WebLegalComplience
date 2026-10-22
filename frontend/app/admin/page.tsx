"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { adminFetch, fmtDate } from "@/lib/admin";

interface Stats {
  totals: Record<string, number | null>;
  per_day: { date: string; count: number }[];
  grades: Record<string, number>;
  top_violations: { id: string; title: string; fails: number; warns: number }[];
  site_types: { name: string; count: number }[];
  platforms: { name: string; count: number }[];
  rkn: { status?: string; rows?: number; updated?: string; finished_at?: string; error?: string };
}

const TILES: [string, string][] = [
  ["scans_today", "Проверок сегодня"],
  ["scans_7d", "За 7 дней"],
  ["scans_30d", "За 30 дней"],
  ["scans_total", "Всего проверок"],
  ["unique_sites", "Уникальных сайтов"],
  ["unique_ips_30d", "Посетителей (IP) за 30 дн."],
  ["avg_score", "Средний балл"],
  ["failed_7d", "Ошибок сканирования (7 дн.)"],
];

export default function Dashboard() {
  const [s, setS] = useState<Stats | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    adminFetch<Stats>("/stats").then(setS).catch((e) => setError(e.message));
  }, []);
  if (error) return <p className="form-error">{error}</p>;
  if (!s) return <div className="spinner" />;

  const max = Math.max(1, ...s.per_day.map((d) => d.count));
  const gradeTotal = Object.values(s.grades).reduce((a, b) => a + b, 0) || 1;

  return (
    <>
      <h1>Дашборд</h1>
      {(s.totals.feedback_new || s.totals.rules_unverified) ? (
        <div className="alerts">
          {!!s.totals.feedback_new && <Link href="/admin/feedback?status=new" className="pill warn">Новых обращений: {s.totals.feedback_new}</Link>}
          {!!s.totals.rules_unverified && <Link href="/admin/rules" className="pill fail">Не сверено правил: {s.totals.rules_unverified}</Link>}
          {s.rkn.status !== "ok" && <Link href="/admin/rkn" className="pill fail">Реестр РКН: {s.rkn.status || "не загружен"}</Link>}
        </div>
      ) : null}

      <div className="tiles">
        {TILES.map(([k, label]) => (
          <div className="card tile" key={k}>
            <div className="k">{label}</div>
            <div className="v">{s.totals[k] ?? "—"}</div>
          </div>
        ))}
      </div>

      <div className="card">
        <h3>Проверки по дням (30 дней)</h3>
        <div className="bars">
          {s.per_day.map((d) => (
            <div key={d.date} className="bar-col" title={`${d.date}: ${d.count}`}>
              <div style={{ height: `${(d.count / max) * 100}%` }} />
              <span>{d.date.slice(8)}</span>
            </div>
          ))}
          {!s.per_day.length && <p className="meta">Пока нет данных</p>}
        </div>
      </div>

      <div className="two-col">
        <div className="card">
          <h3>Частые нарушения</h3>
          <table className="table">
            <thead><tr><th>Правило</th><th>Нарушений</th><th>Рисков</th></tr></thead>
            <tbody>
              {s.top_violations.map((v) => (
                <tr key={v.id}><td>{v.title}</td><td className="txt-bad">{v.fails}</td><td>{v.warns}</td></tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="card">
          <h3>Оценки сайтов</h3>
          {(["good", "medium", "bad"] as const).map((g) => (
            <div key={g} className="hbar">
              <span>{{ good: "Хорошо", medium: "Есть риски", bad: "Высокий риск" }[g]}</span>
              <div><div className={`fill ${g}`} style={{ width: `${((s.grades[g] || 0) / gradeTotal) * 100}%` }} /></div>
              <b>{s.grades[g] || 0}</b>
            </div>
          ))}
          <h3>Типы сайтов</h3>
          {s.site_types.map((t) => <div key={t.name} className="row"><span>{t.name}</span><b>{t.count}</b></div>)}
          <h3>Платформы</h3>
          {s.platforms.map((t) => <div key={t.name} className="row"><span>{t.name}</span><b>{t.count}</b></div>)}
        </div>
      </div>

      <p className="meta">
        Реестр РКН: {s.rkn.status || "не загружен"}
        {s.rkn.rows ? ` · ${s.rkn.rows.toLocaleString("ru-RU")} записей · данные на ${s.rkn.updated} · обновлён ${fmtDate(s.rkn.finished_at)}` : ""}
      </p>
      {s.rkn.error && <p className="form-error">Ошибка импорта реестра РКН: {s.rkn.error} (<Link href="/admin/rkn">подробнее</Link>)</p>}
    </>
  );
}
