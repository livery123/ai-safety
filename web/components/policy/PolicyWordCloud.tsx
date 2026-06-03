/**
 * 功能：政策词云（CSS 权重标签云，避免 react-wordcloud 与 React 18 冲突）。
 * 输入：PolicyWordItem[]、可选 field 筛选。
 * 输出：字体大小随词频变化的 tag 云。
 */

"use client";

import { useMemo, useState } from "react";
import type { PolicyWordItem } from "@/lib/types";

const FIELD_TABS = [
  { id: "mixed", label: "综合" },
  { id: "authority", label: "发布机关" },
  { id: "intl", label: "国际组织" },
  { id: "tags", label: "标签" },
] as const;

type FieldTab = (typeof FIELD_TABS)[number]["id"];

const CAT_COLOR: Record<string, string> = {
  authority: "text-blue-700 bg-blue-50 border-blue-100",
  intl_org: "text-violet-700 bg-violet-50 border-violet-100",
  tag: "text-slate-700 bg-slate-50 border-slate-200",
};

interface PolicyWordCloudProps {
  words: PolicyWordItem[];
  onFieldChange?: (field: string) => void;
}

function filterWords(words: PolicyWordItem[], field: FieldTab): PolicyWordItem[] {
  if (field === "mixed") return words;
  if (field === "authority") {
    return words.filter((w) => w.category === "authority");
  }
  if (field === "intl") {
    return words.filter((w) => w.category === "intl_org");
  }
  return words.filter((w) => w.category === "tag");
}

export default function PolicyWordCloud({ words, onFieldChange }: PolicyWordCloudProps) {
  const [field, setField] = useState<FieldTab>("mixed");

  const visible = useMemo(() => filterWords(words, field), [words, field]);
  const maxVal = useMemo(
    () => Math.max(1, ...visible.map((w) => w.value)),
    [visible]
  );

  const fontSize = (v: number) => {
    const ratio = v / maxVal;
    return `${Math.round(12 + ratio * 18)}px`;
  };

  return (
    <div>
      <div className="mb-3 flex flex-wrap gap-2">
        {FIELD_TABS.map((tab) => (
          <button
            key={tab.id}
            type="button"
            onClick={() => {
              setField(tab.id);
              onFieldChange?.(tab.id);
            }}
            className={`rounded-full px-3 py-1 text-xs font-medium transition ${
              field === tab.id
                ? "bg-brand-600 text-white"
                : "bg-slate-100 text-slate-600 hover:bg-slate-200"
            }`}
          >
            {tab.label}
          </button>
        ))}
      </div>
      {!visible.length ? (
        <p className="py-10 text-center text-sm text-slate-500">暂无词条</p>
      ) : (
        <div className="flex min-h-[180px] flex-wrap items-center justify-center gap-2 rounded-xl border border-dashed border-slate-200 bg-slate-50/50 p-4">
          {visible.map((w) => (
            <span
              key={`${w.text}-${w.category}`}
              className={`rounded-lg border px-2 py-0.5 font-medium leading-snug ${
                CAT_COLOR[w.category] || CAT_COLOR.tag
              }`}
              style={{ fontSize: fontSize(w.value) }}
              title={`${w.text} (${w.value})`}
            >
              {w.text}
            </span>
          ))}
        </div>
      )}
      <p className="mt-2 text-[11px] text-slate-400">
        蓝=发布机关 · 紫=国际组织 · 灰=标签；字号越大出现频次越高
      </p>
    </div>
  );
}
