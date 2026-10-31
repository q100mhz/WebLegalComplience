"use client";

import { useEffect, useState } from "react";
import { adminFetch, fmtDate } from "@/lib/admin";

interface RknRecord { reg_number: string; name: string; basis: string; reg_date: string; start_date: string; url: string }
interface Check { inn: string; found?: boolean; records?: RknRecord[]; checked_at?: string; cached?: boolean; error?: string }
interface Status {
  status?: string; last_ok?: string; last_error?: string; error?: string; requests?: number; errors?: number;
  cached: number; cached_found: number; recent: Check[];
}

export default function Rkn() {
  const [st, setSt] = useState<Status | null>(null);
  const [inn, setInn] = useState("");
  const [res, setRes] = useState<Check | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  const load = () => adminFetch<Status>("/rkn").then(setSt).catch(() => {});
  useEffect(() => { load(); }, []);

  async function search(e: React.FormEvent) {
    e.preventDefault();
    setRes(null); setMsg(""); setBusy(true);
    try { setRes(await adminFetch<Check>(`/rkn/search?inn=${inn}`)); load(); } catch (err) { setMsg(err instanceof Error ? err.message : "Ошибка"); }
    setBusy(false);
  }

  return (
    <>
      <h1>Реестр операторов ПДн (Роскомнадзор)</h1>
      <p className="meta">
        При каждой проверке сайта ИНН владельца сверяется с реестром на портале{" "}
        <a href="https://pd.rkn.gov.ru/operators-registry/operators-list/" target="_blank" rel="noreferrer">pd.rkn.gov.ru</a>.
        Результат хранится 30 дней (если оператор не найден — сутки: уведомление могли подать). Запросы к порталу идут
        не чаще раза в 2 секунды. Если портал недоступен, в отчёте будет «проверьте вручную», а клиент может сообщить
        номер уведомления кнопкой в отчёте — такие обращения приходят в «Обратную связь».
      </p>

      <div className="card">
        <div className="row"><span>Связь с порталом</span><b className={st?.status === "error" ? "txt-bad" : "txt-ok"}>{st?.status === "error" ? "ошибка" : st?.status === "ok" ? "работает" : "ещё не было запросов"}</b></div>
        <div className="row"><span>Последний успешный запрос</span><b>{fmtDate(st?.last_ok)}</b></div>
        <div className="row"><span>Запросов / ошибок всего</span><b>{st?.requests ?? 0} / {st?.errors ?? 0}</b></div>
        <div className="row"><span>ИНН в кеше (найдено в реестре)</span><b>{st?.cached ?? 0} ({st?.cached_found ?? 0})</b></div>
        {st?.status === "error" && st.error && <p className="form-error">Последняя ошибка ({fmtDate(st.last_error)}): {st.error}</p>}
      </div>

      <form className="card" onSubmit={search}>
        <h3>Проверить ИНН</h3>
        <div className="toolbar">
          <input className="input" placeholder="ИНН (10 или 12 цифр)" value={inn} onChange={(e) => setInn(e.target.value.trim())} />
          <button className="btn small" type="submit" disabled={busy}>{busy ? "Запрос к порталу…" : "Проверить"}</button>
        </div>
        {msg && <p className="form-error">{msg}</p>}
        {res && <CheckView c={res} />}
      </form>

      {!!st?.recent.length && (
        <div className="card">
          <h3>Последние проверки</h3>
          {st.recent.map((c) => <CheckView key={c.inn} c={c} compact />)}
        </div>
      )}
    </>
  );
}

function CheckView({ c, compact }: { c: Check; compact?: boolean }) {
  if (c.error) return <p className="form-error">ИНН {c.inn}: портал недоступен — {c.error}</p>;
  if (!c.found) return <div className="row"><span>ИНН {c.inn}</span><span className="txt-bad">нет в реестре · {fmtDate(c.checked_at)}</span></div>;
  return (
    <>
      {c.records!.map((r) => (
        <div key={r.reg_number} className={compact ? "row" : ""}>
          {compact ? (
            <>
              <span>ИНН {c.inn} · {r.name}</span>
              <a href={r.url} target="_blank" rel="noreferrer">№ {r.reg_number}</a>
            </>
          ) : (
            <p>
              <b>{r.name}</b> · ИНН {c.inn}<br />
              <span className="meta">
                Рег. № <a href={r.url} target="_blank" rel="noreferrer">{r.reg_number}</a> · уведомление от {r.reg_date} ·
                обработка с {r.start_date} · {r.basis}
              </span>
            </p>
          )}
        </div>
      ))}
    </>
  );
}
