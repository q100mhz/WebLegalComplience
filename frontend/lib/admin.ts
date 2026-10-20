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
  queued: "в очереди", running: "идёт", done: "готово", failed: "ошибка",
  new: "новое", accepted: "принято", rejected: "отклонено", fixed: "исправлено",
  false_positive: "ложное срабатывание", rkn_claim: "уведомление РКН", other: "другое",
};
