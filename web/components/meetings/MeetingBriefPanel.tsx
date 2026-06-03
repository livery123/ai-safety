/**
 * 功能：会议专题分析 Markdown 展示。
 * 输入：analysis_markdown、generated_at。
 * 输出：渲染区块。
 */

import ReportMarkdownView from "@/components/ReportMarkdownView";
import { formatDate } from "@/lib/api";

interface Props {
  markdown: string;
  generatedAt?: string | null;
}

export default function MeetingBriefPanel({ markdown, generatedAt }: Props) {
  if (!markdown?.trim()) {
    return (
      <section className="rounded-lg border border-dashed border-slate-300 bg-slate-50 p-6 text-sm text-slate-600">
        <h2 className="text-lg font-semibold text-slate-800">专题分析</h2>
        <p className="mt-2">
          尚无专题报告。关联报道达到阈值后可运行{" "}
          <code className="text-xs">scripts/generate_meeting_briefs.py</code> 生成。
        </p>
      </section>
    );
  }
  return (
    <section className="rounded-lg border border-slate-200 bg-white p-6">
      <div className="mb-4 flex items-center justify-between">
        <h2 className="text-lg font-semibold text-slate-900">专题分析</h2>
        {generatedAt && (
          <span className="text-xs text-slate-500">
            更新于 {formatDate(generatedAt)}
          </span>
        )}
      </div>
      <ReportMarkdownView markdown={markdown} />
    </section>
  );
}
