/**
 * 功能：三子系统运行状态卡片网格。
 * 输入：subsystems 列表。
 * 输出：统一布局的三列卡片。
 * 上下游：MonitoringCenter；颜色映射 SYSTEM_COLORS。
 */

import { SYSTEM_COLORS } from "@/lib/system-names";
import type { SubsystemStatus } from "@/lib/types";
import SubsystemStatusCard from "./SubsystemStatusCard";

export default function SubsystemStatusGrid({
  subsystems,
}: {
  subsystems: SubsystemStatus[];
}) {
  return (
    <section>
      <h2 className="mb-4 text-xl font-bold text-slate-900">子系统运行状态</h2>
      <div className="grid gap-5 md:grid-cols-3">
        {subsystems.map((s) => (
          <SubsystemStatusCard
            key={s.key}
            subsystem={s}
            color={SYSTEM_COLORS[s.key as keyof typeof SYSTEM_COLORS] || "#64748b"}
          />
        ))}
      </div>
    </section>
  );
}
