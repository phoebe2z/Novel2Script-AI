export interface ConvertResponse {
  yaml: string;
  metadata: { title: string; version: string };
  scene_count: number;
  character_count: number;
}

// 默认走 Next.js 代理（/api → 后端 8001），避免端口不一致导致 failed to fetch
const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "";

export async function convertNovel(
  novelText: string,
  titleHint?: string
): Promise<ConvertResponse> {
  let res: Response;
  try {
    res = await fetch(`${API_BASE}/api/convert`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        novel_text: novelText,
        title_hint: titleHint || null,
      }),
    });
  } catch {
    throw new Error(
      "无法连接后端，请确认已在 backend 目录启动：uvicorn main:app --reload --port 8001"
    );
  }

  const data = await res.json();
  if (!res.ok) {
    throw new Error(data.detail ?? "转换失败");
  }
  return data;
}
