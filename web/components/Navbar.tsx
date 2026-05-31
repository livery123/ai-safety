/**
 * 功能：全站顶部导航。
 */

import Link from "next/link";

const links = [
  { href: "/", label: "首页" },
  { href: "/policy", label: "政策监管" },
  { href: "/meetings", label: "国际会议" },
  { href: "/literature", label: "文献情报" },
  { href: "/about", label: "关于" },
];

export default function Navbar() {
  return (
    <header className="sticky top-0 z-50 border-b border-slate-200/80 bg-white/90 backdrop-blur-md">
      <div className="mx-auto flex max-w-6xl items-center justify-between px-4 py-4 sm:px-6">
        <Link href="/" className="flex items-center gap-2 font-semibold text-slate-900">
          <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-brand-600 text-lg text-white">
            🛡️
          </span>
          <span className="hidden sm:inline">全球 AI 治理监测平台</span>
        </Link>
        <nav className="flex flex-wrap items-center gap-1 sm:gap-2">
          {links.map((l) => (
            <Link
              key={l.href}
              href={l.href}
              className="rounded-lg px-3 py-2 text-sm font-medium text-slate-600 transition hover:bg-slate-100 hover:text-slate-900"
            >
              {l.label}
            </Link>
          ))}
        </nav>
      </div>
    </header>
  );
}
