/**
 * 功能：文献列表（左栏来源筛选 + 搜索 + 卡片分页）。
 * 输入：无；track 固定 literature。
 * 输出：arXiv / Scopus / Springer 筛选与列表。
 * 上下游：literature page；apiUrl → /api/tracks/literature。
 */

"use client";

import { useCallback, useEffect, useState } from "react";
import LiteratureCard from "@/components/LiteratureCard";
import SourceFilterPanel, { type SourceSelection } from "@/components/SourceFilterPanel";
import TrackPageShell from "@/components/TrackPageShell";
import { apiUrl } from "@/lib/api-base";
import type { LiteratureItem, PaginatedResponse } from "@/lib/types";

export default function LiteratureList() {
  const [keyword, setKeyword] = useState("");
  const [page, setPage] = useState(1);
  const [selectedSources, setSelectedSources] = useState<SourceSelection>(null);
  const [data, setData] = useState<PaginatedResponse<LiteratureItem> | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const fetchData = useCallback(async () => {
    setLoading(true);
    setError("");
    if (selectedSources?.length === 0) {
      setData({ items: [], total: 0, page: 1, page_size: 12, pages: 0 });
      setLoading(false);
      return;
    }
    try {
      const q = new URLSearchParams({ page: String(page), page_size: "12" });
      if (keyword.trim()) q.set("keyword", keyword.trim());
      if (selectedSources && selectedSources.length > 0) {
        q.set("sources", selectedSources.join(","));
      }
      const res = await fetch(apiUrl(`/api/tracks/literature?${q.toString()}`));
      if (!res.ok) throw new Error(String(res.status));
      setData(await res.json());
    } catch (e) {
      setError(e instanceof Error ? e.message : "加载失败");
      setData(null);
    } finally {
      setLoading(false);
    }
  }, [page, keyword, selectedSources]);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  const onSourcesChange = (value: SourceSelection) => {
    setSelectedSources(value);
    setPage(1);
  };

  const main = (
    <div>
      <input
        type="search"
        value={keyword}
        onChange={(e) => {
          setKeyword(e.target.value);
          setPage(1);
        }}
        placeholder="搜索标题或摘要…"
        className="mb-6 w-full rounded-xl border border-slate-300 px-4 py-2.5 text-sm outline-none focus:border-emerald-600"
      />
      {loading && <p className="text-slate-500">加载中…</p>}
      {error && (
        <p className="rounded-lg bg-red-50 px-4 py-3 text-sm text-red-700">
          加载失败（{error}），请确认 API 已启动。
        </p>
      )}
      {!loading && selectedSources?.length === 0 && (
        <p className="rounded-xl border border-dashed border-amber-200 bg-amber-50/50 p-8 text-center text-sm text-amber-800">
          请至少选择一个来源
        </p>
      )}
      {!loading &&
        selectedSources?.length !== 0 &&
        data &&
        data.items.length === 0 && (
          <p className="rounded-xl border border-dashed p-8 text-center text-slate-500">
            暂无匹配结果
          </p>
        )}
      <div className="grid gap-4 sm:grid-cols-2">
        {data?.items.map((item, i) => (
          <LiteratureCard key={`${item.title}-${i}`} item={item} />
        ))}
      </div>
      {data && data.pages > 1 && (
        <div className="mt-8 flex justify-center gap-3">
          <button
            type="button"
            disabled={page <= 1}
            onClick={() => setPage((p) => p - 1)}
            className="rounded-lg border px-4 py-2 text-sm disabled:opacity-40"
          >
            上一页
          </button>
          <span className="text-sm text-slate-600">
            {page} / {data.pages}（共 {data.total} 条）
          </span>
          <button
            type="button"
            disabled={page >= data.pages}
            onClick={() => setPage((p) => p + 1)}
            className="rounded-lg border px-4 py-2 text-sm disabled:opacity-40"
          >
            下一页
          </button>
        </div>
      )}
    </div>
  );

  return (
    <TrackPageShell
      sidebar={
        <SourceFilterPanel
          track="literature"
          selected={selectedSources}
          onChange={onSourcesChange}
        />
      }
      main={main}
    />
  );
}
