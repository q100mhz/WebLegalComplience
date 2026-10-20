/** @type {import('next').NextConfig} */
const nextConfig = {
  output: "standalone",
  poweredByHeader: false,
  // В продакшене /api проксирует nginx напрямую в бэкенд; для локальной разработки — через Next
  async rewrites() {
    return process.env.API_URL
      ? [{ source: "/api/:path*", destination: `${process.env.API_URL}/api/:path*` }]
      : [];
  },
};

export default nextConfig;
