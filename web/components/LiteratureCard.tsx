/**
 * 功能：文献卡片；标题/DIO 可外链。
 */

import { formatDate } from "@/lib/api";
import type { LiteratureItem } from "@/lib/types";

export default function LiteratureCard({ item }: { item: LiteratureItem }) {
  const titleEl = item.url ? (
    <a
      href={item.url}
      target="_blank"
      rel="noopener noreferrer"
      className="text-lg font-bold text-slate-900 transition hover:text-literature"
    >
      {item.title}
    </a>
  ) : (
    <h3 className="text-lg font-bold text-slate-900">{item.title}</h3>
  );

  return (
    <article className="rounded-2xl border border-slate-200 bg-white p-5 shadow-card">
      <div className="mb-2 flex flex-wrap gap-2">
        {item.source && (
          <span className="rounded-full bg-emerald-50 px-2.5 py-0.5 text-xs font-medium text-emerald-700">
            {item.source}
          </span>
        )}
        {item.document_type && (
          <span className="rounded-full bg-slate-100 px-2.5 py-0.5 text-xs text-slate-600">
            {item.document_type}
          </span>
        )}
      </div>
      {titleEl}
      {item.authors && (
        <p className="mt-2 text-sm text-slate-600">{item.authors}</p>
      )}
      {item.publication && (
        <p className="mt-1 text-sm text-slate-500">{item.publication}</p>
      )}
      <div className="mt-4 flex flex-wrap items-center justify-between gap-2 text-xs text-slate-500">
        <span>{formatDate(item.published_at)}</span>
        {item.url && (
          <a
            href={item.url}
            target="_blank"
            rel="noopener noreferrer"
            className="font-semibold text-literature hover:underline"
          >
            查看原文 →
          </a>
        )}
      </div>
    </article>
  );
}
