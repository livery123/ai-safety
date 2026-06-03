/**
 * 功能：政策入库周趋势面积图。
 * 输入：KeywordItem[]（week_bucket / count）。
 * 输出：Recharts AreaChart。
 */

"use client";

import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { KeywordItem } from "@/lib/types";

interface PolicyTrendChartProps {
  data: KeywordItem[];
}

export default function PolicyTrendChart({ data }: PolicyTrendChartProps) {
  if (!data.length) {
    return (
      <p className="py-12 text-center text-sm text-slate-500">暂无趋势数据</p>
    );
  }

  const chartData = data.map((d) => ({
    week: d.keyword,
    count: d.count,
  }));

  return (
    <ResponsiveContainer width="100%" height={220}>
      <AreaChart data={chartData} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
        <defs>
          <linearGradient id="policyTrendFill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="5%" stopColor="#2563eb" stopOpacity={0.35} />
            <stop offset="95%" stopColor="#2563eb" stopOpacity={0} />
          </linearGradient>
        </defs>
        <CartesianGrid strokeDasharray="3 3" />
        <XAxis dataKey="week" tick={{ fontSize: 10 }} interval="preserveStartEnd" />
        <YAxis allowDecimals={false} width={32} tick={{ fontSize: 11 }} />
        <Tooltip
          formatter={(value) => [`${Number(value ?? 0)} 条`, "入库量"]}
        />
        <Area
          type="monotone"
          dataKey="count"
          stroke="#2563eb"
          fill="url(#policyTrendFill)"
          strokeWidth={2}
        />
      </AreaChart>
    </ResponsiveContainer>
  );
}
