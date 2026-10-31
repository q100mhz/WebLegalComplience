"use client";

import { useEffect, useState } from "react";

interface DocItem { id: string; title: string; description: string; markdown: string; filename: string }

export default function DocumentsView({ scanId }: { scanId: string }) {
  const [docs, setDocs] = useState<DocItem[] | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const [copied, setCopied] = useState<string | null>(null);

  useEffect(() => {
    fetch(`/api/scans/${scanId}/documents`, { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : []))
      .then(setDocs)
      .catch(() => setDocs([]));
  }, [scanId]);

  if (!docs?.length) return null;

  async function copy(d: DocItem) {
    await navigator.clipboard.writeText(d.markdown);
    setCopied(d.id);
    setTimeout(() => setCopied(null), 2000);
  }

  return (
    <section className="documents">
      <h2 className="findings-title">Документы для вашего сайта</h2>
      <p className="meta">
        Черновики составлены по фактам проверки: ваши формы и их поля, подключённые сервисы и cookies, реквизиты с сайта.
        Заполните поля в [квадратных скобках] и согласуйте с юристом перед публикацией.
      </p>
      <div className="doc-list">
        {docs.map((d) => (
          <div key={d.id} className="card doc-card">
            <div className="doc-head">
              <div>
                <b>{d.title}</b>
                <p className="meta">{d.description}</p>
              </div>
              <div className="fb-actions" style={{ marginTop: 0 }}>
                <a className="btn small" href={`/api/scans/${scanId}/documents/${d.id}.docx`}>Скачать .docx</a>
                <button className="btn secondary small" onClick={() => setOpen(open === d.id ? null : d.id)}>
                  {open === d.id ? "Скрыть" : "Показать"}
                </button>
                <button className="link-btn" onClick={() => copy(d)}>{copied === d.id ? "Скопировано ✓" : "Скопировать текст"}</button>
              </div>
            </div>
            {open === d.id && <pre className="doc-preview">{d.markdown}</pre>}
          </div>
        ))}
      </div>
    </section>
  );
}
