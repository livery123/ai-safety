/**
 * 功能：与 api/schemas.py 对齐的 TypeScript 类型。
 */

export interface StatsResponse {
  total_incidents: number;
  total_tags: number;
  taxonomy_kinds: number;
  keyword_nodes: number;
}

export interface KeywordItem {
  keyword: string;
  count: number;
}

export interface IncidentItem {
  id?: number | null;
  title: string;
  content_type: string;
  risk_domain: string;
  subdomain: string;
  entities: string;
  summary: string;
  source: string;
  url: string;
  tags: string[];
  published_at?: string | null;
}

export interface LiteratureItem {
  title: string;
  source: string;
  authors: string;
  publication: string;
  document_type: string;
  doi: string;
  published_at?: string | null;
  url: string;
}

export interface WeeklySummary {
  range_start: string;
  range_end: string;
  week_new: number;
  total: number;
  top_source: string;
  top_subdomain: string;
  highlights: string[];
  bullets: string[];
}

export interface SystemInfo {
  key: string;
  system_no: string;
  name: string;
  tagline: string;
  color: string;
  week_new: number;
  total: number;
}

export interface PaginatedResponse<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
  pages: number;
}

export interface PlatformStatus {
  status: string;
  status_label: string;
  online_subsystems: number;
  total_subsystems: number;
  today_run_count: number;
  today_new_data: number;
  last_run_at?: string | null;
  last_run_ago: string;
  next_scheduled_at?: string | null;
  next_scheduled_ago: string;
}

export interface SubsystemStatus {
  key: string;
  name: string;
  status: string;
  status_label: string;
  last_run_at?: string | null;
  last_run_ago: string;
  today_new: number;
  total: number;
  source_count: number;
  source_label: string;
  detail_href: string;
  highlight_count?: number;
  highlight_label?: string;
}

export interface TimelineItem {
  at?: string | null;
  system_key: string;
  system_label: string;
  summary: string;
  status: string;
  data_count: number;
}

export interface MonitoringOverview {
  platform: PlatformStatus;
  subsystems: SubsystemStatus[];
  timeline: TimelineItem[];
}

export interface SourceFilterOption {
  key: string;
  label: string;
  group: string;
  group_label: string;
  count: number;
  hint?: string;
}

export interface SourceFilterResponse {
  track: string;
  panel_title: string;
  options: SourceFilterOption[];
  total_count?: number;
}
