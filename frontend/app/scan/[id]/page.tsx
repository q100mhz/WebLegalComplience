"use client";

import Link from "next/link";
import { use, useEffect, useMemo, useState } from "react";
import FeedbackForm from "@/components/FeedbackForm";
import ProfileView from "@/components/ProfileView";
import { getScan } from "@/lib/api";
import type { Finding, Scan, Status } from "@/lib/types";

const STATUS_LABEL: Record<Status, string> = {
  fail: "Нарушения",
  warn: "Риски",
  info: "К сведению",
  pass: "В порядке",
};
const ICON: Record<Status, string> = { fail: "!", warn: "!", info: "i", pass: "✓" };
const GRADE_LABEL = { good: "Хорошо", medium: "Есть риски", bad: "Высокий риск" };

export default function ScanPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const [scan, setScan] = useState<Scan | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let timer: ReturnType<typeof setTimeout>;
    let stopped = false;
    const poll = async () => {
      try {
        const s = await getScan(id);
        if (stopped) return;
        setScan(s);
        if (s.status === "queued" || s.status === "running" || s.status === "pending") timer = setTimeout(poll, 3000);
      } catch (e) {
        if (!stopped) setError(e instanceof Error ? e.message : "Ошибка загрузки");
      }
    };
    poll();
    return () => {
      stopped = true;
      clearTimeout(timer);
    };
  }, [id]);

  if (error) return <Message title="Не удалось загрузить проверку" text={error} />;
  if (!scan) return <Progress text="Загрузка…" />;
  if (scan.status === "cancelled") return <Message title={`Проверка ${scan.host} отменена`} text="Проверка была отменена администратором." />;
  if (scan.status === "failed")
    return <Message title={`Не удалось проверить ${scan.host}`} text={scan.error || "Неизвестная ошибка"} />;
  if (!scan.report) {
    // ждём старта или первых страниц: посетителю — только сколько осталось
    const pct = scan.status === "running" ? Math.max(4, Math.round((scan.pages_scanned / scan.max_pages) * 100)) : 2;
    return (
      <div className="container">
        <div className="progress-box">
          <div className="spinner" />
          <h2>Проверяем {scan.host}</h2>
          <div className="bar">
            <div style={{ width: `${pct}%` }} />
          </div>
          <div className="meta">{etaText(scan.eta_seconds)}</div>
          {scan.status === "running" && scan.current_url && <div className="current">{scan.current_url}</div>}
          <p className="hint">Первые результаты появятся примерно через 30 секунд после начала проверки.</p>
        </div>
      </div>
    );
  }
  return <ReportView scan={scan} />;
}

function etaText(sec: number | null | undefined): string {
  if (sec == null) return "Подготовка проверки…";
  if (sec < 60) return "Осталось меньше минуты";
  return `Осталось примерно ${Math.ceil(sec / 60)} мин`;
}

