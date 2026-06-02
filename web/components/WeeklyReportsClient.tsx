/**
 * 功能：监测周报历史 archive + 详情（客户端筛选与 Markdown 渲染）。
 */

"use client";

import { useCallback, useEffect, useState } from "react";
import ReportMarkdownView from "@/components/ReportMarkdownView";
import { apiUrl } from "@/lib/api-base";
import { formatDate } from "@/lib/api";
import type { WeeklyReportDetail, WeeklyReportItem } from "@/lib/types";

const SYSTEMS = [
  { key: "", label: "全部" },
  { key: "policy", label: "政策监管" },
  { key: "meeting", label: "国际会议" },
  { key: "literature", label: "文献情报" },
  { key: "platform", label: "平台综合" },
];

interface WeeklyReportsPageProps {
  initialSystem?: string;
}

export default function WeeklyReportsClient({ initialSystem = "policy" }: WeeklyReportsPageProps) {
  const [system, setSystem] = useState(initialSystem);
  const [reportType, setReportType] = useState<"weekly" | "brief">("weekly");
  const [list, setList] = useState<WeeklyReportItem[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [detail, setDetail] = useState<WeeklyReportDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const fetchList = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const q = new URLSearchParams({ report_type: reportType, limit: "52" });
      if (system) q.set("system", system);
      const res = await fetch(apiUrl(`/api/analysis/reports/weekly?${q.toString()}`));
      if (!res.ok) throw new Error(String(res.status));
      const data: WeeklyReportItem[] = await res.json();
      setList(data);
      if (data.length > 0 && !selectedId) {
        setSelectedId(data[0].id);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "加载失败");
      setList([]);
    } finally {
      setLoading(false);
    }
  }, [system, reportType, selectedId]);

  useEffect(() => {
    fetchList();
  }, [fetchList]);

  useEffect(() => {
    if (!selectedId) {
      setDetail(null);
      return;
    }
    fetch(apiUrl(`/api/analysis/reports/weekly/${selectedId}`))
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
      .then((d: WeeklyReportDetail) => setDetail(d))
      .catch(() => setDetail(null));
  }, [selectedId]);

  const downloadMd = () => {
    if (!detail) return;
    const blob = new Blob([detail.report_markdown], { type: "text/markdown;charset=utf-8" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `report_${detail.id}_${detail.week_start}.md`;
    a.click();
    URL.revokeObjectURL(a.href);
  };

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-slate-900">监测报告 archive</h1>
        <p className="mt-2 text-slate-600">
          每周自动生成 AI 治理监测周报（政策意义 · 可能影响 · 历史关系 · 落地性），历史记录长期保留。
        </p>
      </div>

      <div className="flex flex-wrap gap-3">
        <select
          value={system}
          onChange={(e) => {
            setSystem(e.target.value);
            setSelectedId(null);
          }}
          className="rounded-xl border border-slate-300 px-3 py-2 text-sm"
        >
          {SYSTEMS.map((s) => (
            <option key={s.key || "all"} value={s.key}>
              {s.label}
            </option>
          ))}
        </select>
        <select
          value={reportType}
          onChange={(e) => {
            setReportType(e.target.value as "weekly" | "brief");
            setSelectedId(null);
          }}
          className="rounded-xl border border-slate-300 px-3 py-2 text-sm"
        >
          <option value="weekly">周报</option>
          <option value="brief">简报</option>
        </select>
      </div>

      {error && (
        <div className="rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
          API 请求失败（{error}）。请确认 FastAPI 已启动且 MySQL 可用。
        </div>
      )}

      <div className="grid gap-6 lg:grid-cols-[280px_minmax(0,1fr)]">
        <aside className="max-h-[70vh] overflow-y-auto rounded-2xl border border-slate-200 bg-white p-3 shadow-card">
          {loading ? (
            <p className="p-4 text-sm text-slate-500">加载中…</p>
          ) : list.length === 0 ? (
            <p className="p-4 text-sm text-slate-500">暂无报告。请运行 cron 或手动生成脚本。</p>
          ) : (
            <ul className="space-y-1">
              {list.map((item) => (
                <li key={item.id}>
                  <button
                    type="button"
                    onClick={() => setSelectedId(item.id)}
                    className={`w-full rounded-xl px-3 py-3 text-left text-sm transition ${
                      selectedId === item.id
                        ? "bg-brand-50 font-semibold text-brand-700"
                        : "hover:bg-slate-50"
                    }`}
                  >
                    <span className="block">{item.week_start} ～ {item.week_end}</span>
                    <span className="mt-0.5 block text-xs text-slate-500">
                      {item.system_key} · {item.article_count} 条 · {formatDate(item.created_at)}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </aside>

        <section className="min-w-0 rounded-2xl border border-slate-200 bg-white p-6 shadow-card">
          {!detail ? (
            <p className="text-slate-500">请选择左侧报告查看全文。</p>
          ) : (
            <>
              <div className="mb-6 flex flex-wrap items-center justify-between gap-3 border-b border-slate-100 pb-4">
                <div className="text-xs text-slate-500">
                  生成于 {formatDate(detail.created_at)}
                  {detail.task_id ? ` · task #${detail.task_id}` : ""}
                  {detail.trigger_source ? ` · ${detail.trigger_source}` : ""}
                  {detail.model_name ? ` · ${detail.model_name}` : ""}
                </div>
                <button
                  type="button"
                  onClick={downloadMd}
                  className="rounded-lg border border-slate-300 px-3 py-1.5 text-sm font-medium hover:bg-slate-50"
                >
                  下载 Markdown
                </button>
              </div>
              <ReportMarkdownView markdown={detail.report_markdown} />
            </>
          )}
        </section>
      </div>
    </div>
  );
}
