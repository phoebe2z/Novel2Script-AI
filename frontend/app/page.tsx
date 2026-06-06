"use client";

import { useEffect, useState } from "react";
import { convertNovel, type ConvertResponse } from "@/lib/api";

function extractTitleFromYaml(yaml: string, fallback: string) {
  const match = yaml.match(/^\s*title:\s*["']?([^"'\n]+)["']?\s*$/m);
  return match?.[1]?.trim() || fallback;
}

function downloadYaml(yaml: string, title: string) {
  const filename = extractTitleFromYaml(yaml, title);
  const blob = new Blob([yaml], { type: "text/yaml;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `${filename || "script"}.yaml`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

export default function Home() {
  const [novelText, setNovelText] = useState("");
  const [titleHint, setTitleHint] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<ConvertResponse | null>(null);
  const [editableYaml, setEditableYaml] = useState("");

  useEffect(() => {
    if (result?.yaml) {
      setEditableYaml(result.yaml);
    }
  }, [result]);

  async function handleConvert() {
    if (!novelText.trim()) {
      setError("请粘贴小说文本");
      return;
    }
    setLoading(true);
    setError(null);
    setResult(null);
    setEditableYaml("");
    try {
      const data = await convertNovel(novelText, titleHint || undefined);
      setResult(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : "转换失败");
    } finally {
      setLoading(false);
    }
  }

  const isEdited = result != null && editableYaml !== result.yaml;

  return (
    <main style={styles.main}>
      <header style={styles.header}>
        <h1 style={styles.title}>Novel2Script AI</h1>
        <p style={styles.subtitle}>小说转剧本助手 · 自动输出 YAML 结构化剧本</p>
      </header>

      <div style={styles.grid}>
        <section style={styles.panel}>
          <h2 style={styles.panelTitle}>输入</h2>
          <label style={styles.label}>
            标题提示（可选）
            <input
              type="text"
              placeholder="例如：三体第一章"
              value={titleHint}
              onChange={(e) => setTitleHint(e.target.value)}
              style={{ marginTop: 6 }}
            />
          </label>
          <label style={{ ...styles.label, flex: 1, display: "flex", flexDirection: "column" }}>
            小说文本
            <textarea
              placeholder="在此粘贴多章节小说内容..."
              value={novelText}
              onChange={(e) => setNovelText(e.target.value)}
              rows={20}
              style={{ marginTop: 6, flex: 1, minHeight: 320 }}
            />
          </label>
          <button
            onClick={handleConvert}
            disabled={loading}
            style={styles.convertBtn}
          >
            {loading ? "转换中..." : "转换为剧本"}
          </button>
          {error && <p style={styles.error}>{error}</p>}
        </section>

        <section style={styles.panel}>
          <div style={styles.previewHeader}>
            <div>
              <h2 style={styles.panelTitle}>YAML 编辑</h2>
              {result && (
                <p style={styles.editHint}>
                  可直接修改内容，下载时将保存当前编辑版本
                  {isEdited && " · 已编辑"}
                </p>
              )}
            </div>
            {result && editableYaml && (
              <div style={styles.previewActions}>
                {isEdited && (
                  <button
                    onClick={() => setEditableYaml(result.yaml)}
                    style={styles.resetBtn}
                  >
                    还原
                  </button>
                )}
                <button
                  onClick={() =>
                    downloadYaml(editableYaml, result.metadata.title)
                  }
                  style={styles.downloadBtn}
                >
                  下载 .yaml
                </button>
              </div>
            )}
          </div>

          {result ? (
            <>
              <div style={styles.stats}>
                <span>标题：{result.metadata.title}</span>
                <span>场景：{result.scene_count}</span>
                <span>自动切分：{result.source_scenes} 场</span>
                <span>角色：{result.character_count}</span>
              </div>
              <textarea
                value={editableYaml}
                onChange={(e) => setEditableYaml(e.target.value)}
                spellCheck={false}
                style={styles.yamlEditor}
                aria-label="YAML 剧本编辑器"
              />
            </>
          ) : (
            <div style={styles.placeholder}>
              {loading
                ? "AI 正在分析文本并拆分场景..."
                : "转换结果将在此显示"}
            </div>
          )}
        </section>
      </div>
    </main>
  );
}

const styles: Record<string, React.CSSProperties> = {
  main: {
    maxWidth: 1200,
    margin: "0 auto",
    padding: "32px 24px",
    minHeight: "100vh",
  },
  header: {
    marginBottom: 32,
    textAlign: "center",
  },
  title: {
    fontSize: 28,
    fontWeight: 700,
    letterSpacing: "-0.02em",
    background: "linear-gradient(135deg, #e8e8ed 0%, #9585ff 100%)",
    WebkitBackgroundClip: "text",
    WebkitTextFillColor: "transparent",
  },
  subtitle: {
    color: "var(--text-muted)",
    marginTop: 8,
    fontSize: 15,
  },
  grid: {
    display: "grid",
    gridTemplateColumns: "1fr 1fr",
    gap: 24,
    alignItems: "stretch",
  },
  panel: {
    background: "var(--surface)",
    border: "1px solid var(--border)",
    borderRadius: 12,
    padding: 20,
    display: "flex",
    flexDirection: "column",
    gap: 14,
    minHeight: 520,
  },
  panelTitle: {
    fontSize: 16,
    fontWeight: 600,
  },
  label: {
    fontSize: 13,
    color: "var(--text-muted)",
    fontWeight: 500,
  },
  convertBtn: {
    background: "var(--accent)",
    color: "#fff",
    padding: "12px 20px",
    fontSize: 15,
    fontWeight: 600,
  },
  previewHeader: {
    display: "flex",
    justifyContent: "space-between",
    alignItems: "flex-start",
    gap: 12,
  },
  previewActions: {
    display: "flex",
    gap: 8,
    flexShrink: 0,
  },
  editHint: {
    fontSize: 12,
    color: "var(--text-muted)",
    marginTop: 4,
  },
  resetBtn: {
    background: "transparent",
    color: "var(--text-muted)",
    padding: "8px 14px",
    fontSize: 13,
    border: "1px solid var(--border)",
  },
  downloadBtn: {
    background: "var(--surface-hover)",
    color: "var(--text)",
    padding: "8px 14px",
    fontSize: 13,
    border: "1px solid var(--border)",
  },
  stats: {
    display: "flex",
    gap: 16,
    fontSize: 13,
    color: "var(--text-muted)",
    flexWrap: "wrap",
  },
  yamlEditor: {
    flex: 1,
    overflow: "auto",
    resize: "vertical",
    background: "#12121a",
    border: "1px solid var(--border)",
    borderRadius: 8,
    padding: 16,
    fontFamily: "var(--font-mono)",
    fontSize: 13,
    lineHeight: 1.5,
    whiteSpace: "pre",
    wordBreak: "break-word",
    minHeight: 360,
    width: "100%",
    color: "var(--text)",
  },
  placeholder: {
    flex: 1,
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    color: "var(--text-muted)",
    fontSize: 14,
    border: "1px dashed var(--border)",
    borderRadius: 8,
    minHeight: 360,
  },
  error: {
    color: "var(--error)",
    fontSize: 13,
  },
};
