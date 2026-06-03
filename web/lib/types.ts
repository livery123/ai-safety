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

export interface PolicyCountItem {
  label: string;
  count: number;
  kind: "sovereign" | "region";
}

export interface PolicyWordItem {
  text: string;
  value: number;
  category: "authority" | "tag" | "intl_org";
}

export interface PolicyCoverageStats {
  sovereign_count: number;
  sovereign_names: string[];
  region_count: number;
  region_names: string[];
  intl_org_doc_count: number;
  missing_geo_count: number;
  meets_kpi: boolean;
}

export interface PolicyAnalyticsResponse {
  coverage: PolicyCoverageStats;
  by_country: PolicyCountItem[];
  by_week: KeywordItem[];
  wordcloud: PolicyWordItem[];
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
  publish_country?: string;
  publish_region?: string;
  publish_authority?: string;
  international_orgs?: string;
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

export interface MeetingEventSummary {
  id: number;
  catalog_key: string;
  series_name?: string;
  edition_label: string;
  edition_year?: number | null;
  start_date?: string | null;
  end_date?: string | null;
  location: string;
  host: string;
  status: string;
  article_count: number;
  has_analysis: boolean;
}

export interface MeetingCatalogItem {
  catalog_key: string;
  series_name: string;
  category: string;
  is_major: boolean;
  aliases: string[];
  topics: string[];
  official_urls: string[];
  events: MeetingEventSummary[];
}

export interface MeetingTimelineArticle {
  article_id: number;
  title: string;
  summary: string;
  source: string;
  url: string;
  published_at?: string | null;
  phase: string;
}

export interface MeetingTimelineResponse {
  event_id: number;
  pre: MeetingTimelineArticle[];
  during: MeetingTimelineArticle[];
  post: MeetingTimelineArticle[];
  unknown: MeetingTimelineArticle[];
}

export interface MeetingEventDetailResponse {
  event: MeetingEventSummary;
  countries: string[];
  official_url: string;
  notes: string;
  analysis_markdown: string;
  analysis_generated_at?: string | null;
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

export interface WeeklyReportItem {
  id: number;
  system_key: string;
  report_type: string;
  week_start: string;
  week_end: string;
  title: string;
  excerpt: string;
  article_count: number;
  task_id?: number | null;
  trigger_source: string;
  created_at: string;
}

export interface WeeklyReportDetail extends WeeklyReportItem {
  report_markdown: string;
  model_name: string;
  source_article_ids: number[];
}
