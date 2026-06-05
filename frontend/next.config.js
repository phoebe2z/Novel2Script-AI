/** @type {import('next').NextConfig} */
const path = require("path");

const backendUrl = process.env.BACKEND_URL || "http://localhost:8001";

const nextConfig = {
  reactStrictMode: true,
  outputFileTracingRoot: path.join(__dirname),
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `${backendUrl}/api/:path*`,
      },
    ];
  },
};

module.exports = nextConfig;
