/**
 * 功能：统一解析 API 基址——浏览器走同源 /api（Next 反代），服务端 SSR 走内网地址。
 * 输入：环境变量 API_INTERNAL_URL / NEXT_PUBLIC_API_URL。
 * 输出：完整 URL 或相对路径。
 * 上下游：lib/api.ts、TrackList、LiteratureList。
 */

/** 浏览器端返回空字符串，使用相对路径 /api/*；SSR 使用内网 FastAPI 地址。 */
export function getApiBase(): string {
  if (typeof window !== "undefined") {
    return "";
  }
  return (
    process.env.API_INTERNAL_URL ||
    process.env.NEXT_PUBLIC_API_URL ||
    "http://127.0.0.1:8000"
  ).replace(/\/$/, "");
}

/** 拼接 API 路径为可 fetch 的 URL。 */
export function apiUrl(path: string): string {
  const normalized = path.startsWith("/") ? path : `/${path}`;
  const base = getApiBase();
  return base ? `${base}${normalized}` : normalized;
}
