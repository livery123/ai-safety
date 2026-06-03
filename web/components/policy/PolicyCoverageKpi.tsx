/**
 * 功能：政策发布地理覆盖度 KPI 四卡。
 * 输入：PolicyCoverageStats。
 * 输出：主权国/区域/国际组织/达标状态展示。
 */

import type { PolicyCoverageStats } from "@/lib/types";

interface PolicyCoverageKpiProps {
  coverage: PolicyCoverageStats;
}

export default function PolicyCoverageKpi({ coverage }: PolicyCoverageKpiProps) {
  const sovereignList =
    coverage.sovereign_names.length > 0
      ? coverage.sovereign_names.join("、")
      : "—";
  const regionList =
    coverage.region_names.length > 0 ? coverage.region_names.join("、") : "—";

  const cards = [
    {
      label: "主权国家覆盖",
      value: `${coverage.sovereign_count} 个`,
      sub: sovereignList,
      accent: coverage.sovereign_count >= 5 ? "text-emerald-700" : "text-amber-700",
    },
    {
      label: "次级区域",
      value: `${coverage.region_count} 个`,
      sub: regionList,
      accent: "text-slate-900",
    },
    {
      label: "国际组织政策",
      value: `${coverage.intl_org_doc_count} 条`,
      sub: coverage.intl_org_doc_count >= 1 ? "含联合国/OECD 等" : "暂无",
      accent: coverage.intl_org_doc_count >= 1 ? "text-emerald-700" : "text-amber-700",
    },
    {
      label: "指标达标",
      value: coverage.meets_kpi ? "已达标" : "未达标",
      sub: coverage.meets_kpi
        ? "≥5 国 + 国际组织"
        : `差 ${Math.max(0, 5 - coverage.sovereign_count)} 国${
            coverage.intl_org_doc_count < 1 ? "；缺国际组织" : ""
          }`,
      accent: coverage.meets_kpi ? "text-emerald-700" : "text-amber-700",
    },
  ];

  return (
    <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
      {cards.map((c) => (
        <div
          key={c.label}
          className="rounded-xl border border-slate-200 bg-white px-4 py-3 shadow-sm"
        >
          <p className="text-xs text-slate-500">{c.label}</p>
          <p className={`mt-1 text-lg font-bold ${c.accent}`}>{c.value}</p>
          <p className="mt-1 line-clamp-2 text-xs text-slate-600">{c.sub}</p>
        </div>
      ))}
    </div>
  );
}
