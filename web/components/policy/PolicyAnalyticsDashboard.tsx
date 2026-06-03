/**
 * 功能：政策页可视化分析容器（KPI + 国家图 + 趋势 + 词云）。
 * 输入：无；客户端拉取 /api/stats/policy/analytics。
 * 输出：完整可视化区块。
 * 上下游：web/app/policy/page.tsx。
 */

"use client";

import { useCallback, useEffect, useState } from "react";
import { apiUrl } from "@/lib/api-base";
import type { PolicyAnalyticsResponse } from "@/lib/types";
import PolicyCountryChart from "./PolicyCountryChart";
import PolicyCoverageKpi from "./PolicyCoverageKpi";
import PolicyTrendChart from "./PolicyTrendChart";
import PolicyWordCloud from "./PolicyWordCloud";

export default function PolicyAnalyticsDashboard() {
  const [data, setData] = useState<PolicyAnalyticsResponse | null>(null);
  const [wordField, setWordField] = useState("mixed");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = useCallback(async (field: string) => {
    setLoading(true);
    setError("");
    try {
      const q = new URLSearchParams({
        country_limit: "12",
        word_limit: "40",
        word_field: field,
        week_limit: "16",
      });
      const res = await fetch(apiUrl(`/api/stats/policy/analytics?${q}`));
      if (!res.ok) throw new Error(String(res.status));
      setData(await res.json());
    } catch (e) {
      setError(e instanceof Error ? e.message : "加载失败");
      setData(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load(wordField);
  }, [load, wordField]);

  const onWordFieldChange = (field: string) => {
    setWordField(field);
  };

  return (
    <section className="rounded-3xl border border-slate-200 bg-white p-6 shadow-card sm:p-8">
      <div className="mb-6 flex flex-wrap items-end justify-between gap-2">
        <div>
          <h2 className="text-xl font-bold text-slate-900">可视化分析</h2>
          <p className="mt-1 text-sm text-slate-500">
            全球政策覆盖度、国家分布、入库趋势与关键词云
          </p>
        </div>
      </div>

      {loading && <p className="text-sm text-slate-500">加载分析数据…</p>}
      {error && (
        <p className="rounded-lg bg-red-50 px-4 py-3 text-sm text-red-700">{error}</p>
      )}

      {data && !loading && (
        <div className="space-y-8">
          <PolicyCoverageKpi coverage={data.coverage} />

          <div className="grid gap-6 lg:grid-cols-2">
            <div className="rounded-2xl border border-slate-100 bg-slate-50/30 p-4">
              <h3 className="mb-3 text-sm font-semibold text-slate-800">国家/地区分布</h3>
              <PolicyCountryChart data={data.by_country} />
              <p className="mt-2 text-[11px] text-slate-400">
                蓝色=主权国家 · 紫色=区域（如欧盟）
              </p>
            </div>
            <div className="rounded-2xl border border-slate-100 bg-slate-50/30 p-4">
              <h3 className="mb-3 text-sm font-semibold text-slate-800">入库周趋势</h3>
              <PolicyTrendChart data={data.by_week} />
            </div>
          </div>

          <div className="rounded-2xl border border-slate-100 bg-slate-50/30 p-4">
            <h3 className="mb-3 text-sm font-semibold text-slate-800">政策词云</h3>
            <PolicyWordCloud words={data.wordcloud} onFieldChange={onWordFieldChange} />
          </div>
        </div>
      )}
    </section>
  );
}
