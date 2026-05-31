import Hero from "@/components/Hero";
import MonitoringCenter from "@/components/monitoring/MonitoringCenter";
import { SYSTEM_NAMES } from "@/lib/system-names";
import NewsCard from "@/components/NewsCard";
import SystemCard from "@/components/SystemCard";
import { getKeywords, getLatestIncidents, getMonitoringOverview, getStats, getSystems } from "@/lib/api";
import type { MonitoringOverview } from "@/lib/types";

const EMPTY_OVERVIEW: MonitoringOverview = {
  platform: {
    status: "unknown",
    status_label: "等待首次运行",
    online_subsystems: 0,
    total_subsystems: 3,
    today_run_count: 0,
    today_new_data: 0,
    last_run_ago: "—",
    next_scheduled_ago: "—",
  },
  subsystems: [],
  timeline: [],
};

export default async function HomePage() {
  let stats = { total_incidents: 0, total_tags: 0, taxonomy_kinds: 0, keyword_nodes: 0 };
  let systems: Awaited<ReturnType<typeof getSystems>> = [];
  let latest: Awaited<ReturnType<typeof getLatestIncidents>> = [];
  let keywords: Awaited<ReturnType<typeof getKeywords>> = [];
  let overview: MonitoringOverview = EMPTY_OVERVIEW;
  let apiError = "";

  try {
    [stats, systems, latest, keywords, overview] = await Promise.all([
      getStats(),
      getSystems(),
      getLatestIncidents(12),
      getKeywords(16),
      getMonitoringOverview(),
    ]);
  } catch (e) {
    apiError = e instanceof Error ? e.message : "API 连接失败";
  }

  return (
    <div className="space-y-12">
      <Hero stats={stats} />

      {apiError && (
        <div className="rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
          后端 API 请求失败（{apiError}）。
          {apiError.includes("500") ? (
            <span>
              {" "}
              请查看 API 终端日志或访问{" "}
              <code className="mx-1 rounded bg-amber-100 px-1">/docs</code> 排查。
            </span>
          ) : (
            <span>
              {" "}
              请先启动：
              <code className="mx-1 rounded bg-amber-100 px-1">
                uvicorn api.main:app --reload --host 127.0.0.1 --port 8000
              </code>
            </span>
          )}
        </div>
      )}

      <MonitoringCenter overview={overview} />

      <section>
        <div className="mb-6 flex items-end justify-between">
          <div>
            <h2 className="text-2xl font-bold text-slate-900">三大监测系统</h2>
            <p className="mt-1 text-slate-600">
              {SYSTEM_NAMES.policy} · {SYSTEM_NAMES.meeting} · {SYSTEM_NAMES.literature} 并行运行
            </p>
          </div>
        </div>
        <div className="grid gap-5 md:grid-cols-3">
          {systems.map((s) => (
            <SystemCard key={s.key} system={s} />
          ))}
        </div>
      </section>

      <section>
        <h2 className="mb-6 text-2xl font-bold text-slate-900">最新 AI 治理动态</h2>
        {latest.length === 0 ? (
          <p className="rounded-xl border border-dashed border-slate-300 bg-white p-8 text-center text-slate-500">
            暂无数据，请确认 MySQL 已接入并在后台触发同步。
          </p>
        ) : (
          <div className="grid gap-4 sm:grid-cols-2">
            {latest.map((item, i) => (
              <NewsCard key={`${item.title}-${i}`} item={item} />
            ))}
          </div>
        )}
      </section>

      {keywords.length > 0 && (
        <section className="rounded-2xl border border-slate-200 bg-white p-6 shadow-card">
          <h2 className="text-lg font-bold text-slate-900">热门监测主题</h2>
          <div className="mt-4 flex flex-wrap gap-2">
            {keywords.map((kw) => (
              <span
                key={kw.keyword}
                className="rounded-full bg-slate-100 px-3 py-1.5 text-sm text-slate-700"
              >
                {kw.keyword}
                <span className="ml-1 text-slate-400">×{kw.count}</span>
              </span>
            ))}
          </div>
        </section>
      )}
    </div>
  );
}
