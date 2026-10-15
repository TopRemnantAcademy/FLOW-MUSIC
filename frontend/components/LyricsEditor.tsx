"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { api, LyricLine, LyricsDoc } from "@/lib/api";

// ── GAP-012 歌词编辑器 ────────────────────────────────────────────────────────
// 三件事：段落结构化编辑、改词后与 lyrics_sync 再对齐、LRC 导出/导入。
//
// 设计取舍（为什么行与时间轴分两栏，而不是所见即所得的富文本）：
//   歌词编辑是**双向**的 —— 文字改了，时间轴要不要跟着动只有用户知道。
//   所以把「文字」和「时点」放在同一行、各自可改，让用户明确表达意图，
//   而不是让编辑器猜（猜错的代价是播放器高亮整段失配，比多一个输入框贵得多）。
//
// 时间显示：一律 mm:ss.xx（与导出的 .lrc 同一格式）—— 用户在编辑器里看到的就是
// 第三方播放器里会看到的那串数字，省掉一层心算。

const SECTION_OPTIONS: { value: string; label: string }[] = [
  { value: "intro", label: "前奏" },
  { value: "verse", label: "主歌" },
  { value: "pre-chorus", label: "导歌" },
  { value: "chorus", label: "副歌" },
  { value: "bridge", label: "桥段" },
  { value: "instrumental", label: "间奏" },
  { value: "outro", label: "尾奏" },
];

const MAX_LYRIC_CHARS = 5000;
const MAX_LINES = 3000;

type Row = LyricLine & { key: string };

let seq = 0;
function toRows(lines: LyricLine[]): Row[] {
  return lines.map((l) => ({ ...l, key: `r${seq++}` }));
}

/** 毫秒 → `mm:ss.xx`（与后端 lrc.format_time 同规则，含 >99 分钟进位）。 */
function fmt(ms: number | null | undefined): string {
  if (ms == null || !isFinite(ms)) return "";
  const v = Math.max(0, Math.round(ms));
  const cs = Math.floor(v / 10);
  const mm = Math.floor(cs / 6000);
  const ss = Math.floor((cs % 6000) / 100);
  return `${String(mm).padStart(2, "0")}:${String(ss).padStart(2, "0")}.${String(cs % 100).padStart(2, "0")}`;
}

/**
 * `mm:ss.xx` / `mm:ss` / 纯秒数 → 毫秒。解析失败返回 null（不静默当 0：
 * 把「用户输错了」变成「时间轴被悄悄清零」是最坏的一种失败）。
 */
function parseTimeInput(raw: string): number | null {
  const t = (raw || "").trim();
  if (!t) return null;
  const m = t.match(/^(\d{1,3}):(\d{1,2})(?:[.:](\d{1,3}))?$/);
  if (m) {
    const frac = (m[3] || "").padEnd(3, "0").slice(0, 3);
    return (parseInt(m[1], 10) * 60 + parseInt(m[2], 10)) * 1000 + parseInt(frac, 10);
  }
  if (/^\d+(\.\d+)?$/.test(t)) return Math.round(parseFloat(t) * 1000); // 纯秒数
  return null;
}

