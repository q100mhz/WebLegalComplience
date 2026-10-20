"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { createScan } from "@/lib/api";

export default function ScanForm() {
  const router = useRouter();
  const [url, setUrl] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      const { id } = await createScan(url);
      router.push(`/scan/${id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось запустить проверку");
      setLoading(false);
    }
  }

  return (
    <>
      <form className="scan-form" onSubmit={submit}>
        <input
          type="text"
          inputMode="url"
          autoComplete="url"
          placeholder="example.ru"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          aria-label="Адрес сайта"
          required
        />
        <button className="btn" type="submit" disabled={loading || !url.trim()}>
          {loading ? "Запускаем…" : "Проверить сайт"}
        </button>
      </form>
      {error && <div className="form-error">{error}</div>}
      <div className="hint">Бесплатно, без регистрации. Анализируем до 30 страниц, обычно 1–3 минуты.</div>
    </>
  );
}
