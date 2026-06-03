/**
 * 功能：单届会议事件卡片，链至详情页。
 * 输入：MeetingEventSummary。
 * 输出：可点击卡片 UI。
 */

import Link from "next/link";
import { formatDate } from "@/lib/api";
import type { MeetingEventSummary } from "@/lib/types";

interface Props {
  event: MeetingEventSummary;
}

export default function MeetingEventCard({ event }: Props) {
  if (!event.id) {
    return (
      <div className="rounded-lg border border-dashed border-slate-300 bg-slate-50 p-4 text-sm text-slate-600">
        <p className="font-medium text-slate-800">{event.edition_label}</p>
        <p className="mt-1">
          {event.location} · {event.host}
        </p>
        <p className="mt-2 text-xs text-slate-500">待采集入库后生成事件</p>
      </div>
    );
  }

  const range =
    event.start_date || event.end_date
      ? `${formatDate(event.start_date)} — ${formatDate(event.end_date)}`
      : "日期待补全";

  return (
    <Link
      href={`/meetings/${event.id}`}
      className="block rounded-lg border border-slate-200 bg-white p-4 shadow-sm transition hover:border-violet-400 hover:shadow-md"
    >
      <div className="flex items-start justify-between gap-2">
        <h3 className="font-semibold text-slate-900">{event.edition_label}</h3>
        {event.has_analysis && (
          <span className="shrink-0 rounded bg-violet-100 px-2 py-0.5 text-xs text-violet-700">
            专题
          </span>
        )}
      </div>
      <p className="mt-1 text-sm text-slate-600">{event.series_name || event.catalog_key}</p>
      <p className="mt-2 text-xs text-slate-500">
        {range} · {event.location}
      </p>
      <p className="mt-1 text-xs text-slate-500">
        主办：{event.host || "—"} · 报道 {event.article_count} 篇
      </p>
    </Link>
  );
}
