/**
 * 功能：政策页顶栏下展示最新 AI 监测简报 excerpt。
 */

import Link from "next/link";
import type { WeeklyReportItem } from "@/lib/types";

interface AnalysisBriefCardProps {
  brief?: WeeklyReportItem | null;
  systemKey: string;
}

export default function AnalysisBriefCard({ brief, systemKey }: AnalysisBriefCardProps) {
  if (!brief) return null;

  return (
    <section className="rounded-2xl border border-blue-100 bg-gradient-to-br from-blue-50/80 to-white p-6 shadow-card">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-xs font-semibold uppercase tracking-wider text-brand-600">
            AI 监测简报
          </p>
          <h2 className="mt-1 text-lg font-bold text-slate-900">{brief.title || "本周简报"}</h2>
          <p className="mt-1 text-sm text-slate-500">
            {brief.week_start} ～ {brief.week_end} · 纳入 {brief.article_count} 条
          </p>
        </div>
        <Link
          href={`/reports/weekly?system=${systemKey}`}
          className="rounded-lg bg-brand-600 px-4 py-2 text-sm font-semibold text-white hover:bg-brand-700"
        >
          查看历史周报 →
        </Link>
      </div>
      {brief.excerpt && (
        <p className="mt-4 line-clamp-4 text-sm leading-relaxed text-slate-600">{brief.excerpt}…</p>
      )}
    </section>
  );
}
