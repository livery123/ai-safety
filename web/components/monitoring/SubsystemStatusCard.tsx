/**
 * 功能：单个子系统运行状态卡片（三系统统一模板）。
 * 输入：SubsystemStatus + 品牌色。
 * 输出：可点击「查看详情」的卡片。
 * 上下游：SubsystemStatusGrid 渲染三次；名称与颜色来自 system-names.ts。
 */

import Link from "next/link";
import type { SubsystemStatus } from "@/lib/types";

const STATUS_STYLE: Record<string, { badge: string; dot: string }> = {
  healthy: { badge: "bg-emerald-50 text-emerald-700", dot: "bg-emerald-400" },
  degraded: { badge: "bg-amber-50 text-amber-700", dot: "bg-amber-400" },
  stale: { badge: "bg-red-50 text-red-700", dot: "bg-red-400" },
  unknown: { badge: "bg-slate-100 text-slate-600", dot: "bg-slate-400" },
};

interface Props {
  subsystem: SubsystemStatus;
  color: string;
}

export default function SubsystemStatusCard({ subsystem, color }: Props) {
  const style = STATUS_STYLE[subsystem.status] || STATUS_STYLE.unknown;
  const lastRun = subsystem.last_run_at
    ? new Date(subsystem.last_run_at).toLocaleTimeString("zh-CN", {
        hour: "2-digit",
        minute: "2-digit",
        hour12: false,
      })
    : "—";

  const rows: { label: string; value: string }[] = [
    { label: "上次运行", value: lastRun !== "—" ? lastRun : subsystem.last_run_ago },
    { label: "今日新增", value: subsystem.today_new.toLocaleString() },
    { label: subsystem.source_label, value: subsystem.source_count.toLocaleString() },
    { label: "数据总量", value: subsystem.total.toLocaleString() },
    { label: "运行状态", value: subsystem.status_label },
  ];

  if (subsystem.highlight_count != null && subsystem.highlight_label) {
    rows.splice(3, 0, {
      label: subsystem.highlight_label,
      value: subsystem.highlight_count.toLocaleString(),
    });
  }

  return (
    <article
      className="flex flex-col rounded-2xl border border-slate-200 bg-white p-5 shadow-card"
      style={{ borderTopWidth: 4, borderTopColor: color }}
    >
      <div className="mb-4 flex items-start justify-between gap-2">
        <div>
          <div className="flex items-center gap-2">
            <span className={`h-2 w-2 rounded-full ${style.dot}`} />
            <h3 className="text-base font-bold leading-snug text-slate-900">{subsystem.name}</h3>
          </div>
        </div>
        <span className={`shrink-0 rounded-full px-2.5 py-0.5 text-xs font-medium ${style.badge}`}>
          {subsystem.status === "healthy" ? "运行正常" : subsystem.status_label}
        </span>
      </div>

      <dl className="flex-1 space-y-2.5">
        {rows.map((row) => (
          <div key={row.label} className="flex items-center justify-between text-sm">
            <dt className="text-slate-500">{row.label}</dt>
            <dd className="font-semibold text-slate-800">{row.value}</dd>
          </div>
        ))}
      </dl>

      <Link
        href={subsystem.detail_href}
        className="mt-5 block rounded-xl border border-slate-200 py-2.5 text-center text-sm font-semibold text-brand-600 transition hover:border-brand-200 hover:bg-brand-50"
      >
        查看详情 →
      </Link>
    </article>
  );
}
