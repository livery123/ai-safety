import PageBanner from "@/components/PageBanner";
import TrackList from "@/components/TrackList";
import { getWeeklySummary } from "@/lib/api";
import {
  SYSTEM_COLORS,
  SYSTEM_NAMES,
  SYSTEM_NOS,
  SYSTEM_TAGLINES,
} from "@/lib/system-names";

export default async function PolicyPage() {
  let summary;
  try {
    summary = await getWeeklySummary("policy");
  } catch {
    summary = undefined;
  }

  return (
    <div className="space-y-8">
      <PageBanner
        systemNo={SYSTEM_NOS.policy}
        title={SYSTEM_NAMES.policy}
        tagline={SYSTEM_TAGLINES.policy}
        color={SYSTEM_COLORS.policy}
        summary={summary}
      />
      <TrackList track="policy" />
    </div>
  );
}
