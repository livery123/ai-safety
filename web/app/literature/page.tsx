import LiteratureList from "@/components/LiteratureList";
import PageBanner from "@/components/PageBanner";
import { getWeeklySummary } from "@/lib/api";
import {
  SYSTEM_COLORS,
  SYSTEM_NAMES,
  SYSTEM_NOS,
  SYSTEM_TAGLINES,
} from "@/lib/system-names";

export default async function LiteraturePage() {
  let summary;
  try {
    summary = await getWeeklySummary("literature");
  } catch {
    summary = undefined;
  }

  return (
    <div className="space-y-8">
      <PageBanner
        systemNo={SYSTEM_NOS.literature}
        title={SYSTEM_NAMES.literature}
        tagline={SYSTEM_TAGLINES.literature}
        color={SYSTEM_COLORS.literature}
        summary={summary}
      />
      <LiteratureList />
    </div>
  );
}
