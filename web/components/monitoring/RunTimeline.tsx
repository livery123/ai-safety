/**
 * 功能：最近系统运行时间线（统一 log 展示）。
 * 输入：timeline 条目列表。
 * 输出：时间 + 系统标签 + 摘要。
 * 上下游：MonitoringCenter；数据来自 system_tasks 表。
 */

import { formatTime } from "@/lib/api";
import type { TimelineItem } from "@/lib/types";

const SYSTEM_BADGE: Record<string, string> = {
  policy: "bg-blue-50 text-blue-700",
  meeting: "bg-violet-50 text-violet-700",
  literature: "bg-emerald-50 text-emerald-700",
};

export default function RunTimeline({ items }: { items: TimelineItem[] }) {
  return (
    <section className="rounded-2xl border border-slate-200 bg-white p-6 shadow-card">
      <h2 className="text-xl font-bold text-slate-900">最近运行记录</h2>
      {items.length === 0 ? (
        <p className="mt-4 rounded-xl border border-dashed border-slate-200 bg-slate-50 px-4 py-8 text-center text-sm text-slate-500">
          暂无运行记录。配置定时任务后将在此展示自动监测历史。
        </p>
      ) : (
        <ul className="mt-4 divide-y divide-slate-100">
          {items.map((item, idx) => {
            const badge = SYSTEM_BADGE[item.system_key] || "bg-slate-100 text-slate-600";
            const failed = item.status === "failed";
            return (
              <li key={`${item.at}-${idx}`} className="flex flex-wrap items-center gap-3 py-3 text-sm">
                <time className="w-14 shrink-0 font-mono text-slate-500">{formatTime(item.at)}</time>
                <span className={`rounded-md px-2 py-0.5 text-xs font-medium ${badge}`}>
                  {item.system_label}
                </span>
                <span className={`flex-1 ${failed ? "text-red-600" : "text-slate-700"}`}>
                  {item.summary}
                </span>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
