import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Ceiling Inspection Agent — Cenomi",
  description: "Open / Close Ceiling Inspection agent demo",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body className="app-bg">
        <div className="pointer-events-none fixed inset-0 -z-0 overflow-hidden" aria-hidden>
          <div className="absolute -top-24 -left-16 h-[28rem] w-[28rem] rounded-full bg-[#C9A84C]/35 blur-[90px]" />
          <div className="absolute top-10 -right-20 h-[26rem] w-[26rem] rounded-full bg-indigo-500/30 blur-[100px]" />
          <div className="absolute bottom-[-6rem] left-1/3 h-[22rem] w-[22rem] rounded-full bg-[#C9A84C]/20 blur-[80px]" />
        </div>
        <div className="relative z-10">{children}</div>
      </body>
    </html>
  );
}
