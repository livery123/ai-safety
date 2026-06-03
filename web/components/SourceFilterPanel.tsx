/**
 * 功能：三子系统列表页左栏来源筛选（顶部工具栏 + 分组级全选 + 单项 checkbox）。
 * 输入：track、selected；null=全选，[]=全不选，string[]=部分选。
 * 输出：onChange 回调。
 * 上下游：TrackList / LiteratureList；GET /api/tracks/{track}/sources。
 */

"use client";

import { useEffect, useMemo, useRef, useState } from "react";
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

const LINK_HOVER_CLASS: Record<string, string> = {
  literature: "hover:text-emerald-700",
  policy: "hover:text-brand-600",
  meetings: "hover:text-violet-600",
};

/** 将 null 展开为全选 key 列表，便于集合运算。 */
function effectiveKeys(selected: SourceSelection, allKeys: string[]): string[] {
  if (selected === null) return allKeys;
  return selected;
}

/** 选满则归一为 null，空则 []，否则保持数组。 */
function normalizeSelection(keys: string[], allKeys: string[]): SourceSelection {
  if (keys.length === 0) return [];
  if (keys.length >= allKeys.length && allKeys.every((k) => keys.includes(k))) return null;
  return keys;
}

export default function SourceFilterPanel({ track, selected, onChange }: SourceFilterPanelProps) {
  const [meta, setMeta] = useState<SourceFilterResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const allCheckboxRef = useRef<HTMLInputElement>(null);

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
  const totalCount = allKeys.length;
  const isAll = selected === null;
  const isNone = selected !== null && selected.length === 0;
  const selectedCount = isAll ? totalCount : (selected?.length ?? 0);
  const isPartial =
    selected !== null && selected.length > 0 && selected.length < totalCount;
  const isLiterature = track === "literature";
  const checkboxAccent = CHECKBOX_CLASS[track] || CHECKBOX_CLASS.policy;
  const linkHover = LINK_HOVER_CLASS[track] || LINK_HOVER_CLASS.policy;

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

  useEffect(() => {
    if (allCheckboxRef.current) {
      allCheckboxRef.current.indeterminate = isPartial;
    }
  }, [isPartial]);

  const isChecked = (key: string) => {
    if (isNone) return false;
    return isAll || (selected?.includes(key) ?? false);
  };

  const selectAll = () => onChange(null);

  const deselectAll = () => onChange([]);

  const toggleMaster = () => {
    if (isAll) deselectAll();
    else selectAll();
  };

  const toggleOne = (key: string) => {
    if (isAll) {
      onChange(normalizeSelection(allKeys.filter((k) => k !== key), allKeys));
      return;
    }
    const set = new Set(selected ?? []);
    if (set.has(key)) {
      set.delete(key);
    } else {
      set.add(key);
    }
    onChange(normalizeSelection(Array.from(set), allKeys));
  };

  const isGroupAllSelected = (groupKeys: string[]) => {
    if (groupKeys.length === 0) return false;
    const eff = effectiveKeys(selected, allKeys);
    return groupKeys.every((k) => eff.includes(k));
  };

  const toggleGroup = (groupKeys: string[]) => {
    const current = new Set(effectiveKeys(selected, allKeys));
    const allInGroup = isGroupAllSelected(groupKeys);
    if (allInGroup) {
      groupKeys.forEach((k) => current.delete(k));
    } else {
      groupKeys.forEach((k) => current.add(k));
    }
    onChange(normalizeSelection(Array.from(current), allKeys));
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

  const toolbarLinkClass = `text-xs font-medium text-slate-500 ${linkHover} disabled:cursor-not-allowed disabled:opacity-40`;

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
          <div className="space-y-2 border-b border-slate-100 pb-3">
            <div className="flex items-center justify-between gap-2">
              <label className="flex cursor-pointer items-center gap-2 text-sm font-medium text-slate-800">
                <input
                  ref={allCheckboxRef}
                  type="checkbox"
                  className={`h-4 w-4 rounded border-slate-300 ${checkboxAccent}`}
                  checked={isAll}
                  onChange={toggleMaster}
                />
                全部
              </label>
              <div className="flex shrink-0 items-center gap-1.5 text-xs">
                <button
                  type="button"
                  className={toolbarLinkClass}
                  disabled={isAll || totalCount === 0}
                  onClick={selectAll}
                >
                  全选
                </button>
                <span className="text-slate-300">|</span>
                <button
                  type="button"
                  className={toolbarLinkClass}
                  disabled={isNone || totalCount === 0}
                  onClick={deselectAll}
                >
                  取消全选
                </button>
              </div>
            </div>
            {totalCount > 0 && (
              <p className={`text-xs ${isNone ? "text-amber-600" : "text-slate-500"}`}>
                {isNone ? "未选择任何来源" : `已选 ${selectedCount} / ${totalCount} 项`}
              </p>
            )}
          </div>

          {isLiterature ? (
            <ul className="space-y-3">{grouped[0]?.[1].map(renderOption)}</ul>
          ) : (
            grouped.map(([groupLabel, opts]) => {
              const groupKeys = opts.map((o) => o.key);
              const showGroupToggle = groupKeys.length >= 2;
              const groupAll = isGroupAllSelected(groupKeys);
              return (
                <div key={groupLabel}>
                  <div className="mb-2 flex items-center justify-between gap-2">
                    <p className="text-xs font-semibold uppercase tracking-wide text-slate-400">
                      {groupLabel}
                    </p>
                    {showGroupToggle && (
                      <button
                        type="button"
                        className={`shrink-0 text-xs font-medium text-slate-500 ${linkHover}`}
                        onClick={() => toggleGroup(groupKeys)}
                      >
                        {groupAll ? "取消本组" : "本组全选"}
                      </button>
                    )}
                  </div>
                  <ul className="space-y-2">{opts.map(renderOption)}</ul>
                </div>
              );
            })
          )}

          {(meta?.options.length ?? 0) === 0 && (
            <p className="text-xs text-slate-500">暂无来源数据，请先运行文献同步。</p>
          )}
        </div>
      )}
    </aside>
  );
}
