import type { NextConfig } from "next";

const backendUrl = (process.env.BACKEND_URL || "http://127.0.0.1:8000").replace(/\/$/, "");

const nextConfig: NextConfig = {
  agentRules: false,
  experimental: {
    useTypeScriptCli: false,
    // Next buffers request bodies before a route handler can proxy them. Keep
    // this slightly above the 35 MiB file contract to allow multipart framing.
    // The client and API enforce the actual 35 MiB file limit.
    proxyClientMaxBodySize: "36mb",
  },
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${backendUrl}/api/:path*` }];
  },
};

export default nextConfig;
