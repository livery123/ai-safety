import PageBanner from "@/components/PageBanner";
import AnalysisBriefCard from "@/components/AnalysisBriefCard";
import PolicyAnalyticsDashboard from "@/components/policy/PolicyAnalyticsDashboard";
import TrackList from "@/components/TrackList";
import { getWeeklyReports, getWeeklySummary } from "@/lib/api";
import {
  SYSTEM_COLORS,
  SYSTEM_NAMES,
  SYSTEM_NOS,
  SYSTEM_TAGLINES,
} from "@/lib/system-names";

export default async function PolicyPage() {
  let summary;
  let brief;
  try {
    [summary, brief] = await Promise.all([
      getWeeklySummary("policy"),
      getWeeklyReports({ system: "policy", report_type: "brief", limit: 1 }),
    ]);
  } catch {
    summary = undefined;
    brief = undefined;
  }
  const latestBrief = brief?.[0];

  return (
    <div className="space-y-8">
      <PageBanner
        systemNo={SYSTEM_NOS.policy}
        title={SYSTEM_NAMES.policy}
        tagline={SYSTEM_TAGLINES.policy}
        color={SYSTEM_COLORS.policy}
        summary={summary}
      />
      <AnalysisBriefCard brief={latestBrief} systemKey="policy" />
      <PolicyAnalyticsDashboard />
      <TrackList track="policy" />
    </div>
  );
}
