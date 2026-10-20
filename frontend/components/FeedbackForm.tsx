"use client";

import Link from "next/link";
import { useState } from "react";
import { sendFeedback } from "@/lib/api";

export default function FeedbackForm({ scanId, findingId, rkn }: { scanId: string; findingId: string; rkn?: boolean }) {
  const [open, setOpen] = useState(false);
  const [kind, setKind] = useState(rkn ? "rkn_claim" : "false_positive");
  const [comment, setComment] = useState("");
  const [contact, setContact] = useState("");
  const [consent, setConsent] = useState(false);
  const [state, setState] = useState<"idle" | "sending" | "sent">("idle");
  const [error, setError] = useState<string | null>(null);

  if (state === "sent") return <div className="fb-done">Спасибо! Мы проверим и уточним правило.</div>;
  if (!open)
    return (
      <div className="fb-actions">
        <button className="link-btn" onClick={() => setOpen(true)}>
          Это ошибка?
        </button>
        {rkn && (
          <button className="link-btn" onClick={() => { setKind("rkn_claim"); setOpen(true); }}>
            У нас есть уведомление в РКН
          </button>
        )}
      </div>
    );

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setState("sending");
    try {
      await sendFeedback(scanId, { finding_id: findingId, kind, comment, contact, consent });
      setState("sent");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось отправить");
      setState("idle");
    }
  }

  return (
    <form className="fb-form" onSubmit={submit}>
      <select value={kind} onChange={(e) => setKind(e.target.value)}>
        <option value="false_positive">Проверка сработала ошибочно</option>
        {rkn && <option value="rkn_claim">Уведомление в РКН подано</option>}
        <option value="other">Другое</option>
      </select>
      <textarea
        placeholder={kind === "rkn_claim" ? "Регистрационный номер в реестре операторов или дата подачи" : "Что не так? Например: «на сайте есть чекбокс согласия, он внизу формы»"}
        value={comment}
        onChange={(e) => setComment(e.target.value)}
        maxLength={2000}
        required={kind === "rkn_claim"}
        rows={3}
      />
      <input
        type="text"
        placeholder="Контакт для ответа (необязательно)"
        value={contact}
        onChange={(e) => setContact(e.target.value)}
        maxLength={255}
      />
      {contact.trim() && (
        <label className="consent">
          <input type="checkbox" checked={consent} onChange={(e) => setConsent(e.target.checked)} required />
          <span>
            Даю <Link href="/consent" target="_blank">согласие на обработку персональных данных</Link> в соответствии с{" "}
            <Link href="/privacy" target="_blank">политикой</Link>
          </span>
        </label>
      )}
      {error && <div className="form-error">{error}</div>}
      <div className="fb-actions">
        <button className="btn small" type="submit" disabled={state === "sending"}>
          Отправить
        </button>
        <button className="link-btn" type="button" onClick={() => setOpen(false)}>
          Отмена
        </button>
      </div>
    </form>
  );
}
