/**
 * 功能：首页 Hero 区。
 */

import Link from "next/link";
import type { StatsResponse } from "@/lib/types";

interface HeroProps {
  stats: StatsResponse;
}

export default function Hero({ stats }: HeroProps) {
  return (
    <section className="relative overflow-hidden rounded-3xl bg-gradient-to-br from-slate-900 via-brand-700 to-indigo-800 px-6 py-16 text-white shadow-card sm:px-12 sm:py-20">
      <div className="absolute -right-20 -top-20 h-64 w-64 rounded-full bg-white/10 blur-3xl" />
      <div className="absolute -bottom-16 -left-16 h-48 w-48 rounded-full bg-cyan-400/20 blur-3xl" />
      <div className="relative max-w-3xl">
        <p className="mb-3 text-sm font-medium uppercase tracking-widest text-blue-200">
          AI Governance Observatory
        </p>
        <h1 className="text-3xl font-bold leading-tight sm:text-5xl">
          全球 AI 安全与治理
          <br />
          动态智能感知平台
        </h1>
        <p className="mt-5 max-w-2xl text-base leading-relaxed text-blue-100 sm:text-lg">
          自动追踪监管政策、国际会议与学术文献，基于三元风险模型结构化分类，
          为研究者、政策制定者与公众提供可浏览、可检索的 AI 治理情报门户。
        </p>
        <div className="mt-8 flex flex-wrap gap-3">
          <Link
            href="/policy"
            className="rounded-xl bg-white px-5 py-3 text-sm font-semibold text-brand-700 shadow transition hover:bg-blue-50"
          >
            浏览政策监管
          </Link>
          <Link
            href="/literature"
            className="rounded-xl border border-white/30 px-5 py-3 text-sm font-semibold text-white transition hover:bg-white/10"
          >
            探索文献情报
          </Link>
        </div>
        <dl className="mt-10 grid grid-cols-2 gap-4 sm:grid-cols-4">
          {[
            { label: "监测情报", value: stats.total_incidents },
            { label: "关键词", value: stats.total_tags },
            { label: "风险子域", value: stats.taxonomy_kinds },
            { label: "词库节点", value: stats.keyword_nodes },
          ].map((item) => (
            <div key={item.label} className="rounded-2xl bg-white/10 px-4 py-3 backdrop-blur">
              <dt className="text-xs text-blue-200">{item.label}</dt>
              <dd className="mt-1 text-2xl font-bold">{item.value.toLocaleString()}</dd>
            </div>
          ))}
        </dl>
      </div>
    </section>
  );
}
