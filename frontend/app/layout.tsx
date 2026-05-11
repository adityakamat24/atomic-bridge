import "./globals.css";
import type { Metadata } from "next";
import { GeistSans } from "geist/font/sans";
import { GeistMono } from "geist/font/mono";

export const metadata: Metadata = {
  title: "Atomic Bridge — Talk to your IT system",
  description:
    "Atom plans, the executor proves it. A natural-language mediation layer over ServiceNow-shaped IT data.",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html
      lang="en"
      className={`${GeistSans.variable} ${GeistMono.variable}`}
    >
      <body className="min-h-screen antialiased font-sans text-fg">
        {children}
      </body>
    </html>
  );
}