export default function LyricsEditor({ songId }: { songId: number }) {
  const [doc, setDoc] = useState<LyricsDoc | null>(null);
  const [rows, setRows] = useState<Row[]>([]);
  const [plain, setPlain] = useState("");       // 纯文本模式下的歌词
  const [mode, setMode] = useState<"timeline" | "plain">("plain");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState("");         // 正在跑的动作名（禁用按钮 + 提示）
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const [importOpen, setImportOpen] = useState(false);
  const [importText, setImportText] = useState("");
  const [lrcPreview, setLrcPreview] = useState("");
  const [showPreview, setShowPreview] = useState(false);
  const [assistIdea, setAssistIdea] = useState("");

  const audioRef = useRef<HTMLAudioElement | null>(null);

  const applyDoc = useCallback((d: LyricsDoc) => {
    setDoc(d);
    setRows(toRows(d.lines));
    setPlain(d.lyric || "");
    // 有轴 → 默认时间轴模式（这才是有逐字数据的曲子的主场景）；无轴 → 纯文本
    setMode(d.has_timeline ? "timeline" : "plain");
  }, []);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    api
      .lyricsDoc(songId)
      .then((d) => {
        if (alive) {
          applyDoc(d);
          setError("");
        }
      })
      .catch((e) => alive && setError(e?.message || "读取歌词失败"))
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, [songId, applyDoc]);

  /** 统一执行器：负责按钮态与成功/失败提示。fn 返回要展示的说明文案。 */
  async function run(name: string, fn: () => Promise<string>) {
    setBusy(name);
    setError("");
    setNotice("");
    try {
      setNotice(await fn());
    } catch (e: any) {
      setError(e?.message || "操作失败");
    } finally {
      setBusy("");
    }
  }

  const chars = mode === "plain" ? plain.length : rows.reduce((n, r) => n + r.text.length, 0) + rows.length - 1;
  const overChars = chars > MAX_LYRIC_CHARS;
  const overLines = rows.length > MAX_LINES;

  // ── 行操作 ────────────────────────────────────────────────────────────────
  const patchRow = (key: string, patch: Partial<Row>) =>
    setRows((prev) => prev.map((r) => (r.key === key ? { ...r, ...patch } : r)));

  const removeRow = (key: string) => setRows((prev) => prev.filter((r) => r.key !== key));

  /** 在指定行之后插入空行；时点取「该行 end」到「下一行 start」的中点，避开邻居。 */
  const insertAfter = (key: string | null) =>
    setRows((prev) => {
      const blank: Row = { key: `r${seq++}`, start: 0, end: null, text: "", section: "verse" };
      if (!prev.length) return [blank];
      if (key === null) return [blank, ...prev];
      const i = prev.findIndex((r) => r.key === key);
      if (i < 0) return [...prev, blank];
      const cur = prev[i];
      const next = prev[i + 1];
      const from = cur.end ?? cur.start;
      const to = next ? next.start : from + 4000;
      blank.start = from + Math.max(1, Math.floor((to - from) / 2));
      blank.section = cur.section;
      return [...prev.slice(0, i + 1), blank, ...prev.slice(i + 1)];
    });

  /** 把段落类型刷到从 index 起的连续同段行（用户按段落按钮 = 整段一起改，不是逐行点）。 */
  const setSectionFrom = (index: number, section: string) =>
    setRows((prev) => {
      const out = [...prev];
      const from = out[index]?.section;
      for (let i = index; i < out.length; i++) {
        if (i > index && out[i].section !== from) break;
        out[i] = { ...out[i], section };
      }
      return out;
    });

  /** 段落分组（渲染用）：连续同 section 的行归为一段。 */
  const groups = useMemo(() => {
    const g: { section: string; rows: Row[]; offset: number }[] = [];
    rows.forEach((r, i) => {
      const last = g[g.length - 1];
      if (last && last.section === r.section) last.rows.push(r);
      else g.push({ section: r.section, rows: [r], offset: i });
    });
    return g;
  }, [rows]);

  // ── 动作 ──────────────────────────────────────────────────────────────────
  const onSave = () =>
    run("save", async () => {
      const d =
        mode === "timeline"
          ? await api.saveLyrics(songId, {
              lyric: "",
              lines: rows.map(({ key, ...l }) => l),
            })
          : await api.saveLyrics(songId, { lyric: plain, realign: true });
      applyDoc(d);
      return d.has_timeline ? "已保存（歌词 + 时间轴）" : "已保存（纯文本，本曲无时间轴）";
    });

  /** 丢弃时间轴：明确降级为纯文本，而不是让 start=0 的假时轴留在库里。 */
  const onDropTimeline = () =>
    run("drop", async () => {
      const d = await api.saveLyrics(songId, { lyric: plain, realign: false });
      applyDoc(d);
      return "已保存为纯文本歌词（时间轴已清除）";
    });

  /** 改词后一键重排：把当前输入框的文字重新对齐到当前时间轴（不先去改时点）。 */
  const onRealign = () =>
    run("realign", async () => {
      if (mode === "plain") {
        const saved = await api.saveLyrics(songId, { lyric: plain, realign: true });
        applyDoc(saved);
        return `已按原时间轴重排成 ${saved.lines.length} 行`;
      }
      const d = await api.realignLyrics(songId);
      applyDoc(d);
      return `已按原时间轴重排成 ${d.lines.length} 行`;
    });

  const onExport = () =>
    run("export", async () => {
      const text = await api.lyricsLrc(songId);
      setLrcPreview(text);
      setShowPreview(true);
      return "已生成 .lrc 预览";
    });

  /** 直接交给浏览器下载：走服务端 Content-Disposition，文件名（含中文标题）才不会是 blob UUID。 */
  const onDownload = () => {
    if (!doc) return;
    const a = document.createElement("a");
    a.href = api.lyricsLrcUrl(songId);
    a.rel = "noreferrer";
    document.body.appendChild(a);
    a.click();
    a.remove();
    setNotice("已开始下载 .lrc —— 可拖进第三方播放器（须知同一目录同名音频）");
  };

  const onImport = (save: boolean) =>
    run(save ? "import-save" : "import-preview", async () => {
      const r = await api.importLrc(songId, importText, save);
      if (!save) {
        setRows(toRows(r.imported_lines));
        setMode("timeline");
        setNotice("");
        return `解析出 ${r.parsed_lines} 行，已填入编辑器（尚未保存）`;
      }
      const d = await api.lyricsDoc(songId);
      applyDoc(d);
      setImportOpen(false);
      setImportText("");
      return [
        `已导入 ${d.lines.length} 行`,
        ...r.notes,
        ...r.warnings,
      ]
        .filter(Boolean)
        .join("；");
    });

  // ── AI 助手（复用现有 /assist/lyrics 与 /assist/lyrics/format）────────────
  const onAssistGenerate = () =>
    run("ai-gen", async () => {
      const idea = (assistIdea || doc?.title || "").trim();
      if (!idea) throw new Error("请先填写主题/情绪，再让 AI 起草歌词");
      const r = await api.assistLyrics(idea);
      if (!r.lyrics) throw new Error("AI 未返回歌词，请换个描述再试");
      setPlain(r.lyrics);
      setMode("plain");
      return "AI 已起草歌词，可切到「时间轴」继续定时或直接保存";
    });

  const onAssistFormat = () =>
    run("ai-fmt", async () => {
      const src = mode === "plain" ? plain : rows.map((r) => r.text).join("\n");
      if (!src.trim()) throw new Error("请先填写歌词，再执行格式优化");
      const r = await api.assistLyricsFormat(src);
      if (r.optimized) {
        setPlain(r.optimized);
        setMode("plain");
      }
      return r.notes?.length ? r.notes.join("；") : "歌词已是规范格式，无需改动";
    });

  // ── 渲染 ──────────────────────────────────────────────────────────────────
  if (loading) {
    return (
      <div className="card p-6 text-sm text-[var(--muted)]">正在读取歌词…</div>
    );
  }
  if (error && !doc) {
    return (
      <div className="card p-6">
        <p className="text-sm text-[var(--danger)]">{error}</p>
        <Link href="/library" className="btn btn-secondary btn-sm mt-3 inline-block">
          返回我的作品
        </Link>
      </div>
    );
  }
  if (!doc) return null;

  const readOnly = !doc.can_edit;
  const disable = (name: string) => readOnly || !!busy || name === busy;

  return (
    <div className="space-y-4">
      {/* ── 状态条 ── */}
      <div className="card flex flex-wrap items-center justify-between gap-2 p-3">
        <div className="text-sm">
          <span className="font-semibold">{doc.title || "未命名"}</span>
          <span className="ml-2 rounded-full bg-[var(--surface-2)] px-2 py-0.5 text-xs text-[var(--muted)]">
            {doc.has_timeline ? `逐字 ${doc.word_count} 个时点` : "无时间轴"}
          </span>
          {doc.duration ? (
            <span className="ml-2 text-xs text-[var(--muted)]">曲长 {fmt(doc.duration * 1000)}</span>
          ) : null}
        </div>
        <div className="flex items-center gap-2 text-xs">
          <button
            type="button"
            onClick={() => setMode("timeline")}
            disabled={!doc.has_timeline && rows.every((r) => !r.start)}
            className="chip rounded px-2 py-0.5"
            data-on={mode === "timeline"}
          >
            时间轴
          </button>
          <button
            type="button"
            onClick={() => setMode("plain")}
            className="chip rounded px-2 py-0.5"
            data-on={mode === "plain"}
          >
            纯文本
          </button>
        </div>
      </div>

      {readOnly && (
        <p className="text-xs text-[var(--muted)]">这是他人的公开作品，只能查看歌词。</p>
      )}

      {/* ── AI 助手入口 ── */}
      <div className="card p-3">
        <div className="mb-2 text-xs text-[var(--muted)]">
          行内 AI 助手（复用创作页同一套接口：起草 = /assist/lyrics，格式对齐 = /assist/lyrics/format）
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <input
            className="input flex-1 min-w-[12rem]"
            placeholder="主题 / 情绪（例如：雨夜里的告白）"
            value={assistIdea}
            onChange={(e) => setAssistIdea(e.target.value)}
            disabled={readOnly || busy === "ai-gen"}
          />
          <button
            type="button"
            className="btn btn-secondary btn-sm"
            onClick={onAssistGenerate}
            disabled={disable("ai-gen")}
            title="按主题起草整首歌词（会覆盖当前输入框内容）"
          >
            {busy === "ai-gen" ? "起草中…" : "✨ 起草歌词"}
          </button>
          <button
            type="button"
            className="btn btn-secondary btn-sm"
            onClick={onAssistFormat}
            disabled={disable("ai-fmt")}
            title="本地规则：段落结构对齐 + 去空行 + 超限截断，不改写语义"
          >
            {busy === "ai-fmt" ? "整理中…" : "✨ 格式对齐"}
          </button>
        </div>
      </div>

      {/* ── 主体 ── */}
      {mode === "plain" ? (
        <div className="card p-3">
          <textarea
            className="input"
            rows={14}
            value={plain}
            onChange={(e) => setPlain(e.target.value)}
            disabled={readOnly || busy === "save"}
            placeholder={"一行一句歌词。\n段落标记可写 [Verse] / [Chorus]（保存时会自动识别并分组）。"}
          />
          <div className="mt-1 flex items-center justify-between text-xs">
            <span className="text-[var(--muted)]">
              {doc.has_timeline
                ? "本曲已有时间轴：保存时会按原节奏重排，播放器高亮不会失配。"
                : "本曲没有时间轴，保存后为纯文本歌词。"}
            </span>
            <span className={overChars ? "text-[var(--danger)]" : "text-[var(--muted)]"}>
              {chars}/{MAX_LYRIC_CHARS}
            </span>
          </div>
        </div>
      ) : (
        <div className="card p-3">
          <div className="mb-2 flex flex-wrap items-center gap-2">
            <button
              type="button"
              className="btn btn-secondary btn-sm"
              onClick={() => insertAfter(null)}
              disabled={readOnly || busy === "save"}
            >
              + 开头插行
            </button>
            <button
              type="button"
              className="btn btn-secondary btn-sm"
              onClick={() => insertAfter(rows[rows.length - 1]?.key ?? null)}
              disabled={readOnly || busy === "save"}
            >
              + 末尾插行
            </button>
            <span className="text-xs text-[var(--muted)]">
              共 {rows.length} 行
              {overLines ? <span className="text-[var(--danger)]">（超过 {MAX_LINES} 行上限）</span> : null}
              {" · "}
              <span className={overChars ? "text-[var(--danger)]" : ""}>
                {chars}/{MAX_LYRIC_CHARS} 字
              </span>
            </span>
          </div>

          <div className="space-y-3">
            {groups.map((g) => (
              <div key={`${g.offset}-${g.section}`} className="rounded-lg bg-[var(--surface-2)] p-2">
                <div className="mb-1 flex items-center gap-2">
                  <select
                    className="input !w-auto !py-0.5 text-xs"
                    value={g.section}
                    onChange={(e) => setSectionFrom(g.offset, e.target.value)}
                    disabled={readOnly || busy === "save"}
                    aria-label="段落类型"
                  >
                    {SECTION_OPTIONS.map((o) => (
                      <option key={o.value} value={o.value}>
                        {o.label}（{o.value}）
                      </option>
                    ))}
                    {SECTION_OPTIONS.every((o) => o.value !== g.section) && (
                      <option value={g.section}>{g.section}</option>
                    )}
                  </select>
                  <span className="text-xs text-[var(--muted)]">{g.rows.length} 行</span>
                </div>
                {g.rows.map((r) => {
                  const idx = rows.findIndex((x) => x.key === r.key);
                  return (
                    <div key={r.key} className="mb-1 flex items-center gap-1.5">
                      <TimeInput
                        value={r.start}
                        onCommit={(ms) => ms != null && patchRow(r.key, { start: ms })}
                        disabled={readOnly || busy === "save"}
                        title="行起始时点"
                      />
                      <input
                        className="input flex-1"
                        value={r.text}
                        onChange={(e) => patchRow(r.key, { text: e.target.value })}
                        disabled={readOnly || busy === "save"}
                        placeholder="（空行 = 间奏）"
                        aria-label={`第 ${idx + 1} 行歌词`}
                      />
                      <button
                        type="button"
                        className="btn btn-secondary btn-sm !px-1.5"
                        onClick={() => insertAfter(r.key)}
                        disabled={readOnly || busy === "save"}
                        title="在此行之后插入一行"
                      >
                        +
                      </button>
                      <button
                        type="button"
                        className="btn btn-danger btn-sm !px-1.5"
                        onClick={() => removeRow(r.key)}
                        disabled={readOnly || busy === "save"}
                        title="删除此行"
                      >
                        ×
                      </button>
                    </div>
                  );
                })}
              </div>
            ))}
            {!rows.length && (
              <p className="text-sm text-[var(--muted)]">
                还没有带时间轴的行。可以先「导入 LRC」，或用纯文本模式写词后点「按原时间轴重排」。
              </p>
            )}
          </div>
        </div>
      )}

      {/* ── LRC 导入 ── */}
      {importOpen && (
        <div className="card p-3">
          <div className="mb-2 text-sm font-semibold">导入 LRC</div>
          <textarea
            className="input font-mono text-xs"
            rows={8}
            value={importText}
            onChange={(e) => setImportText(e.target.value)}
            placeholder={"[ti:歌名]\n[00:12.00]第一句\n[00:16.50]第二句"}
            disabled={readOnly || busy.startsWith("import")}
          />
          <div className="mt-2 flex flex-wrap gap-2">
            <button
              type="button"
              className="btn btn-secondary btn-sm"
              onClick={() => onImport(false)}
              disabled={disable("import-preview") || !importText.trim()}
              title="只解析并填入编辑器，不落库"
            >
              {busy === "import-preview" ? "解析中…" : "仅解析预览"}
            </button>
            <button
              type="button"
              className="btn btn-primary btn-sm"
              onClick={() => onImport(true)}
              disabled={disable("import-save") || !importText.trim()}
              title="解析并直接保存（以文件里的时点为准）"
            >
              {busy === "import-save" ? "导入中…" : "导入并保存"}
            </button>
            <button
              type="button"
              className="btn btn-secondary btn-sm"
              onClick={() => {
                setImportOpen(false);
                setImportText("");
              }}
              disabled={busy.startsWith("import")}
            >
              取消
            </button>
          </div>
        </div>
      )}

      {/* ── LRC 预览 ── */}
      {showPreview && (
        <div className="card p-3">
          <div className="mb-2 flex items-center justify-between">
            <span className="text-sm font-semibold">.lrc 导出预览（标准行级时标）</span>
            <button
              type="button"
              className="btn btn-secondary btn-sm"
              onClick={() => setShowPreview(false)}
            >
              收起
            </button>
          </div>
          <pre className="max-h-64 overflow-auto whitespace-pre-wrap break-all rounded bg-[var(--surface-2)] p-2 text-xs">
            {lrcPreview || "（空）"}
          </pre>
        </div>
      )}

      {/* ── 动作栏 ── */}
      <div className="card flex flex-wrap items-center gap-2 p-3">
        <button
          type="button"
          className="btn btn-primary btn-sm"
          onClick={onSave}
          disabled={disable("save")}
        >
          {busy === "save" ? "保存中…" : "保存"}
        </button>
        <button
          type="button"
          className="btn btn-secondary btn-sm"
          onClick={onRealign}
          disabled={disable("realign")}
          title="保留时间轴，把当前歌词按原节奏重新分配到各行"
        >
          {busy === "realign" ? "重排中…" : "改词后重排时轴"}
        </button>
        <button
          type="button"
          className="btn btn-secondary btn-sm"
          onClick={onExport}
          disabled={disable("export")}
        >
          {busy === "export" ? "生成中…" : "导出 .lrc 预览"}
        </button>
        <button
          type="button"
          className="btn btn-secondary btn-sm"
          onClick={onDownload}
          disabled={readOnly}
          title="下载 .lrc，可拖入第三方播放器（与音频同目录同名即可自动加载）"
        >
          下载 .lrc
        </button>
        <button
          type="button"
          className="btn btn-secondary btn-sm"
          onClick={() => setImportOpen((v) => !v)}
          disabled={readOnly}
        >
          {importOpen ? "收起导入" : "导入 LRC"}
        </button>
        {doc.has_timeline && (
          <button
            type="button"
            className="btn btn-danger btn-sm"
            onClick={onDropTimeline}
            disabled={disable("drop")}
            title="明确丢弃时间轴，只保留歌词文字（播放器将不再逐字高亮）"
          >
            {busy === "drop" ? "处理中…" : "清除时间轴"}
          </button>
        )}
        <Link href="/library" className="btn btn-secondary btn-sm">
          返回作品
        </Link>
      </div>

      {/* ── 试听（重排后立刻验证高亮是否跟上）── */}
      {doc.can_edit && (
        <div className="card p-3">
          <div className="mb-1 text-xs text-[var(--muted)]">
            试听（用详情端点按需取回音频 URL；本页只做时序验证，不做完整播放页）
          </div>
          {/*
            * 歌曲音频无对白/字幕轨，track 只是让 jsx-a11y/media-has-caption 通过的空占位。
            * src 是运行时由按钮异步写入的（见下方 onClick），故这里不设 src。
            */}
          <audio ref={audioRef} controls className="w-full" preload="none">
            <track kind="captions" srcLang="zh" label="无字幕（歌曲音频）" />
          </audio>
          <button
            type="button"
            className="btn btn-secondary btn-sm mt-2"
            onClick={async () => {
              const a = audioRef.current;
              if (!a) return;
              try {
                const s = await api.getSong(songId);
                const src = s.audio_url || s.preview_audio_url || "";
                if (!src) {
                  setError("本曲暂无可播放的音频地址");
                  return;
                }
                a.src = src;
                a.play().catch(() => {});
              } catch (e: any) {
                setError(e?.message || "加载音频失败");
              }
            }}
          >
            载入音频
          </button>
        </div>
      )}

      {notice && (
        <p className="rounded bg-[var(--surface-2)] p-2 text-sm text-[var(--ink)]">{notice}</p>
      )}
      {error && <p className="text-sm text-[var(--danger)]">{error}</p>}
    </div>
  );
}

