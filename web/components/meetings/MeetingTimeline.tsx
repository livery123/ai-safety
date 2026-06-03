/**
 * 功能：会前 / 会中 / 会后事件流时间线。
 * 输入：MeetingTimelineResponse。
 * 输出：三阶段分组列表。
 */

import { formatDate } from "@/lib/api";
import type { MeetingTimelineArticle } from "@/lib/types";

const PHASE_LABELS: Record<string, string> = {
  pre: "会前",
  during: "会中",
  post: "会后",
  unknown: "其他相关",
};

function ArticleList({ items }: { items: MeetingTimelineArticle[] }) {
  if (!items.length) {
    return <p className="text-sm text-slate-500">暂无报道</p>;
  }
  return (
    <ul className="space-y-3">
      {items.map((a) => (
        <li
          key={a.article_id}
          className="rounded-md border border-slate-100 bg-slate-50/80 p-3 text-sm"
        >
          <a
            href={a.url}
            target="_blank"
            rel="noopener noreferrer"
            className="font-medium text-violet-700 hover:underline"
          >
            {a.title}
          </a>
          <p className="mt-1 text-xs text-slate-500">
            {a.source} · {formatDate(a.published_at)}
          </p>
          {a.summary && <p className="mt-2 text-slate-600 line-clamp-3">{a.summary}</p>}
        </li>
      ))}
    </ul>
  );
}

interface Props {
  timeline: {
    pre: MeetingTimelineArticle[];
    during: MeetingTimelineArticle[];
    post: MeetingTimelineArticle[];
    unknown: MeetingTimelineArticle[];
  };
}

export default function MeetingTimeline({ timeline }: Props) {
  const phases = ["pre", "during", "post", "unknown"] as const;
  return (
    <section className="space-y-6">
      <h2 className="text-lg font-semibold text-slate-900">事件流</h2>
      <div className="grid gap-6 lg:grid-cols-3">
        {phases.map((ph) => {
          const items = timeline[ph] || [];
          if (ph === "unknown" && items.length === 0) return null;
          return (
            <div key={ph} className="rounded-lg border border-slate-200 bg-white p-4">
              <h3 className="mb-3 text-sm font-semibold uppercase tracking-wide text-violet-700">
                {PHASE_LABELS[ph]}
                <span className="ml-2 font-normal text-slate-500">({items.length})</span>
              </h3>
              <ArticleList items={items} />
            </div>
          );
        })}
      </div>
    </section>
  );
}
