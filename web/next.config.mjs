/** @type {import('next').NextConfig} */
const apiInternal = (process.env.API_INTERNAL_URL || "http://127.0.0.1:8000").replace(/\/$/, "");

const nextConfig = {
  reactStrictMode: true,
  /** 浏览器 /api/* 反代到本机 FastAPI，避免外网访问时请求 127.0.0.1 失败。 */
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `${apiInternal}/api/:path*`,
      },
    ];
  },
};

export default nextConfig;
