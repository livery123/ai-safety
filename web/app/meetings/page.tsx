/**
 * 功能：国际会议门户页（名录 + 事件列表 + 报道 Tab）。
 * 输入：服务端请求 /api/meetings/*、/api/systems/meeting/weekly。
 * 输出：渲染 MeetingsHub。
 */
import PageBanner from "@/components/PageBanner";
import MeetingsHub from "@/components/meetings/MeetingsHub";
import { getMeetingCatalog, getMeetingEvents, getWeeklySummary } from "@/lib/api";

/** 每次请求拉最新事件，避免 build 时 API 不可用导致长期空白。 */
export const dynamic = "force-dynamic";
import {
  SYSTEM_COLORS,
  SYSTEM_NAMES,
  SYSTEM_NOS,
  SYSTEM_TAGLINES,
} from "@/lib/system-names";

export default async function MeetingsPage() {
  let summary;
  let catalog: Awaited<ReturnType<typeof getMeetingCatalog>> = [];
  let eventItems: Awaited<ReturnType<typeof getMeetingEvents>>["items"] = [];
  try {
    summary = await getWeeklySummary("meeting");
  } catch {
    summary = undefined;
  }
  try {
    const [cat, evPage] = await Promise.all([
      getMeetingCatalog(),
      getMeetingEvents({ page: 1, page_size: 48, major_only: true }),
    ]);
    catalog = cat;
    eventItems = evPage.items;
  } catch (err) {
    console.error("[meetings] catalog/events fetch failed:", err);
    catalog = [];
    eventItems = [];
  }

  return (
    <div className="space-y-8">
      <PageBanner
        systemNo={SYSTEM_NOS.meeting}
        title={SYSTEM_NAMES.meeting}
        tagline={SYSTEM_TAGLINES.meeting}
        color={SYSTEM_COLORS.meeting}
        summary={summary}
      />
      <MeetingsHub catalog={catalog} events={eventItems} />
    </div>
  );
}
