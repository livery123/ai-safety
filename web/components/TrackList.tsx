/**
 * 功能：政策/会议列表（左栏来源筛选 + 搜索 + 卡片分页）。
 * 输入：track=policy|meetings。
 * 输出：完整列表页主区域布局。
 * 上下游：policy/meetings page；apiUrl → /api/tracks/*。
 */

"use client";

import { useCallback, useEffect, useState } from "react";
import NewsCard from "@/components/NewsCard";
import SourceFilterPanel, { type SourceSelection } from "@/components/SourceFilterPanel";
import TrackPageShell from "@/components/TrackPageShell";
import { apiUrl } from "@/lib/api-base";
import type { IncidentItem, PaginatedResponse } from "@/lib/types";

interface TrackListProps {
  track: "policy" | "meetings";
}

export default function TrackList({ track }: TrackListProps) {
  const [keyword, setKeyword] = useState("");
  const [page, setPage] = useState(1);
  const [selectedSources, setSelectedSources] = useState<SourceSelection>(null);
  const [data, setData] = useState<PaginatedResponse<IncidentItem> | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const fetchData = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const q = new URLSearchParams({ page: String(page), page_size: "12" });
      if (keyword.trim()) q.set("keyword", keyword.trim());
      if (selectedSources && selectedSources.length > 0) {
        q.set("sources", selectedSources.join(","));
      }
      const path = track === "policy" ? "/api/tracks/policy" : "/api/tracks/meetings";
      const res = await fetch(apiUrl(`${path}?${q.toString()}`));
      if (!res.ok) throw new Error(String(res.status));
      setData(await res.json());
    } catch (e) {
      setError(e instanceof Error ? e.message : "加载失败");
      setData(null);
    } finally {
      setLoading(false);
    }
  }, [track, page, keyword, selectedSources]);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  const onSearch = (e: React.FormEvent) => {
    e.preventDefault();
    setPage(1);
  };

  const onSourcesChange = (value: SourceSelection) => {
    setSelectedSources(value);
    setPage(1);
  };

  const main = (
    <div>
      <form onSubmit={onSearch} className="mb-6 flex gap-2">
        <input
          type="search"
          value={keyword}
          onChange={(e) => {
            setKeyword(e.target.value);
            setPage(1);
          }}
          placeholder="搜索标题或摘要…"
          className="flex-1 rounded-xl border border-slate-300 px-4 py-2.5 text-sm outline-none focus:border-brand-600 focus:ring-2 focus:ring-blue-100"
        />
        <button
          type="submit"
          className="rounded-xl bg-brand-600 px-5 py-2.5 text-sm font-semibold text-white hover:bg-brand-700"
        >
          搜索
        </button>
      </form>

      {loading && <p className="text-slate-500">加载中…</p>}
      {error && (
        <p className="rounded-lg bg-red-50 px-4 py-3 text-sm text-red-700">{error}</p>
      )}
      {!loading && data && data.items.length === 0 && (
        <p className="rounded-xl border border-dashed p-8 text-center text-slate-500">
          暂无匹配结果
        </p>
      )}
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-2">
        {data?.items.map((item, i) => (
          <NewsCard key={`${item.id ?? i}-${item.title}`} item={item} />
        ))}
      </div>
      {data && data.pages > 1 && (
        <div className="mt-8 flex items-center justify-center gap-3">
          <button
            type="button"
            disabled={page <= 1}
            onClick={() => setPage((p) => Math.max(1, p - 1))}
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
        <SourceFilterPanel track={track} selected={selectedSources} onChange={onSourcesChange} />
      }
      main={main}
    />
  );
}
