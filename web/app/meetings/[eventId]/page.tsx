import Link from "next/link";
import MeetingBriefPanel from "@/components/meetings/MeetingBriefPanel";
import MeetingTimeline from "@/components/meetings/MeetingTimeline";
import { formatDate, getMeetingEventDetail, getMeetingTimeline } from "@/lib/api";
import { notFound } from "next/navigation";

export const dynamic = "force-dynamic";

interface PageProps {
  params: Promise<{ eventId: string }>;
}

export default async function MeetingEventPage({ params }: PageProps) {
  const { eventId: rawId } = await params;
  const eventId = parseInt(rawId, 10);
  if (!Number.isFinite(eventId) || eventId <= 0) {
    notFound();
  }

  let detail;
  let timeline;
  try {
    [detail, timeline] = await Promise.all([
      getMeetingEventDetail(eventId),
      getMeetingTimeline(eventId),
    ]);
  } catch {
    notFound();
  }

  const ev = detail.event;

  return (
    <div className="mx-auto max-w-6xl space-y-8 px-4 py-8">
      <Link href="/meetings" className="text-sm text-violet-600 hover:underline">
        ← 返回会议监测
      </Link>

      <header className="space-y-2 border-b border-slate-200 pb-6">
        <p className="text-sm text-violet-600">{ev.series_name || ev.catalog_key}</p>
        <h1 className="text-2xl font-bold text-slate-900">{ev.edition_label}</h1>
        <p className="text-sm text-slate-600">
          {formatDate(ev.start_date)} — {formatDate(ev.end_date)} · {ev.location}
        </p>
        <p className="text-sm text-slate-600">主办：{ev.host || "—"}</p>
        {detail.countries?.length > 0 && (
          <p className="text-sm text-slate-600">
            参与国家/地区：{detail.countries.join("、")}
          </p>
        )}
        {detail.official_url && (
          <a
            href={detail.official_url}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-block text-sm text-violet-700 hover:underline"
          >
            官网链接
          </a>
        )}
      </header>

      <MeetingBriefPanel
        markdown={detail.analysis_markdown}
        generatedAt={detail.analysis_generated_at}
      />

      <MeetingTimeline timeline={timeline} />
    </div>
  );
}
