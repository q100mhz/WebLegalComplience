import { parse } from "./api";

export class Unauthorized extends Error {}

export async function adminFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api/admin${path}`, {
    ...init,
    cache: "no-store",
    headers: init?.body ? { "Content-Type": "application/json", ...init?.headers } : init?.headers,
  });
  if (res.status === 401) {
    if (typeof window !== "undefined" && !location.pathname.startsWith("/admin/login")) {
      location.href = `/admin/login?next=${encodeURIComponent(location.pathname)}`;
    }
    throw new Unauthorized("Требуется вход");
  }
  return parse<T>(res);
}

export const fmtDate = (s: string | null | undefined) => (s ? new Date(s).toLocaleString("ru-RU") : "—");

export const STATUS_RU: Record<string, string> = {
  pending: "ожидает", queued: "в очереди", running: "идёт", done: "готово", failed: "ошибка", cancelled: "отменено",
  new: "новое", accepted: "принято", rejected: "отклонено", fixed: "исправлено",
  false_positive: "ложное срабатывание", rkn_claim: "уведомление РКН", other: "другое",
};

export interface BatchView {
  id: string; name: string; total: number; created_at: string; counts: Record<string, number>;
  finished: number; left: number; avg_score: number | null; eta_minutes: number | null; state: "running" | "done";
}

export const fmtEta = (m: number | null) =>
  m == null ? "" : m < 60 ? `≈ ${m} мин` : `≈ ${Math.floor(m / 60)} ч ${m % 60} мин`;
