/**
 * 功能：子系统列表页左右分栏布局（左：来源筛选，右：主内容）。
 * 输入：sidebar 与 main 两个 React 节点。
 * 输出：响应式 grid；小屏上下堆叠。
 * 上下游：policy/meetings/literature 列表页。
 */

import type { ReactNode } from "react";

interface TrackPageShellProps {
  sidebar: ReactNode;
  main: ReactNode;
}

export default function TrackPageShell({ sidebar, main }: TrackPageShellProps) {
  return (
    <div className="grid gap-6 lg:grid-cols-[240px_minmax(0,1fr)] lg:items-start">
      {sidebar}
      <section className="min-w-0">{main}</section>
    </div>
  );
}
