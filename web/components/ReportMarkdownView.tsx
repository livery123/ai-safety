/**
 * 功能：监测报告 Markdown 轻量渲染（无额外依赖）。
 */

import type { ReactNode } from "react";

function escapeHtml(text: string): string {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

function inlineFormat(text: string): string {
  let s = escapeHtml(text);
  s = s.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer" class="text-brand-600 hover:underline">$1</a>');
  s = s.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  return s;
}

export function renderMarkdownToBlocks(md: string): ReactNode[] {
  const lines = (md || "").split("\n");
  const nodes: ReactNode[] = [];
  let listItems: string[] = [];
  let key = 0;

  const flushList = () => {
    if (listItems.length === 0) return;
    nodes.push(
      <ul key={`ul-${key++}`} className="my-3 list-disc space-y-1 pl-5 text-slate-700">
        {listItems.map((item, i) => (
          <li key={i} dangerouslySetInnerHTML={{ __html: inlineFormat(item) }} />
        ))}
      </ul>
    );
    listItems = [];
  };

  for (const line of lines) {
    const trimmed = line.trimEnd();
    if (trimmed.startsWith("- ") || trimmed.startsWith("* ")) {
      listItems.push(trimmed.slice(2));
      continue;
    }
    flushList();
    if (!trimmed) {
      nodes.push(<div key={`sp-${key++}`} className="h-2" />);
      continue;
    }
    if (trimmed.startsWith("### ")) {
      nodes.push(
        <h3 key={`h3-${key++}`} className="mt-4 text-base font-bold text-slate-900">
          {trimmed.slice(4)}
        </h3>
      );
    } else if (trimmed.startsWith("## ")) {
      nodes.push(
        <h2 key={`h2-${key++}`} className="mt-6 border-b border-slate-100 pb-2 text-lg font-bold text-slate-900">
          {trimmed.slice(3)}
        </h2>
      );
    } else if (trimmed.startsWith("# ")) {
      nodes.push(
        <h1 key={`h1-${key++}`} className="text-2xl font-bold text-slate-900">
          {trimmed.slice(2)}
        </h1>
      );
    } else {
      nodes.push(
        <p
          key={`p-${key++}`}
          className="leading-relaxed text-slate-700"
          dangerouslySetInnerHTML={{ __html: inlineFormat(trimmed) }}
        />
      );
    }
  }
  flushList();
  return nodes;
}

export default function ReportMarkdownView({ markdown }: { markdown: string }) {
  return <article className="prose-report space-y-1">{renderMarkdownToBlocks(markdown)}</article>;
}
