/**
 * 功能：三子系统列表页左栏来源筛选（全选/单选 checkbox）。
 * 输入：track、选中 key 列表；null 表示「全部」。
 * 输出：onChange 回调。
 * 上下游：TrackList / LiteratureList；GET /api/tracks/{track}/sources。
 */

"use client";

import { useEffect, useMemo, useState } from "react";
import { apiUrl } from "@/lib/api-base";
import type { SourceFilterOption, SourceFilterResponse } from "@/lib/types";

export type SourceSelection = string[] | null;

interface SourceFilterPanelProps {
  track: "policy" | "meetings" | "literature";
  selected: SourceSelection;
  onChange: (value: SourceSelection) => void;
}

const CHECKBOX_CLASS: Record<string, string> = {
  literature: "text-emerald-600",
  policy: "text-brand-600",
  meetings: "text-violet-600",
};

export default function SourceFilterPanel({ track, selected, onChange }: SourceFilterPanelProps) {
  const [meta, setMeta] = useState<SourceFilterResponse | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    fetch(apiUrl(`/api/tracks/${track}/sources`))
      .then((res) => (res.ok ? res.json() : Promise.reject(new Error(String(res.status)))))
      .then((data: SourceFilterResponse) => {
        if (!cancelled) setMeta(data);
      })
      .catch(() => {
        if (!cancelled) setMeta(null);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [track]);

  const allKeys = useMemo(() => (meta?.options ?? []).map((o) => o.key), [meta]);
  const isAll = selected === null;
  const isLiterature = track === "literature";
  const checkboxAccent = CHECKBOX_CLASS[track] || CHECKBOX_CLASS.policy;

  const grouped = useMemo(() => {
    if (isLiterature) {
      return [["", meta?.options ?? []] as [string, SourceFilterOption[]]];
    }
    const map = new Map<string, SourceFilterOption[]>();
    for (const opt of meta?.options ?? []) {
      const g = opt.group_label || opt.group;
      if (!g) continue;
      if (!map.has(g)) map.set(g, []);
      map.get(g)!.push(opt);
    }
    return Array.from(map.entries());
  }, [meta, isLiterature]);

  const isChecked = (key: string) => isAll || (selected?.includes(key) ?? false);

  const toggleAll = () => onChange(null);

  const toggleOne = (key: string) => {
    if (isAll) {
      onChange(allKeys.filter((k) => k !== key));
      return;
    }
    const set = new Set(selected ?? []);
    if (set.has(key)) {
      set.delete(key);
    } else {
      set.add(key);
    }
    const next = Array.from(set);
    if (next.length === 0 || next.length === allKeys.length) {
      onChange(null);
    } else {
      onChange(next);
    }
  };

  const renderOption = (opt: SourceFilterOption) => (
    <li key={opt.key}>
      <label className="flex cursor-pointer items-start gap-2 text-sm text-slate-700">
        <input
          type="checkbox"
          className={`mt-0.5 h-4 w-4 shrink-0 rounded border-slate-300 ${checkboxAccent}`}
          checked={isChecked(opt.key)}
          onChange={() => toggleOne(opt.key)}
        />
        <span className="min-w-0 flex-1 leading-snug">
          <span className="font-medium text-slate-800">{opt.label}</span>
          <span className="ml-1 text-xs text-slate-400">({opt.count})</span>
          {opt.hint && (
            <span className="mt-0.5 block text-xs leading-relaxed text-slate-500">{opt.hint}</span>
          )}
        </span>
      </label>
    </li>
  );

  return (
    <aside className="rounded-2xl border border-slate-200 bg-white p-4 shadow-card lg:sticky lg:top-24 lg:max-h-[calc(100vh-7rem)] lg:overflow-y-auto">
      <h2 className="border-b border-slate-100 pb-3 text-sm font-bold text-slate-900">
        {meta?.panel_title ?? "来源筛选"}
      </h2>

      {isLiterature && meta && meta.total_count != null && meta.total_count > 0 && (
        <p className="mt-2 text-xs text-slate-500">
          已入库 <span className="font-semibold text-emerald-700">{meta.total_count}</span> 篇
        </p>
      )}

      {loading && <p className="mt-4 text-xs text-slate-500">加载来源…</p>}

      {!loading && (
        <div className="mt-3 space-y-4">
          <label className="flex cursor-pointer items-center gap-2 text-sm font-medium text-slate-800">
            <input
              type="checkbox"
              className={`h-4 w-4 rounded border-slate-300 ${checkboxAccent}`}
              checked={isAll}
              onChange={toggleAll}
            />
            全部
          </label>

          {isLiterature ? (
            <ul className="space-y-3 border-t border-slate-100 pt-3">{grouped[0]?.[1].map(renderOption)}</ul>
          ) : (
            grouped.map(([groupLabel, opts]) => (
              <div key={groupLabel}>
                <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-400">
                  {groupLabel}
                </p>
                <ul className="space-y-2">{opts.map(renderOption)}</ul>
              </div>
            ))
          )}

          {(meta?.options.length ?? 0) === 0 && (
            <p className="text-xs text-slate-500">暂无来源数据，请先运行文献同步。</p>
          )}
        </div>
      )}
    </aside>
  );
}
