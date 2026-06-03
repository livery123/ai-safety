/**
 * 功能：会议页主区：名录 + 事件列表 Tab + 全部报道。
 * 输入：catalog、events 初始数据。
 * 输出：Tab 切换 UI。
 */

"use client";

import { useState } from "react";
import MeetingCatalogPanel from "@/components/meetings/MeetingCatalogPanel";
import MeetingEventCard from "@/components/meetings/MeetingEventCard";
import TrackList from "@/components/TrackList";
import type { MeetingCatalogItem, MeetingEventSummary } from "@/lib/types";

interface Props {
  catalog: MeetingCatalogItem[];
  events: MeetingEventSummary[];
}

export default function MeetingsHub({ catalog, events }: Props) {
  const [tab, setTab] = useState<"events" | "catalog" | "articles">("events");

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap gap-2 border-b border-slate-200 pb-2">
        {(
          [
            ["events", "会议事件"],
            ["catalog", "重大会议清单"],
            ["articles", "全部会议报道"],
          ] as const
        ).map(([key, label]) => (
          <button
            key={key}
            type="button"
            onClick={() => setTab(key)}
            className={`rounded-md px-4 py-2 text-sm font-medium ${
              tab === key
                ? "bg-violet-600 text-white"
                : "bg-slate-100 text-slate-700 hover:bg-slate-200"
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      {tab === "catalog" && <MeetingCatalogPanel catalog={catalog} />}
      {tab === "events" && (
        <section className="space-y-4">
          <h2 className="text-lg font-semibold text-slate-900">按事件浏览</h2>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {events.map((ev) => (
              <MeetingEventCard key={ev.id || ev.edition_label} event={ev} />
            ))}
          </div>
          {events.length === 0 && (
            <p className="text-sm text-slate-500">暂无事件，请先执行迁移、种子与采集。</p>
          )}
        </section>
      )}
      {tab === "articles" && <TrackList track="meetings" />}
    </div>
  );
}
