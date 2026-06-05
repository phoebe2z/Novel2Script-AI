export interface ConvertResponse {
  yaml: string;
  metadata: { title: string; version: string };
  scene_count: number;
  character_count: number;
  source_scenes: number;
}

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "";

function parseErrorDetail(text: string, status: number): string {
  if (text.includes("Internal Server Error") || text.startsWith("Internal S")) {
    return "转换超时或连接中断。长文请分段转换，并确认后端在 8080 端口运行。";
  }
  try {
    const data = JSON.parse(text) as { detail?: string };
    if (data.detail) return data.detail;
  } catch {
    /* not json */
  }
  return text.slice(0, 300) || `请求失败 (${status})`;
}

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
      "无法连接服务，请确认前端与后端均已启动（后端：uvicorn main:app --port 8080）"
    );
  }

  const text = await res.text();

  let data: ConvertResponse & { detail?: string };
  try {
    data = JSON.parse(text) as ConvertResponse & { detail?: string };
  } catch {
    throw new Error(parseErrorDetail(text, res.status));
  }

  if (!res.ok) {
    throw new Error(data.detail ?? parseErrorDetail(text, res.status));
  }

  return data;
}
