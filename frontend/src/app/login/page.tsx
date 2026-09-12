import { redirect } from "next/navigation";
import { hasPageSession, getAuthConfig } from "@/lib/auth";
import LoginForm from "./login-form";

export const dynamic = "force-dynamic";

export default async function LoginPage() {
  if (await hasPageSession()) redirect("/campaign");
  return <main className="login-page"><section className="login-card panel"><div className="brand"><span className="brand-mark">l<span>·</span></span><span>lexia<span className="brand-dot">.</span></span></div><div className="login-heading"><p className="eyebrow">ESPACE DE PROSPECTION</p><h1>Retrouvez votre espace.</h1><p>Connectez-vous pour accéder à vos prospects et à vos séquences.</p></div><LoginForm available={Boolean(getAuthConfig())} /><p className="login-help">Accès privé. En cas de perte de vos identifiants, contactez l’administrateur de cet espace.</p></section></main>;
}
