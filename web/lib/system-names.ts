/**
 * 功能：三大监测系统正式名称（全站唯一来源，子页/导航/API 文案应对齐）。
 */

export const SYSTEM_NAMES = {
  policy: "政策法规/科技政策监测系统",
  meeting: "重大国际会议监测系统",
  literature: "国内外相关文献监测系统",
} as const;

export const SYSTEM_NAV_LABELS = {
  policy: "政策法规/科技政策",
  meeting: "重大国际会议",
  literature: "国内外相关文献",
} as const;

export const SYSTEM_TAGLINES = {
  policy: "追踪全球 AI 立法、监管文件与科技政策动态",
  meeting: "监测重大国际 AI 治理会议、论坛与多边磋商",
  literature: "汇聚 arXiv / Scopus / Springer 等 AI 安全学术文献",
} as const;

export const SYSTEM_NOS = {
  policy: "系统一",
  meeting: "系统二",
  literature: "系统三",
} as const;

export const SYSTEM_COLORS = {
  policy: "#2563eb",
  meeting: "#7c3aed",
  literature: "#059669",
} as const;