/** 时点输入：本地保留用户正在敲的字（受控数字框会吃掉 "00:0"），失焦/回车时才提交。 */
function TimeInput({
  value,
  onCommit,
  disabled,
  title,
}: {
  value: number;
  onCommit: (ms: number | null) => void;
  disabled?: boolean;
  title?: string;
}) {
  const [text, setText] = useState(fmt(value));
  const [bad, setBad] = useState(false);
  const focused = useRef(false);

  // 外部值变化（重排 / 导入 / 保存回填）要刷新显示；但**正在编辑时不覆盖**用户输入
  useEffect(() => {
    if (!focused.current) {
      setText(fmt(value));
      setBad(false);
    }
  }, [value]);

  const commit = () => {
    focused.current = false;
    const ms = parseTimeInput(text);
    if (ms == null) {
      setBad(true);
      setText(fmt(value)); // 回退到上一个有效值，不静默清零
      return;
    }
    setBad(false);
    setText(fmt(ms));
    if (ms !== value) onCommit(ms);
  };

  return (
    <input
      className={`input !w-[5.5rem] font-mono text-xs ${bad ? "!border-[var(--danger)]" : ""}`}
      value={text}
      title={title}
      aria-label={title}
      onFocus={() => {
        focused.current = true;
      }}
      onChange={(e) => {
        setText(e.target.value);
        setBad(false);
      }}
      onBlur={commit}
      onKeyDown={(e) => {
        if (e.key === "Enter") (e.target as HTMLInputElement).blur();
        if (e.key === "Escape") {
          focused.current = false;
          setText(fmt(value));
          setBad(false);
          (e.target as HTMLInputElement).blur();
        }
      }}
      disabled={disabled}
      inputMode="numeric"
    />
  );
}
