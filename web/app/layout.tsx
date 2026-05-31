import type { Metadata } from "next";
import Navbar from "@/components/Navbar";
import "./globals.css";

export const metadata: Metadata = {
  title: "全球 AI 治理监测平台",
  description: "AI 安全与治理动态智能感知门户",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="zh-CN">
      <body className="min-h-screen bg-slate-50 antialiased">
        <Navbar />
        <main className="mx-auto max-w-6xl px-4 py-8 sm:px-6">{children}</main>
        <footer className="border-t border-slate-200 bg-white py-8 text-center text-sm text-slate-500">
          © {new Date().getFullYear()} AI Safety Research · 全球 AI 治理监测平台
        </footer>
      </body>
    </html>
  );
}
