"use client";

import { useState, type FormEvent } from "react";
import { ArrowRight } from "lucide-react";

export default function LoginForm({ available }: { available: boolean }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function login(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true); setError("");
    const data = new FormData(event.currentTarget);
    try {
      const response = await fetch("/api/auth/login", { method: "POST", headers: { "Content-Type": "application/json" }, credentials: "same-origin", cache: "no-store", signal: AbortSignal.timeout(15000), body: JSON.stringify({ username: data.get("username"), password: data.get("password") }) });
      const result = await response.json().catch(() => null);
      if (!response.ok) throw new Error(typeof result?.detail === "string" ? result.detail : "Connexion impossible. Réessayez plus tard.");
      window.location.replace("/campaign");
    } catch (cause) { setError(cause instanceof Error && cause.name !== "TimeoutError" ? cause.message : "Connexion interrompue. Réessayez."); setBusy(false); }
  }
  return <form className="editor-form" onSubmit={login}>
    {!available && <p className="inline-warning" role="status">La connexion est temporairement indisponible. Contactez votre administrateur.</p>}
    <label className="field" htmlFor="username"><span>Identifiant</span><input id="username" name="username" autoComplete="username" autoCapitalize="none" spellCheck={false} maxLength={200} required disabled={busy || !available} /></label>
    <label className="field" htmlFor="password"><span>Mot de passe</span><input id="password" name="password" type="password" autoComplete="current-password" maxLength={1024} required disabled={busy || !available} /></label>
    {error && <p className="inline-error" role="alert">{error}</p>}
    <button className="btn primary" disabled={busy || !available}>{busy ? "Connexion…" : "Se connecter"}<ArrowRight size={16} /></button>
  </form>;
}
