/**
 * 功能：门户前端 API 客户端；请求 FastAPI /api/*。
 * 输入：环境变量 NEXT_PUBLIC_API_URL。
 * 输出：JSON 解析后的 typed 数据。
 */

import type {
  IncidentItem,
  KeywordItem,
  LiteratureItem,
  MonitoringOverview,
  PaginatedResponse,
  PolicyAnalyticsResponse,
  StatsResponse,
  SystemInfo,
  WeeklySummary,
  WeeklyReportItem,
  WeeklyReportDetail,
} from "./types";

const API_BASE =
  process.env.NEXT_PUBLIC_API_URL?.replace(/\/$/, "") || "http://127.0.0.1:8000";

async function fetchJson<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
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
