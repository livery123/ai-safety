/**
 * 功能：新闻/情报卡片；标题链至原文。
 */

import { formatDate } from "@/lib/api";
import type { IncidentItem } from "@/lib/types";

export default function NewsCard({ item }: { item: IncidentItem }) {
  const titleEl = item.url ? (
    <a
      href={item.url}
      target="_blank"
      rel="noopener noreferrer"
      className="text-lg font-bold text-slate-900 transition hover:text-brand-600"
    >
      {item.title}
    </a>
  ) : (
    <h3 className="text-lg font-bold text-slate-900">{item.title}</h3>
  );

  return (
    <article className="rounded-2xl border border-slate-200 bg-white p-5 shadow-card transition hover:border-slate-300">
      <div className="mb-3 flex flex-wrap gap-2">
        {item.content_type && (
          <span className="rounded-full bg-slate-100 px-2.5 py-0.5 text-xs font-medium text-slate-700">
            {item.content_type}
          </span>
        )}
        {item.risk_domain && (
          <span className="rounded-full bg-blue-50 px-2.5 py-0.5 text-xs font-medium text-brand-600">
            {item.risk_domain}
          </span>
        )}
        {item.subdomain && item.subdomain !== "未指定子域" && (
          <span className="rounded-full bg-violet-50 px-2.5 py-0.5 text-xs font-medium text-violet-700">
            {item.subdomain}
          </span>
        )}
      </div>
      {titleEl}
      {item.summary && (
        <p className="mt-3 line-clamp-3 text-sm leading-relaxed text-slate-600">
          {item.summary}
        </p>
      )}
      <div className="mt-4 flex flex-wrap items-center justify-between gap-2 text-xs text-slate-500">
        <span>
          {item.source || "未知来源"} · {formatDate(item.published_at)}
        </span>
        {item.url && (
          <a
            href={item.url}
            target="_blank"
            rel="noopener noreferrer"
            className="font-semibold text-brand-600 hover:underline"
          >
            阅读全文 →
          </a>
        )}
      </div>
      {item.tags.length > 0 && (
        <div className="mt-3 flex flex-wrap gap-1.5">
          {item.tags.slice(0, 5).map((tag) => (
            <span
              key={tag}
              className="rounded-md bg-slate-50 px-2 py-0.5 text-[11px] text-slate-600"
            >
              #{tag}
            </span>
          ))}
        </div>
      )}
    </article>
  );
}
