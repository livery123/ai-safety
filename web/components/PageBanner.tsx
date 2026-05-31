/**
 * 功能：子系统页顶栏 Banner。
 */

import type { WeeklySummary } from "@/lib/types";

interface PageBannerProps {
  systemNo: string;
  title: string;
  tagline: string;
  color: string;
  summary?: WeeklySummary;
}

export default function PageBanner({
  systemNo,
  title,
  tagline,
  color,
  summary,
}: PageBannerProps) {
  return (
    <section
      className="rounded-3xl border border-slate-200 bg-white p-6 shadow-card sm:p-8"
      style={{ borderLeftWidth: 6, borderLeftColor: color }}
    >
      <p className="text-xs font-semibold uppercase tracking-wider text-slate-500">
        {systemNo}
      </p>
      <h1 className="mt-2 text-2xl font-bold text-slate-900 sm:text-3xl">{title}</h1>
      <p className="mt-2 max-w-3xl text-slate-600">{tagline}</p>
      {summary && (
        <div className="mt-6 grid grid-cols-2 gap-3 sm:grid-cols-4">
          {[
            { label: "本周新增", value: summary.week_new },
            { label: "系统累计", value: summary.total },
            { label: "主要来源", value: summary.top_source },
            { label: "活跃子域", value: summary.top_subdomain },
          ].map((m) => (
            <div key={m.label} className="rounded-xl bg-slate-50 px-4 py-3">
              <p className="text-xs text-slate-500">{m.label}</p>
              <p className="mt-1 truncate text-sm font-semibold text-slate-900">
                {typeof m.value === "number" ? m.value.toLocaleString() : m.value}
              </p>
            </div>
          ))}
        </div>
      )}
      {summary && summary.bullets.length > 0 && (
        <ul className="mt-4 list-disc space-y-1 pl-5 text-sm text-slate-600">
          {summary.bullets.slice(0, 4).map((b) => (
            <li key={b}>{b}</li>
          ))}
        </ul>
      )}
    </section>
  );
}
