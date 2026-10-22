"use client";

import Link from "next/link";
import { use, useEffect, useState } from "react";
import ProfileView from "@/components/ProfileView";
import { adminFetch, fmtDate, STATUS_RU } from "@/lib/admin";
import type { Finding, Report } from "@/lib/types";

interface Detail {
  id: string; url: string; host: string; status: string; score: number | null; client_ip: string | null;
  error: string | null; created_at: string; finished_at: string | null;
  result: (Report & {
    errors?: string[];
    timing?: Record<string, number>;
    stats?: { mode?: string; browser?: number; http?: number };
    truncated?: boolean;
    diagnostics?: Record<string, string | number | null>;
  }) | null;
  feedback: { id: number; finding_id: string; kind: string; comment: string; contact: string; status: string; created_at: string }[];
}

const DIAG: Record<string, string> = {
  url: "Адрес", status: "HTTP-код ответа", title: "Заголовок страницы", text_len: "Символов текста",
  links: "Ссылок на странице", text: "Начало текста", exception: "Исключение",
};

export default function ScanDetail({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const [d, setD] = useState<Detail | null>(null);
  useEffect(() => { adminFetch<Detail>(`/scans/${id}`).then(setD).catch(() => {}); }, [id]);
  if (!d) return <div className="spinner" />;
  const r = d.result;
  const shot = (name?: string | null) => (name ? `/api/admin/scans/${d.id}/shots/${name}` : null);

  async function rescan() {
    const res = await adminFetch<{ id: string }>(`/scans/${d!.id}/rescan`, { method: "POST" });
    location.href = `/scan/${res.id}`;
  }

  return (
    <>
      <h1>{d.host} {d.score != null && <span className={`score-mini ${r?.grade}`}>{d.score}</span>}</h1>
      <p className="meta">
        {d.url} · {STATUS_RU[d.status]} · создана {fmtDate(d.created_at)} · IP {d.client_ip} ·{" "}
        <Link href={`/scan/${d.id}`} target="_blank">публичный отчёт</Link> ·{" "}
        <button className="link-btn" onClick={rescan}>перепроверить</button>
      </p>
      {d.error && <p className="form-error">{d.error}</p>}

      {r?.diagnostics && (
        <div className="card">
          <h3>Диагностика сбоя</h3>
          {Object.entries(r.diagnostics).filter(([k]) => k !== "screenshot").map(([k, v]) => (
            <div key={k} className="row"><span className="meta">{DIAG[k] || k}</span><span className="diag-val">{String(v ?? "—")}</span></div>
          ))}
          {r.diagnostics.screenshot && (
            <>
              <h3>Что увидел сканер</h3>
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img className="shot" src={shot(String(r.diagnostics.screenshot))!} alt="Скриншот при блокировке" />
            </>
          )}
        </div>
      )}

      {r?.timing && (
        <div className="card">
          <h3>Скорость проверки</h3>
          <div className="row"><span>Всего</span><b>{r.timing.total_s} с</b></div>
          <div className="row"><span>Главная страница (браузер)</span><b>{r.timing.home_s} с</b></div>
          <div className="row"><span>Остальные страницы</span><b>{r.timing.inner_s} с · в среднем {r.timing.avg_page_s} с/стр.</b></div>
          {r.stats && (
            <div className="row">
              <span>Режим загрузки</span>
              <b>{r.stats.mode === "hybrid" ? "гибридный" : "только браузер (сайт на JavaScript)"} · браузером {r.stats.browser}, HTTP {r.stats.http}</b>
            </div>
          )}
          {r.truncated && <p className="meta">Достигнут лимит страниц — на сайте есть и другие страницы.</p>}
        </div>
      )}

      {d.feedback.length > 0 && (
        <div className="card">
          <h3>Обратная связь по проверке</h3>
          {d.feedback.map((f) => (
            <div key={f.id} className="row"><span><b>{f.finding_id}</b> · {STATUS_RU[f.kind]}: {f.comment} {f.contact && `(${f.contact})`}</span><span className="pill info">{STATUS_RU[f.status]}</span></div>
          ))}
        </div>
      )}

      {r?.findings && (
        <>
          {r.pages[0]?.screenshot && (
            <div className="card">
              <h3>Главная страница при первом заходе</h3>
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img className="shot" src={shot(r.pages[0].screenshot)!} alt="Скриншот главной" />
            </div>
          )}
          {r.profile && <ProfileView profile={r.profile} />}
          {r.profile && r.profile.forms.some((f) => f.screenshot) && (
            <div className="card">
              <h3>Скриншоты форм</h3>
              <div className="shots">
                {r.profile.forms.filter((f) => f.screenshot).map((f, i) => (
                  <figure key={i}>
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <a href={shot(f.screenshot)!} target="_blank"><img src={shot(f.screenshot)!} alt={f.kind_name} /></a>
                    <figcaption>{f.kind_name} · {f.page.replace(/^https?:\/\//, "")}</figcaption>
                  </figure>
                ))}
              </div>
            </div>
          )}
          <h2 className="findings-title">Полный перечень результатов</h2>
          {r.findings.map((f: Finding) => (
            <details key={f.id} className={`finding ${f.status}`} open={f.status === "fail"}>
              <summary><div><div className="cat">{f.category} · {f.id}</div><div className="ttl">{f.title}</div><p className="sum">{f.summary}</p></div></summary>
              <dl className="body">
                <dt>Норма</dt><dd>{f.law}</dd>
                {f.recommendation && <><dt>Рекомендация</dt><dd>{f.recommendation}</dd></>}
                {f.penalty && <><dt>Ответственность (на момент проверки)</dt><dd>{f.penalty}</dd></>}
                {f.evidence.length > 0 && <><dt>Доказательства ({f.evidence.length})</dt><dd><ul>{f.evidence.map((e) => <li key={e}>{e}</li>)}</ul></dd></>}
              </dl>
            </details>
          ))}
          <div className="card">
            <h3>Просканированные страницы ({r.pages.length})</h3>
            <ul className="plain">{r.pages.map((p) => <li key={p.url}><a href={p.url} target="_blank" rel="noreferrer">{p.url}</a> <span className="meta">{p.status}</span></li>)}</ul>
            {r.errors && r.errors.length > 0 && (<><h3>Ошибки загрузки</h3><ul className="plain meta">{r.errors.map((e) => <li key={e}>{e}</li>)}</ul></>)}
          </div>
        </>
      )}
    </>
  );
}
