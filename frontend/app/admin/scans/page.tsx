"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import Pager from "@/components/Pager";
import { adminFetch, fmtDate, STATUS_RU } from "@/lib/admin";

interface Row {
  id: string; host: string; status: string; score: number | null; grade: string | null;
  counts: Record<string, number> | null; pages_scanned: number; type: string | null; platform: string | null;
  client_ip: string | null; error: string | null; created_at: string;
}

export default function Scans() {
  const [q, setQ] = useState("");
  const [status, setStatus] = useState("");
  const [page, setPage] = useState(1);
  const [data, setData] = useState<{ total: number; items: Row[] } | null>(null);

  useEffect(() => {
    const p = new URLSearchParams({ q, status, page: String(page) });
    const t = setTimeout(() => adminFetch<{ total: number; items: Row[] }>(`/scans?${p}`).then(setData).catch(() => {}), 250);
    return () => clearTimeout(t);
  }, [q, status, page]);

  return (
    <>
      <h1>Проверки {data && <span className="meta">({data.total})</span>}</h1>
      <div className="toolbar">
        <input className="input" placeholder="Домен, URL или IP" value={q} onChange={(e) => { setQ(e.target.value); setPage(1); }} />
        <select className="input" value={status} onChange={(e) => { setStatus(e.target.value); setPage(1); }}>
          <option value="">Все статусы</option>
          {["done", "failed", "running", "queued"].map((s) => <option key={s} value={s}>{STATUS_RU[s]}</option>)}
        </select>
      </div>
      <div className="table-wrap">
        <table className="table">
          <thead><tr><th>Сайт</th><th>Балл</th><th>Нарушения / риски</th><th>Тип · платформа</th><th>Стр.</th><th>IP</th><th>Дата</th></tr></thead>
          <tbody>
            {data?.items.map((r) => (
              <tr key={r.id}>
                <td><Link href={`/admin/scans/${r.id}`}>{r.host}</Link>{r.status !== "done" && <span className={`pill ${r.status === "failed" ? "fail" : "info"}`}>{STATUS_RU[r.status]}</span>}</td>
                <td>{r.score != null ? <span className={`score-mini ${r.grade}`}>{r.score}</span> : "—"}</td>
                <td>{r.counts ? <><span className="txt-bad">{r.counts.fail}</span> / {r.counts.warn}</> : r.error?.slice(0, 60) || "—"}</td>
                <td className="meta">{[r.type, r.platform].filter(Boolean).join(" · ") || "—"}</td>
                <td>{r.pages_scanned}</td>
                <td className="meta">{r.client_ip}</td>
                <td className="meta">{fmtDate(r.created_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <Pager page={page} total={data?.total || 0} onChange={setPage} />
    </>
  );
}
