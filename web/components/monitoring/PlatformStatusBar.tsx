/**
 * 功能：平台全局运行状态条（运行监控中心顶部总控视角）。
 * 输入：platform 聚合字段（来自 GET /api/monitoring/overview）。
 * 输出：静态展示区块。
 * 上下游：MonitoringCenter → 本组件；数据 api/services/monitoring_data。
 */

import type { PlatformStatus } from "@/lib/types";

const STATUS_DOT: Record<string, string> = {
  healthy: "bg-emerald-400",
  degraded: "bg-amber-400",
  stale: "bg-red-400",
  unknown: "bg-slate-400",
};

export default function PlatformStatusBar({ platform }: { platform: PlatformStatus }) {
  const dot = STATUS_DOT[platform.status] || STATUS_DOT.unknown;

  return (
    <section className="rounded-2xl border border-slate-200 bg-white p-6 shadow-card">
      <div className="flex flex-wrap items-center gap-3">
        <span className={`inline-block h-2.5 w-2.5 rounded-full ${dot} animate-pulse`} />
        <h2 className="text-lg font-bold text-slate-900">平台运行状态</h2>
        <span className="rounded-full bg-emerald-50 px-3 py-1 text-sm font-medium text-emerald-700">
          {platform.status_label}
        </span>
      </div>

      <div className="mt-5 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {[
          { label: "当前运行状态", value: platform.status_label },
          {
            label: "在线子系统",
            value: `${platform.online_subsystems}/${platform.total_subsystems}`,
          },
          { label: "今日任务运行次数", value: platform.today_run_count.toLocaleString() },
          { label: "今日新增数据", value: platform.today_new_data.toLocaleString() },
          { label: "最近运行时间", value: platform.last_run_ago },
          { label: "下次计划运行", value: platform.next_scheduled_ago },
        ].map((item) => (
          <div
            key={item.label}
            className="rounded-xl border border-slate-100 bg-slate-50/80 px-4 py-3"
          >
            <dt className="text-xs text-slate-500">{item.label}</dt>
            <dd className="mt-1 text-base font-semibold text-slate-900">{item.value}</dd>
          </div>
        ))}
      </div>
    </section>
  );
}
