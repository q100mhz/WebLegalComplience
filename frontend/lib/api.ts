import type { Scan } from "./types";

export async function parse<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let msg = `Ошибка ${res.status}`;
    try {
      const body = await res.json();
      if (typeof body.detail === "string") msg = body.detail;
      else if (Array.isArray(body.detail)) msg = "Некорректный адрес сайта";
    } catch {}
    throw new Error(msg);
  }
  return res.json();
}

export async function createScan(url: string): Promise<{ id: string }> {
  return parse(
    await fetch("/api/scans", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url }),
    }),
  );
}

export async function getScan(id: string): Promise<Scan> {
  return parse(await fetch(`/api/scans/${id}`, { cache: "no-store" }));
}

export async function sendFeedback(
  scanId: string,
  body: { finding_id: string; kind: string; comment: string; contact: string; consent: boolean },
): Promise<void> {
  await parse(
    await fetch(`/api/scans/${scanId}/feedback`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  );
}
