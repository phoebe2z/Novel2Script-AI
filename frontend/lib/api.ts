export interface ConvertResponse {
  yaml: string;
  metadata: { title: string; author?: string; version: string; created_at?: string };
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

export interface StreamCallbacks {
  onStart?: (sourceScenes: number) => void;
  onProgress?: (message: string) => void;
  onToken?: (text: string) => void;
}

export async function convertNovelStream(
  novelText: string,
  titleHint: string | undefined,
  callbacks: StreamCallbacks
): Promise<ConvertResponse> {
  let res: Response;
  try {
    res = await fetch(`${API_BASE}/api/convert/stream`, {
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

  if (!res.ok || !res.body) {
    const text = await res.text();
    throw new Error(parseErrorDetail(text, res.status));
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  return new Promise((resolve, reject) => {
    const pump = (): void => {
      reader
        .read()
        .then(({ done, value }) => {
          if (done) {
            reject(new Error("流式连接意外结束，未收到完整结果"));
            return;
          }

          buffer += decoder.decode(value, { stream: true });
          const chunks = buffer.split("\n\n");
          buffer = chunks.pop() ?? "";

          for (const chunk of chunks) {
            const line = chunk
              .split("\n")
              .find((l) => l.startsWith("data: "));
            if (!line) continue;

            try {
              const event = JSON.parse(line.slice(6)) as {
                type: string;
                text?: string;
                message?: string;
                source_scenes?: number;
                detail?: string;
                yaml?: string;
                metadata?: ConvertResponse["metadata"];
                scene_count?: number;
                character_count?: number;
              };

              switch (event.type) {
                case "start":
                  callbacks.onStart?.(event.source_scenes ?? 0);
                  break;
                case "progress":
                  if (event.message) callbacks.onProgress?.(event.message);
                  break;
                case "token":
                  if (event.text) callbacks.onToken?.(event.text);
                  break;
                case "complete":
                  resolve({
                    yaml: event.yaml ?? "",
                    metadata: event.metadata ?? { title: "未命名", version: "2.0" },
                    scene_count: event.scene_count ?? 0,
                    character_count: event.character_count ?? 0,
                    source_scenes: event.source_scenes ?? 0,
                  });
                  return;
                case "error":
                  reject(new Error(event.detail ?? "转换失败"));
                  return;
              }
            } catch {
              /* skip malformed chunk */
            }
          }

          pump();
        })
        .catch(reject);
    };

    pump();
  });
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
