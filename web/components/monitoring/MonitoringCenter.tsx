/**
 * 功能：首页「系统运行监控中心」区块（总控 + 三卡片 + 时间线）。
 * 输入：MonitoringOverview。
 * 输出：组合布局。
 * 上下游：app/page.tsx SSR 拉取 overview 后传入。
 */

import type { MonitoringOverview } from "@/lib/types";
import PlatformStatusBar from "./PlatformStatusBar";
import RunTimeline from "./RunTimeline";
import SubsystemStatusGrid from "./SubsystemStatusGrid";

export default function MonitoringCenter({ overview }: { overview: MonitoringOverview }) {
  return (
    <div className="space-y-8">
      <div>
        <p className="text-sm font-semibold uppercase tracking-wider text-brand-600">
          系统运行监控中心
        </p>
        <p className="mt-1 text-slate-600">
          三子系统统一调度，持续自动采集、分析与更新
        </p>
      </div>
      <PlatformStatusBar platform={overview.platform} />
      <SubsystemStatusGrid subsystems={overview.subsystems} />
      <RunTimeline items={overview.timeline} />
    </div>
  );
}
