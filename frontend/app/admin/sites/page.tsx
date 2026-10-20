"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { adminFetch, fmtDate } from "@/lib/admin";
import Pager from "@/components/Pager";

interface Site { host: string; id: string; score: number | null; status: string; created_at: string; type: string | null; platform: string | null; counts: Record<string, number> | null; scans: number }

export default function Sites() {
  const [q, setQ] = useState("");
  const [page, setPage] = useState(1);
  const [data, setData] = useState<{ total: number; items: Site[] } | null>(null);
  useEffect(() => {
    const t = setTimeout(() => adminFetch<{ total: number; items: Site[] }>(`/sites?${new URLSearchParams({ q, page: String(page) })}`).then(setData).catch(() => {}), 250);
    return () => clearTimeout(t);
  }, [q, page]);

  return (
    <>
      <h1>Сайты {data && <span className="meta">({data.total})</span>}</h1>
      <p className="meta">Все проверенные сайты — база потенциальных клиентов. После запуска личного кабинета здесь появятся владельцы и тарифы.</p>
      <div className="toolbar"><input className="input" placeholder="Поиск по домену" value={q} onChange={(e) => { setQ(e.target.value); setPage(1); }} /></div>
      <div className="table-wrap">
        <table className="table">
          <thead><tr><th>Сайт</th><th>Последний балл</th><th>Нарушения / риски</th><th>Тип</th><th>Платформа</th><th>Проверок</th><th>Последняя проверка</th></tr></thead>
          <tbody>
            {data?.items.map((s) => (
              <tr key={s.host}>
                <td><Link href={`/admin/scans/${s.id}`}>{s.host}</Link></td>
                <td>{s.score ?? "—"}</td>
                <td>{s.counts ? <><span className="txt-bad">{s.counts.fail}</span> / {s.counts.warn}</> : "—"}</td>
                <td className="meta">{s.type || "—"}</td>
                <td className="meta">{s.platform || "—"}</td>
                <td>{s.scans}</td>
                <td className="meta">{fmtDate(s.created_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <Pager page={page} total={data?.total || 0} onChange={setPage} />
    </>
  );
}
