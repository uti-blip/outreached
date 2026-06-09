import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import Link from "next/link";
import "./globals.css";

const geistSans = Geist({ variable: "--font-geist-sans", subsets: ["latin"] });
const geistMono = Geist_Mono({ variable: "--font-geist-mono", subsets: ["latin"] });

export const metadata: Metadata = {
  title: "Outreached — B2B Outbound",
  description: "B2B outbound orchestration — agency-first",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" className="dark">
      <body className={`${geistSans.variable} ${geistMono.variable} antialiased bg-zinc-950`}>
        <nav className="border-b border-zinc-800 px-8 py-4 flex gap-6">
          <Link href="/" className="text-zinc-400 hover:text-zinc-200 font-medium">
            Outreached
          </Link>
          <Link href="/campaign" className="text-zinc-500 hover:text-zinc-300">
            Campaigns
          </Link>
        </nav>
        {children}
      </body>
    </html>
  );
}
