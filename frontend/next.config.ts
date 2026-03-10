import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Статический экспорт — фронтенд собирается в папку out/
  output: "export",
  // URL бэкенда для API запросов (в продакшне — тот же домен)
  env: {
    NEXT_PUBLIC_API_URL: process.env.NEXT_PUBLIC_API_URL || "",
  },
};

export default nextConfig;
