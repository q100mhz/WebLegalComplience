import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";

export const metadata: Metadata = {
  title: "WeLegal Compliance — проверка сайта на соответствие законодательству РФ",
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
              WeLegal<span>Compliance</span>
            </Link>
            <nav>Проверка сайтов по законодательству РФ</nav>
          </div>
        </header>
        <main>{children}</main>
        <footer className="footer">
          <div className="container">
            © {new Date().getFullYear()} WeLegal Compliance. Результаты проверки носят информационный характер и не
            являются юридической консультацией.
          </div>
        </footer>
      </body>
    </html>
  );
}
