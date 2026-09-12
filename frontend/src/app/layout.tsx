import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Lexia · Prospection",
  description: "Votre espace privé pour préparer et suivre votre prospection B2B.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="fr"><body>{children}</body></html>;
}
