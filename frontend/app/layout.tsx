import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Novel2Script AI | 小说转剧本助手",
  description: "将小说文本自动转换为结构化 YAML 剧本",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  );
}
