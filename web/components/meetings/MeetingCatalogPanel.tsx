/**
 * 功能：重大国际会议清单（对齐 conference.html 分组展示）。
 * 输入：MeetingCatalogItem[]。
 * 输出：分组卡片列表。
 */

import MeetingEventCard from "@/components/meetings/MeetingEventCard";
import type { MeetingCatalogItem } from "@/lib/types";

interface Props {
  catalog: MeetingCatalogItem[];
}

export default function MeetingCatalogPanel({ catalog }: Props) {
  return (
    <section className="space-y-8">
      <div>
        <h2 className="text-lg font-semibold text-slate-900">重大国际会议清单</h2>
        <p className="mt-1 text-sm text-slate-600">
          名录驱动的会议识别与事件归并；点击下方届次进入事件流与专题分析。
        </p>
      </div>
      {catalog.map((group) => (
        <div key={group.catalog_key} className="space-y-3">
          <h3 className="border-l-4 border-violet-500 pl-3 text-base font-medium text-slate-800">
            {group.category || group.series_name}
          </h3>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {(group.events || []).map((ev) => (
              <MeetingEventCard
                key={`${group.catalog_key}-${ev.id}-${ev.edition_label}`}
                event={{ ...ev, series_name: group.series_name }}
              />
            ))}
          </div>
        </div>
      ))}
    </section>
  );
}
