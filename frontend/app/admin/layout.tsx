"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { adminFetch } from "@/lib/admin";

const NAV = [
  ["/admin", "Дашборд"],
  ["/admin/scans", "Проверки"],
  ["/admin/sites", "Сайты"],
  ["/admin/feedback", "Обратная связь"],
  ["/admin/rules", "Реестр нарушений"],
  ["/admin/rkn", "Реестр РКН"],
];

export default function AdminLayout({ children }: { children: React.ReactNode }) {
  const path = usePathname();
  if (path.startsWith("/admin/login")) return <div className="container">{children}</div>;

  async function logout() {
    await adminFetch("/logout", { method: "POST" }).catch(() => {});
    location.href = "/admin/login";
  }

  return (
    <div className="admin">
      <aside className="admin-nav">
        {NAV.map(([href, label]) => (
          <Link key={href} href={href} className={(href === "/admin" ? path === href : path.startsWith(href)) ? "active" : ""}>
            {label}
          </Link>
        ))}
        <button className="link-btn" onClick={logout}>
          Выйти
        </button>
      </aside>
      <div className="admin-main">{children}</div>
    </div>
  );
}
