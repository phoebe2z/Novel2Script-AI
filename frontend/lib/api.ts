export interface ConvertResponse {
  yaml: string;
  metadata: { title: string; version: string };
  scene_count: number;
  character_count: number;
}

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export async function convertNovel(
  novelText: string,
  titleHint?: string
): Promise<ConvertResponse> {
  const res = await fetch(`${API_URL}/api/convert`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      novel_text: novelText,
      title_hint: titleHint || null,
    }),
  });

  const data = await res.json();
  if (!res.ok) {
    throw new Error(data.detail ?? "转换失败");
  }
  return data;
}
