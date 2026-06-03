/**
 * 功能：政策按国家/区域分布横向柱状图。
 * 输入：PolicyCountItem[]。
 * 输出：Recharts BarChart。
 */

"use client";

import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { PolicyCountItem } from "@/lib/types";

const COLORS = {
  sovereign: "#2563eb",
  region: "#7c3aed",
};

interface PolicyCountryChartProps {
  data: PolicyCountItem[];
}

export default function PolicyCountryChart({ data }: PolicyCountryChartProps) {
  if (!data.length) {
    return (
      <p className="py-12 text-center text-sm text-slate-500">暂无国家分布数据</p>
    );
  }

  const chartData = [...data].sort((a, b) => b.count - a.count).slice(0, 12);
  const total = chartData.reduce((s, d) => s + d.count, 0);

  return (
    <ResponsiveContainer width="100%" height={Math.max(220, chartData.length * 36)}>
      <BarChart
        data={chartData}
        layout="vertical"
        margin={{ top: 4, right: 16, left: 8, bottom: 4 }}
      >
        <CartesianGrid strokeDasharray="3 3" horizontal={false} />
        <XAxis type="number" allowDecimals={false} />
        <YAxis
          type="category"
          dataKey="label"
          width={72}
          tick={{ fontSize: 12 }}
        />
        <Tooltip
          formatter={(value) => {
            const n = Number(value ?? 0);
            return [
              `${n} 条 (${total ? Math.round((n / total) * 100) : 0}%)`,
              "政策数",
            ];
          }}
        />
        <Bar dataKey="count" radius={[0, 4, 4, 0]}>
          {chartData.map((entry) => (
            <Cell
              key={entry.label}
              fill={entry.kind === "region" ? COLORS.region : COLORS.sovereign}
            />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}
