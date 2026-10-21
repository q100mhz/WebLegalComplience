import type { Metadata } from "next";
import Link from "next/link";
import { COMPANY } from "@/lib/company";
import "./globals.css";

export const metadata: Metadata = {
  title: "WebLegalCompliance — проверка сайта на соответствие законодательству РФ",
  description:
    "Бесплатная автоматическая проверка сайта: 152-ФЗ о персональных данных, cookies, иностранные сервисы, маркировка рекламы, права потребителей.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ru">
      <body>
        <header className="header">
          <div className="container">
            <Link href="/" className="logo">
              WebLegal<span>Compliance</span>
            </Link>
            <nav>Проверка сайтов по законодательству РФ</nav>
          </div>
        </header>
        <main>{children}</main>
        <footer className="footer">
          <div className="container">
            <p>
              <Link href="/privacy">Политика обработки персональных данных</Link>
              <Link href="/consent">Согласие на обработку ПДн</Link>
            </p>
            © {new Date().getFullYear()} WebLegalCompliance · {COMPANY.name}, ИНН {COMPANY.inn}, {COMPANY.email}. 0+
            <br />
            Результаты проверки носят информационный характер и не являются юридической консультацией.
          </div>
        </footer>
      </body>
    </html>
  );
}
