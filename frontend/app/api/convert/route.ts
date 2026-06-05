import { NextRequest, NextResponse } from "next/server";

const BACKEND_URL = process.env.BACKEND_URL || "http://localhost:8080";
const TIMEOUT_MS = 5 * 60 * 1000;

export async function POST(request: NextRequest) {
  const body = await request.text();
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), TIMEOUT_MS);

  try {
    const res = await fetch(`${BACKEND_URL}/api/convert`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body,
      signal: controller.signal,
    });

    const text = await res.text();
    return new NextResponse(text, {
      status: res.status,
      headers: { "Content-Type": "application/json; charset=utf-8" },
    });
  } catch (err) {
    const message =
      err instanceof Error && err.name === "AbortError"
        ? "转换超时（超过 5 分钟），建议分段粘贴后重试"
        : "无法连接后端，请确认已在 8080 端口启动 uvicorn";
    return NextResponse.json({ detail: message }, { status: 502 });
  } finally {
    clearTimeout(timer);
  }
}
