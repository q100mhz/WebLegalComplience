"use client";

import { useState } from "react";
import { adminFetch } from "@/lib/admin";

export default function Login() {
  const [login, setLogin] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      await adminFetch("/login", { method: "POST", body: JSON.stringify({ login, password }) });
      const next = new URLSearchParams(location.search).get("next");
      location.href = next && next.startsWith("/admin") ? next : "/admin";
    } catch (err) {
      setError(err instanceof Error ? err.message : "Ошибка входа");
    }
  }

  return (
    <form className="card login" onSubmit={submit}>
      <h1>Вход в админку</h1>
      <input className="input" placeholder="Логин" value={login} onChange={(e) => setLogin(e.target.value)} autoComplete="username" />
      <input className="input" type="password" placeholder="Пароль" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" />
      {error && <div className="form-error">{error}</div>}
      <button className="btn" type="submit">Войти</button>
    </form>
  );
}