function ReportView({ scan }: { scan: Scan }) {
  const r = scan.report!;
  const [filter, setFilter] = useState<Status | "all">("all");
  const items = useMemo(
    () => (filter === "all" ? r.findings : r.findings.filter((f) => f.status === filter)),
    [filter, r.findings],
  );
  const finished = scan.finished_at ? new Date(scan.finished_at).toLocaleString("ru-RU") : "";

  return (
    <div className="container">
      {scan.partial && (
        <div className="partial-banner">
          <div className="spinner small" />
          <div>
            <b>Первые результаты</b> — проверены главная и ключевые страницы ({r.pages.length}). Ниже — проблемы,
            которые уже найдены. Проверка остальных страниц продолжается, оценка и полный отчёт появятся автоматически.{" "}
            {etaText(scan.eta_seconds)}.
            <div className="bar slim"><div style={{ width: `${Math.max(4, Math.round((scan.pages_scanned / scan.max_pages) * 100))}%` }} /></div>
          </div>
        </div>
      )}
      <div className="report-head">
        {scan.partial || r.score == null ? (
          <div className="score pending"><div className="spinner small light" /></div>
        ) : (
          <div className={`score ${r.grade}`}>
            <div>
              {r.score}
              <small>из 100</small>
            </div>
          </div>
        )}
        <div>
          <h1>{scan.host}</h1>
          <div className="meta">
            {scan.partial
              ? `Идёт проверка · проверено страниц: ${scan.pages_scanned} из ${scan.max_pages}`
              : `${GRADE_LABEL[r.grade!]} · проверено страниц: ${r.pages.length} · ${finished}`}
          </div>
          <div className="counts">
            {((scan.partial ? ["fail", "warn"] : ["fail", "warn", "info", "pass"]) as Status[]).map((s) => (
              <span key={s} className={`pill ${s}`}>
                {STATUS_LABEL[s]}: {r.counts[s] ?? 0}{scan.partial ? " пока" : ""}
              </span>
            ))}
          </div>
        </div>
      </div>

      {r.profile && <ProfileView profile={r.profile} />}

      <h2 className="findings-title">Результаты проверки</h2>
      <div className="filters">
        <button className={filter === "all" ? "active" : ""} onClick={() => setFilter("all")}>
          Все ({r.findings.length})
        </button>
        {(["fail", "warn", "info", "pass"] as Status[])
          .filter((s) => r.counts[s])
          .map((s) => (
            <button key={s} className={filter === s ? "active" : ""} onClick={() => setFilter(s)}>
              {STATUS_LABEL[s]} ({r.counts[s]})
            </button>
          ))}
      </div>

      {items.map((f) => (
        <FindingCard key={f.id} f={f} scanId={scan.id} />
      ))}
      {scan.partial && !items.length && <p className="meta">Пока проблем не найдено — проверка продолжается.</p>}

      <div className="cta">
        <div>
          <strong>Полный отчёт с перечнем всех страниц и доказательств</strong>
          <p>Скоро: личный кабинет, PDF-отчёт, шаблоны документов и регулярный мониторинг сайта.</p>
        </div>
        <Link href="/" className="btn secondary">
          Проверить другой сайт
        </Link>
      </div>

      <div className="disclaimer">{r.disclaimer}</div>
    </div>
  );
}

function FindingCard({ f, scanId }: { f: Finding; scanId: string }) {
  const open = f.status === "fail";
  return (
    <details className={`finding ${f.status}`} open={open}>
      <summary>
        <span className="icon">{ICON[f.status]}</span>
        <div>
          <div className="cat">{f.category}</div>
          <div className="ttl">{f.title}</div>
          <p className="sum">{f.summary}</p>
        </div>
      </summary>
      <dl className="body">
        <dt>Норма закона</dt>
        <dd>{f.law}</dd>
        {f.recommendation && (
          <>
            <dt>Что сделать</dt>
            <dd>{f.recommendation}</dd>
          </>
        )}
        {f.penalty && (f.status === "fail" || f.status === "warn") && (
          <>
            <dt>Ответственность</dt>
            <dd>{f.penalty}</dd>
          </>
        )}
        {f.evidence.length > 0 && (
          <>
            <dt>Где найдено</dt>
            <dd>
              <ul>
                {f.evidence.map((e) => (
                  <li key={e}>{e}</li>
                ))}
              </ul>
              {f.evidence_total > f.evidence.length && (
                <div className="locked">
                  Ещё {f.evidence_total - f.evidence.length} — будут доступны в полном отчёте личного кабинета.
                </div>
              )}
            </dd>
          </>
        )}
        {f.status !== "pass" && (
          <dd>
            <FeedbackForm scanId={scanId} findingId={f.id} rkn={f.id === "rkn_registry"} />
          </dd>
        )}
      </dl>
    </details>
  );
}

function Progress({ text }: { text: string }) {
  return (
    <div className="container">
      <div className="progress-box">
        <div className="spinner" />
        <p>{text}</p>
      </div>
    </div>
  );
}

function Message({ title, text }: { title: string; text: string }) {
  return (
    <div className="container">
      <div className="progress-box">
        <h2>{title}</h2>
        <p className="meta">{text}</p>
        <Link href="/" className="btn">
          На главную
        </Link>
      </div>
    </div>
  );
}
