/**
 * 功能：门户前端 API 客户端；请求 FastAPI /api/*。
 * 输入：SSR 用 API_INTERNAL_URL，浏览器走同源 /api 反代（见 api-base.ts）。
 * 输出：JSON 解析后的 typed 数据。
 */

import { apiUrl } from "@/lib/api-base";
import type {
  IncidentItem,
  KeywordItem,
  LiteratureItem,
  MeetingCatalogItem,
  MeetingEventDetailResponse,
  MeetingEventSummary,
  MeetingTimelineResponse,
  MonitoringOverview,
  PaginatedResponse,
  PolicyAnalyticsResponse,
  StatsResponse,
  SystemInfo,
  WeeklySummary,
  WeeklyReportItem,
  WeeklyReportDetail,
} from "./types";

async function fetchJson<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(apiUrl(path), {
    ...init,
    next: { revalidate: 60 },
  });
  if (!res.ok) {
    throw new Error(`API ${path} failed: ${res.status}`);
  }
  return res.json() as Promise<T>;
}

export function getStats(): Promise<StatsResponse> {
  return fetchJson("/api/stats");
}

export function getKeywords(limit = 20): Promise<KeywordItem[]> {
  return fetchJson(`/api/stats/keywords?limit=${limit}`);
}

export function getPolicyAnalytics(params?: {
  country_limit?: number;
  word_limit?: number;
  word_field?: string;
  week_limit?: number;
}): Promise<PolicyAnalyticsResponse> {
  const q = new URLSearchParams();
  if (params?.country_limit) q.set("country_limit", String(params.country_limit));
  if (params?.word_limit) q.set("word_limit", String(params.word_limit));
  if (params?.word_field) q.set("word_field", params.word_field);
  if (params?.week_limit) q.set("week_limit", String(params.week_limit));
  const qs = q.toString();
  return fetchJson(`/api/stats/policy/analytics${qs ? `?${qs}` : ""}`);
}

export function getSystems(): Promise<SystemInfo[]> {
  return fetchJson("/api/systems");
}

export function getMonitoringOverview(): Promise<MonitoringOverview> {
  return fetchJson("/api/monitoring/overview");
}

export function getWeeklySummary(system: string): Promise<WeeklySummary> {
  return fetchJson(`/api/systems/${system}/weekly`);
}

export function getWeeklyReports(params: {
  system?: string;
  report_type?: string;
  limit?: number;
}): Promise<WeeklyReportItem[]> {
  const q = new URLSearchParams();
  if (params.system) q.set("system", params.system);
  if (params.report_type) q.set("report_type", params.report_type);
  if (params.limit) q.set("limit", String(params.limit));
  const qs = q.toString();
  return fetchJson(`/api/analysis/reports/weekly${qs ? `?${qs}` : ""}`);
}

export function getWeeklyReportDetail(id: number): Promise<WeeklyReportDetail> {
  return fetchJson(`/api/analysis/reports/weekly/${id}`);
}

export function getLatestIncidents(limit = 12): Promise<IncidentItem[]> {
  return fetchJson(`/api/incidents/latest?limit=${limit}`);
}

export function getIncidents(params: {
  page?: number;
  page_size?: number;
  keyword?: string;
}): Promise<PaginatedResponse<IncidentItem>> {
  const q = new URLSearchParams();
  if (params.page) q.set("page", String(params.page));
  if (params.page_size) q.set("page_size", String(params.page_size));
  if (params.keyword) q.set("keyword", params.keyword);
  return fetchJson(`/api/incidents?${q.toString()}`);
}

export function getPolicyTracks(params: {
  page?: number;
  keyword?: string;
}): Promise<PaginatedResponse<IncidentItem>> {
  const q = new URLSearchParams();
  if (params.page) q.set("page", String(params.page));
  if (params.keyword) q.set("keyword", params.keyword);
  q.set("page_size", "12");
  return fetchJson(`/api/tracks/policy?${q.toString()}`);
}

export function getMeetingTracks(params: {
  page?: number;
  keyword?: string;
}): Promise<PaginatedResponse<IncidentItem>> {
  const q = new URLSearchParams();
  if (params.page) q.set("page", String(params.page));
  if (params.keyword) q.set("keyword", params.keyword);
  q.set("page_size", "12");
  return fetchJson(`/api/tracks/meetings?${q.toString()}`);
}

export function getMeetingCatalog(): Promise<MeetingCatalogItem[]> {
  return fetchJson("/api/meetings/catalog");
}

export function getMeetingEvents(params?: {
  page?: number;
  page_size?: number;
  catalog_key?: string;
  major_only?: boolean;
}): Promise<PaginatedResponse<MeetingEventSummary>> {
  const q = new URLSearchParams();
  if (params?.page) q.set("page", String(params.page));
  if (params?.page_size) q.set("page_size", String(params.page_size));
  if (params?.catalog_key) q.set("catalog_key", params.catalog_key);
  if (params?.major_only === false) q.set("major_only", "false");
  const qs = q.toString();
  return fetchJson(`/api/meetings/events${qs ? `?${qs}` : ""}`);
}

export function getMeetingEventDetail(
  eventId: number
): Promise<MeetingEventDetailResponse> {
  return fetchJson(`/api/meetings/events/${eventId}`);
}

export function getMeetingTimeline(
  eventId: number
): Promise<MeetingTimelineResponse> {
  return fetchJson(`/api/meetings/events/${eventId}/timeline`);
}

export function getLiteratureTracks(params: {
  page?: number;
  keyword?: string;
  source?: string;
}): Promise<PaginatedResponse<LiteratureItem>> {
  const q = new URLSearchParams();
  if (params.page) q.set("page", String(params.page));
  if (params.keyword) q.set("keyword", params.keyword);
  if (params.source) q.set("source", params.source);
  q.set("page_size", "12");
  return fetchJson(`/api/tracks/literature?${q.toString()}`);
}

export function formatDate(iso?: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso.slice(0, 10);
  return d.toLocaleDateString("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  });
}

export function formatTime(iso?: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso.slice(11, 16) || "—";
  return d.toLocaleTimeString("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}
