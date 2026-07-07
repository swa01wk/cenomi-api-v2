import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  // Produces a self-contained server bundle in .next/standalone —
  // required for the Docker multi-stage build (copies only server.js + static assets).
  output: "standalone",
};

export default nextConfig;
