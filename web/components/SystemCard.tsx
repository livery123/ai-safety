/**
 * 功能：三系统 Hub 入口卡片。
 */

import Link from "next/link";
import type { SystemInfo } from "@/lib/types";

const hrefMap: Record<string, string> = {
  policy: "/policy",
  meeting: "/meetings",
  literature: "/literature",
};

export default function SystemCard({ system }: { system: SystemInfo }) {
  const href = hrefMap[system.key] || "/";
  return (
    <Link
      href={href}
      className="group block rounded-2xl border border-slate-200 bg-white p-6 shadow-card transition hover:-translate-y-1 hover:shadow-lg"
      style={{ borderTopWidth: 4, borderTopColor: system.color }}
    >
      <p className="text-xs font-semibold uppercase tracking-wider text-slate-500">
        {system.system_no}
      </p>
      <h3 className="mt-2 text-xl font-bold text-slate-900 group-hover:text-brand-600">
        {system.name}
      </h3>
      <p className="mt-2 min-h-[48px] text-sm leading-relaxed text-slate-600">
        {system.tagline}
      </p>
      <div className="mt-5 flex items-end justify-between">
        <div className="text-sm text-slate-500">
          本周 <strong className="text-lg text-slate-900">{system.week_new}</strong>
          <span className="mx-2">·</span>
          累计 <strong className="text-slate-800">{system.total.toLocaleString()}</strong>
        </div>
        <span className="text-sm font-semibold" style={{ color: system.color }}>
          进入 →
        </span>
      </div>
    </Link>
  );
}
