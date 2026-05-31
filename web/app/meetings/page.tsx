import PageBanner from "@/components/PageBanner";
import TrackList from "@/components/TrackList";
import { getWeeklySummary } from "@/lib/api";
import {
  SYSTEM_COLORS,
  SYSTEM_NAMES,
  SYSTEM_NOS,
  SYSTEM_TAGLINES,
} from "@/lib/system-names";

export default async function MeetingsPage() {
  let summary;
  try {
    summary = await getWeeklySummary("meeting");
  } catch {
    summary = undefined;
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
      <TrackList track="meetings" />
    </div>
  );
}
