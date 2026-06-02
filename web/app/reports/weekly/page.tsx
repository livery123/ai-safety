import WeeklyReportsClient from "@/components/WeeklyReportsClient";

export default function WeeklyReportsPage({
  searchParams,
}: {
  searchParams?: { system?: string };
}) {
  const system = searchParams?.system || "policy";
  return <WeeklyReportsClient initialSystem={system} />;
}
