"use client";

import Link from "next/link";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useId,
  useLayoutEffect,
  useRef,
  useState,
} from "react";
import { api, SongView } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { trackGenerate } from "@/lib/eventReporter";
import Player from "./Player";

const POLL_INTERVAL_MS = 5000;
// 60 次 × 5s = 5 分钟。此前 40 次(3分20秒)对 Mureka 常常不够，用户被迫手动点刷新。
const MAX_POLLS = 60;

// Mureka 官方上限（app/services/prompt_assist.py 同源常量）
const MAX_LYRIC_CHARS = 5000;
const MAX_PROMPT_CHARS = 1024;

const DRAFT_KEY = "create_draft_v1";

// ── 风格标签分组 ────────────────────────────────────────────────────────────
// 风格标签：UI 显示全中文；提交前经 styleToEngine() 译回英文描述符（生成引擎对英文标签识别最稳）。
const STYLE_GROUPS: { label: string; items: { cn: string; en: string }[] }[] = [
  {
    label: "曲风",
    items: [
      { cn: "福音", en: "gospel" }, { cn: "流行", en: "pop" }, { cn: "摇滚", en: "rock" },
      { cn: "爵士", en: "jazz" }, { cn: "节奏布鲁斯", en: "r&b" }, { cn: "电子舞曲", en: "edm" },
      { cn: "民谣", en: "folk" }, { cn: "拉丁", en: "latin" }, { cn: "韩语流行", en: "k-pop" },
      { cn: "日语流行", en: "j-pop" }, { cn: "浩室", en: "house" }, { cn: "氛围", en: "ambient" },
      { cn: "低保真", en: "lo-fi" },
    ],
  },
  {
    label: "情绪",
    items: [
      { cn: "温暖", en: "warm" }, { cn: "轻柔", en: "gentle" }, { cn: "激昂", en: "passionate" },
      { cn: "忧郁", en: "melancholic" }, { cn: "治愈", en: "healing" }, { cn: "欢快", en: "upbeat" },
    ],
  },
  { label: "速度", items: [{ cn: "慢速", en: "slow" }, { cn: "快节奏", en: "upbeat" }] },
  {
    label: "乐器",
    items: [
      { cn: "钢琴", en: "piano" }, { cn: "吉他", en: "guitar" }, { cn: "弦乐", en: "strings" },
      { cn: "鼓", en: "drums" }, { cn: "合成器", en: "synth" },
    ],
  },
  { label: "人声", items: [{ cn: "男声", en: "male vocal" }, { cn: "女声", en: "female vocal" }] },
];

const CN_TO_EN: Record<string, string> = Object.fromEntries(
  STYLE_GROUPS.flatMap((g) => g.items.map((it) => [it.cn, it.en])),
);
const EN_TO_CN: Record<string, string> = Object.fromEntries(
  STYLE_GROUPS.flatMap((g) => g.items.map((it) => [it.en.toLowerCase(), it.cn])),
);

/** UI 中文风格串 → 引擎英文描述符；未识别的词原样透传（兼容自由输入与旧草稿英文）。 */
export function styleToEngine(raw: string): string {
  return raw
    .split(/[,，、]/)
    .map((x) => x.trim())
    .filter(Boolean)
    .map((x) => CN_TO_EN[x] ?? x)
    .join(", ");
}

/** 引擎/自动优化返回的英文描述符 → UI 中文（未识别的词原样透传）。 */
function engineToStyle(raw: string): string {
  return raw
    .split(/[,，、]/)
    .map((x) => x.trim())
    .filter(Boolean)
    .map((x) => EN_TO_CN[x.toLowerCase()] ?? x)
    .join("，");
}

// 灵感创作（easy-generate）官方风格枚举：中文标签 → 提交时译回英文枚举。
// 与后端 mureka.MurekaProvider.EASY_STYLES 对齐（gospel / pop / rock / jazz / r&b / edm /
// folk / latin / k-pop / j-pop / house / ambient / lo-fi）。非法值后端会 400，故前端只给枚举。
const EASY_STYLES: { cn: string; en: string }[] = [
  { cn: "福音", en: "gospel" },
  { cn: "流行", en: "pop" },
  { cn: "摇滚", en: "rock" },
  { cn: "爵士", en: "jazz" },
  { cn: "节奏布鲁斯", en: "r&b" },
  { cn: "电子舞曲", en: "edm" },
  { cn: "民谣", en: "folk" },
  { cn: "拉丁", en: "latin" },
  { cn: "韩语流行", en: "k-pop" },
  { cn: "日语流行", en: "j-pop" },
  { cn: "浩室", en: "house" },
  { cn: "氛围", en: "ambient" },
  { cn: "低保真", en: "lo-fi" },
];
const EASY_STYLES_INV: Record<string, string> = Object.fromEntries(
  EASY_STYLES.map((it) => [it.cn, it.en]),
);

// 创作模式：普通创作 / 纯器乐 / 灵感创作，分别对应后端 generate / instrumental / easy-generate 端点。
const MODES: { key: "normal" | "instrumental" | "easy"; label: string }[] = [
  { key: "normal", label: "普通创作" },
  { key: "instrumental", label: "纯器乐" },
  { key: "easy", label: "灵感创作" },
];

// GAP-010 下载格式（**下载侧**可选）。
// ⚠️ Mureka 生成入参无格式/码率控制（官方 spec 实证，见后端 orchestrator/mureka.py 头部），
// 上游响应同时回传 mp3/wav/flac 三种链接 → 本控件选择的是「下载时取哪种」，不是「生成质量」。
// 无损（wav/flac）需订阅（后端 can_download；未订阅选了会被 403）。
const AUDIO_FORMATS: { key: string; label: string; lossless: boolean }[] = [
  { key: "mp3", label: "MP3", lossless: false },
  { key: "wav", label: "WAV 无损", lossless: true },
  { key: "flac", label: "FLAC 无损", lossless: true },
];

// 灵感：一键填充整套参数（降低"空白页恐惧"）
const INSPIRATIONS: { name: string; style: string; prompt: string }[] = [
  { name: "清晨敬拜", style: "福音，慢速，钢琴，女声", prompt: "清晨的微光里，安静地献上感谢" },
  { name: "十架回声", style: "福音，弦乐，激昂，男声", prompt: "十架的爱，胜过一切黑暗" },
  { name: "安慰之夜", style: "福音，氛围，轻柔，钢琴，女声", prompt: "在孤单的夜里被怀抱的平安" },
  { name: "盼望进行曲", style: "福音，快节奏，鼓，男声", prompt: "带着盼望向前奔跑的喜乐" },
];

// 歌词段落标签：按钮显示中文，插入的是引擎识别的英文结构标记（生成的歌词即用此结构）
// 注：UI 快捷按钮不主动推「桥段 / [Bridge]」（用户要求），但后端识别/生成模板仍保留 [Bridge]。
const SECTION_TAGS: { label: string; tag: string }[] = [
  { label: "主歌", tag: "[Verse]" },
  { label: "导歌", tag: "[Pre-Chorus]" },
  { label: "副歌", tag: "[Chorus]" },
  { label: "尾奏", tag: "[Outro]" },
];

type AssistKind = "" | "lyrics-gen" | "lyrics-fmt" | "style" | "prompt" | "title";

export default function CreateForm() {
  const { isAuthed } = useAuth();
  // Field 渲染出的 <label htmlFor> 指向的 id 通过 context 下发到各受控控件
  const fieldCtx = useContext(FieldIdContext);
  const fieldProps = {
    id: fieldCtx.id,
    "aria-describedby": fieldCtx.hintId,
  } as const;

  const [style, setStyle] = useState("");
  const [prompt, setPrompt] = useState("");
  const [lyric, setLyric] = useState("");
  const [title, setTitle] = useState("");
  const [vocal, setVocal] = useState("");          // 人声性别 male / female（仅普通创作）
  const [mode, setMode] = useState<"normal" | "instrumental" | "easy">("normal");
  const instrumental = mode === "instrumental";    // 派生：纯器乐模式
  const [vocalId, setVocalId] = useState("");      // 音色克隆 ID（Mureka /v1/vocal/clone 产出）
  const [vocalCloning, setVocalCloning] = useState(false);
  const [vocalCloneError, setVocalCloneError] = useState("");
  const [easyStyles, setEasyStyles] = useState<string[]>([]); // 灵感创作：官方风格枚举（中文标签）

  // GAP-010：下载格式偏好 + 订阅权益（决定无损是否可选）。
  const [audioFormat, setAudioFormat] = useState("mp3");
  const [canDownload, setCanDownload] = useState(false);
  const [song, setSong] = useState<SongView | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [timedOut, setTimedOut] = useState(false);
  const [elapsed, setElapsed] = useState(0); // 生成已等待秒数

  const [assisting, setAssisting] = useState<AssistKind>("");
  const [assistNote, setAssistNote] = useState("");

  const [showAdvanced, setShowAdvanced] = useState(false);
  const [showInspiration, setShowInspiration] = useState(false);
  const [draftRestored, setDraftRestored] = useState(false);

  const canceledRef = useRef(false);
  const lyricRef = useRef<HTMLTextAreaElement>(null);

  const busy = assisting !== "";

  // ── 草稿：自动保存 / 恢复（防止刷新丢输入）─────────────────────────────────
  useEffect(() => {
    const raw = localStorage.getItem(DRAFT_KEY);
    if (!raw) return;
    try {
      const d = JSON.parse(raw);
      if (d.style) setStyle(d.style);
      if (d.prompt) setPrompt(d.prompt);
      if (d.lyric) setLyric(d.lyric);
      if (d.title) setTitle(d.title);
      if (d.vocal) setVocal(d.vocal);
      if (d.mode === "instrumental" || d.mode === "easy") setMode(d.mode);
      if (Array.isArray(d.easyStyles)) setEasyStyles(d.easyStyles);
      if (typeof d.audioFormat === "string") setAudioFormat(d.audioFormat);
      if (d.style || d.prompt || d.lyric || d.title) setDraftRestored(true);
    } catch {
      localStorage.removeItem(DRAFT_KEY);
    }
  }, []);

  useEffect(() => {
    // 生成中/已有结果时不覆盖草稿（避免把刚完成的作品参数写回去）
    if (loading || song) return;
    const t = setTimeout(() => {
      localStorage.setItem(
        DRAFT_KEY,
        JSON.stringify({ style, prompt, lyric, title, vocal, mode, easyStyles, audioFormat }),
      );
    }, 500);
    return () => clearTimeout(t);
  }, [style, prompt, lyric, title, vocal, instrumental, loading, song, audioFormat]);

  function clearDraft() {
    localStorage.removeItem(DRAFT_KEY);
    setDraftRestored(false);
  }

  useEffect(() => {
    return () => {
      canceledRef.current = true;
    };
  }, []);

  // GAP-010：订阅权益（无损格式解锁条件）。登录态/订阅变更时刷新。
  // 未登录 → 默认不可下载（fail-closed，与后端 403 一致）。
  useEffect(() => {
    let alive = true;
    if (!isAuthed) {
      setCanDownload(false);
      return;
    }
    api
      .meBilling()
      .then((b) => {
        if (alive) setCanDownload(!!b.tier_key && b.tier_key !== "free");
      })
      .catch(() => {
        if (alive) setCanDownload(false);
      });
    return () => {
      alive = false;
    };
  }, [isAuthed]);

  // ── 生成计时 ──────────────────────────────────────────────────────────────
  useEffect(() => {
    if (!loading) {
      setElapsed(0);
      return;
    }
    const id = setInterval(() => setElapsed((e) => e + 1), 1000);
    return () => clearInterval(id);
  }, [loading]);

  async function poll(songId: number): Promise<SongView | null> {
    let s: SongView | null = null;
    let lastStatus: string | null | undefined = null;
    for (let i = 0; i < MAX_POLLS; i++) {
      if (canceledRef.current) return null;
      await new Promise((r) => setTimeout(r, POLL_INTERVAL_MS));
      if (canceledRef.current) return null;
      s = await api.getSong(songId);
      // O5：仅在状态变化时重渲染，避免每 5s 无条件 setSong 引发的整页重绘
      if (s.status !== lastStatus) {
        lastStatus = s.status;
        setSong(s);
      }
      if (s.status === "completed" || s.status === "failed") return s;
    }
    return s;
  }

  // ── 标签增删 ──────────────────────────────────────────────────────────────
  const toggleTag = useCallback((tag: string) => {
    setStyle((prev) => {
      // 标签可能带空格（"男声"/旧草稿英文 "female vocal"）→ 按中英文逗号切，不按空格切
      const parts = prev.split(/[,，、]/).map((x) => x.trim()).filter(Boolean);
      const idx = parts.findIndex((x) => x.toLowerCase() === tag.toLowerCase());
      if (idx >= 0) return parts.filter((_, i) => i !== idx).join("，");
      return [...parts, tag].join("，");
    });
  }, []);

  /** 人声下拉与风格标签同步：避免出现「下拉选女声 + 标签里还留着男声」的矛盾 */
  function changeVocal(g: string) {
    setVocal(g);
    setStyle((prev) => {
      const parts = prev
        .split(/[,，、]/)
        .map((x) => x.trim())
        .filter(Boolean)
        .filter((x) => !/^(male\s+vocal|female\s+vocal|男声|女声)$/i.test(x));
      if (g) parts.push(g === "male" ? "男声" : "女声");
      return parts.join("，");
    });
  }

  /** 切换创作模式：离开普通创作时清掉仅普通模式有效的字段（歌词 / 人声性别），避免误提交。 */
  function changeMode(m: "normal" | "instrumental" | "easy") {
    setMode(m);
    if (m !== "normal") {
      setLyric("");
      setVocal("");
    }
  }

  /** 灵感创作：官方风格标签多选切换（中文标签 → 提交时译回英文枚举）。 */
  function toggleEasyStyle(cn: string) {
    setEasyStyles((prev) => (prev.includes(cn) ? prev.filter((x) => x !== cn) : [...prev, cn]));
  }

  /** 音色克隆：上传 mp3/m4a → 调 /api/songs/vocal-clone → 拿到 vocal_id 写回状态。 */
  async function onCloneVocal(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    e.target.value = ""; // 允许重复选择同一文件
    if (!file) return;
    setVocalCloning(true);
    setVocalCloneError("");
    try {
      const r = await api.vocalClone(file);
      setVocalId(r.vocal_id);
    } catch (err: any) {
      setVocalCloneError(err?.message || "音色克隆失败");
    } finally {
      setVocalCloning(false);
    }
  }

  function clearVocal() {
    setVocalId("");
  }

  // ── 歌词段落插入（在光标处）──────────────────────────────────────────────
  function insertSection(tag: string) {
    const el = lyricRef.current;
    const text = tag + "\n";
    if (!el) {
      setLyric((p) => (p ? p.replace(/\s*$/, "") + "\n\n" + text : text));
      return;
    }
    const start = el.selectionStart ?? lyric.length;
    const end = el.selectionEnd ?? lyric.length;
    const next = lyric.slice(0, start) + text + lyric.slice(end);
    setLyric(next);
    requestAnimationFrame(() => {
      el.focus();
      const pos = start + text.length;
      el.setSelectionRange(pos, pos);
    });
  }

  // ── AI 助手 ───────────────────────────────────────────────────────────────
  /** 统一的自动助手执行器：负责按钮态与成功/失败提示，fn 只返回要展示的说明文案。 */
  async function runAssist(kind: Exclude<AssistKind, "">, fn: () => Promise<string>) {
    setAssisting(kind);
    setError("");
    setAssistNote("");
    try {
      setAssistNote(await fn());
    } catch (e: any) {
      setError(e?.message || "操作失败");
    } finally {
      setAssisting("");
    }
  }

  async function onGenerateLyrics() {
    const idea = (prompt || style || "").trim();
    if (!idea) {
      setError("请先填写「主题 / 情绪」，才能据此自动生成歌词");
      return;
    }
    await runAssist("lyrics-gen", async () => {
      const r = await api.assistLyrics(idea);
      if (r.lyrics) setLyric(r.lyrics);
      if (r.title) setTitle(r.title);
      return r.lyrics
        ? "已自动生成歌词与标题"
        : "自动生成未返回歌词，请换个描述再试";
    });
  }

  async function onFormatLyrics() {
    if (!lyric.trim()) {
      setError("请先填写歌词，再执行优化");
      return;
    }
    await runAssist("lyrics-fmt", async () => {
      const r = await api.assistLyricsFormat(lyric);
      if (r.optimized) setLyric(r.optimized);
      return r.notes?.length ? r.notes.join("；") : "歌词已是规范格式，无需改动";
    });
  }

  async function onOptimizeStyle() {
    const hasLyrics = lyric.trim().length > 0;
    const base = style.trim() || prompt.trim();
    if (!base && !hasLyrics) {
      setError("请先填写风格、歌词或主题，才能生成/优化风格");
      return;
    }
    await runAssist("style", async () => {
      // 有歌词时把歌词一并传给后端，让 AI 基于歌词情绪/主题提炼风格
      // （行业「留空由模型隐式推断风格」的显式化、可编辑版本）
      const r = await api.assistStyle(base, { lyrics: hasLyrics ? lyric : "" });
      // 优化器返回英文规范描述符 → 译回中文再回填（UI 全中文规则）
      if (r.optimized) setStyle(engineToStyle(r.optimized));
      return r.notes?.length
        ? r.notes.join("；")
        : hasLyrics
          ? "已根据歌词生成风格标签"
          : "风格标签已是规范格式";
    });
  }

  async function onOptimizePrompt() {
    if (!prompt.trim()) {
      setError("请先填写主题 / 情绪");
      return;
    }
    await runAssist("prompt", async () => {
      // mode=prompt：AI 把主题/情绪扩写成中文歌意描述，直接回填（非标签，无需翻译）
      const r = await api.assistStyle(prompt, { mode: "prompt" });
      if (r.optimized) setPrompt(r.optimized);
      return r.notes?.length ? r.notes.join("；") : "主题描述已是规范格式";
    });
  }

  /** 标题一键取名：AI 生成接口会连标题一起返回，这里只取标题，不覆盖用户已有的歌词。 */
  async function onSuggestTitle() {
    const idea = (prompt || style || title || "").trim();
    if (!idea) {
      setError("请先填写「主题 / 情绪」或风格标签，才能自动取名");
      return;
    }
    await runAssist("title", async () => {
      const r = await api.assistLyrics(idea);
      if (r.title) setTitle(r.title);
      return r.title ? `已取名：${r.title}` : "自动取名未返回合适的标题，请换个描述再试";
    });
  }

  function applyInspiration(ins: (typeof INSPIRATIONS)[number]) {
    setStyle(ins.style);
    setPrompt(ins.prompt);
    setAssistNote(`已套用灵感「${ins.name}」，可继续微调`);
    setError("");
  }

  function resetForm() {
    setStyle("");
    setPrompt("");
    setLyric("");
    setTitle("");
    setVocal("");
    setMode("normal");
    setEasyStyles([]);
    setAudioFormat("mp3");
    setVocalId("");
    setVocalCloneError("");
    setSong(null);
    setError("");
    setAssistNote("");
    setTimedOut(false);
    clearDraft();
  }

  // ── 提交 ──────────────────────────────────────────────────────────────────
  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!isAuthed) {
      setError("请先在右上角登录后再生成（作品会保存到你的账户）");
      return;
    }
    trackGenerate();
    await runGenerate();
  }

  /** 实际生成流程（抽为独立函数，供失败态的「重新生成」按钮复用）。 */
  async function runGenerate() {
    // 普通创作：风格 + 人声 + 主题 合计不得超过引擎上限 1024 字。
    // 此前只标红不拦截，超长会被后端静默截断 → 生成与用户预期不符的歌。现明确拦截并提示。
    if (mode === "normal" && promptTotal > MAX_PROMPT_CHARS) {
      setError(
        `风格 + 人声 + 主题 合计 ${promptTotal} 字，超过 ${MAX_PROMPT_CHARS} 字上限，请精简后再生成`,
      );
      return;
    }
    canceledRef.current = false;
    setLoading(true);
    setError("");
    setSong(null);
    setTimedOut(false);
    try {
      let songId: number;
      if (mode === "instrumental") {
        // 纯器乐：Mureka 专用端点，prompt-only（无歌词、无风格标签、无人声）。
        if (!prompt.trim()) {
          setLoading(false);
          setError("请填写描述（纯器乐也需要主题 / 情绪描述）");
          return;
        }
        const r = await api.instrumental({
          prompt: prompt.trim(),
          title: title.trim() || undefined,
          audio_format: audioFormat,
        });
        songId = r.song_id;
      } else if (mode === "easy") {
        // 灵感创作：Prompt to Song，无歌词；可叠加官方风格枚举 + 克隆音色。
        if (!prompt.trim()) {
          setLoading(false);
          setError("请填写描述（灵感创作 = Prompt to Song）");
          return;
        }
        const stylesEn = easyStyles.map((cn) => EASY_STYLES_INV[cn]).filter(Boolean);
        const r = await api.easyGenerate({
          prompt: prompt.trim(),
          styles: stylesEn.length ? stylesEn : undefined,
          title: title.trim() || undefined,
          vocal_id: vocalId || undefined,
          audio_format: audioFormat,
        });
        songId = r.song_id;
      } else {
        // 普通创作：风格留空时按歌词 / 主题自动生成（透明、可改），再提交
        let finalStyle = style.trim();
        if (!finalStyle) {
          const basis = lyric.trim() || prompt.trim();
          if (basis) {
            try {
              const rr = await api.assistStyle(basis, { lyrics: lyric.trim() ? lyric : "" });
              if (rr.optimized) {
                finalStyle = engineToStyle(rr.optimized);
                setStyle(finalStyle);
              }
            } catch {
              /* 推导失败则继续，后端会按歌词自行推断 */
            }
          }
        }
        if (!finalStyle) {
          setLoading(false);
          setError("请填写风格，或先填写歌词 / 主题后由系统自动生成");
          return;
        }
        const r = await api.generate({
          task: lyric.trim() ? "custom" : "generate",
          style: styleToEngine(finalStyle), // UI 中文 → 引擎英文描述符
          prompt,
          lyric,
          title: title.trim() || undefined,
          vocal_gender: vocal || undefined,
          make_instrumental: false,
          // GAP-010：下载格式偏好（无损需订阅；未订阅传无损被后端 403 —— 前端已禁用选项）
          audio_format: audioFormat,
          vocal_id: vocalId || undefined, // 音色克隆（mureka-o2 后端自动剔除并 log）
          // is_public 不在创建时提交 —— 发布已后置到播放器上的「发布到广场」
        });
        songId = r.song_id;
      }
      clearDraft();
      const s = await poll(songId);
      if (s && s.status !== "completed" && s.status !== "failed") setTimedOut(true);
    } catch (err: any) {
      setError(err?.message || "生成失败");
    } finally {
      if (!canceledRef.current) setLoading(false);
    }
  }

  async function onRefresh() {
    if (!song) return;
    canceledRef.current = false;
    setTimedOut(false);
    setLoading(true);
    try {
      const s = await poll(song.id);
      if (s && s.status !== "completed" && s.status !== "failed") setTimedOut(true);
    } catch (err: any) {
      setError(err?.message || "刷新失败");
    } finally {
      if (!canceledRef.current) setLoading(false);
    }
  }

  // ── 派生状态 ──────────────────────────────────────────────────────────────
  // 后端把 style + vocal + prompt 拼成一个 prompt（≤1024 截断），
  // 所以要看**合计**长度，而不是单看 style 或 prompt。
  // [2026-09-10] 백엔드의 gospel 강제 주입이 제거되어 합산에서도 빠졌다.
  const promptTotal = [
    styleToEngine(style),
    vocal ? (vocal === "male" ? "male" : "female") : "",
    prompt,
  ]
    .filter(Boolean)
    .join(", ").length;
  const lyricLeft = MAX_LYRIC_CHARS - lyric.length;

  return (
    <div className="space-y-4">
      <form onSubmit={onSubmit} className="card space-y-4 p-4 enter">
        {draftRestored && (
          <div className="flex items-center justify-between rounded-lg bg-[var(--surface-2)] px-3 py-2 text-xs text-[var(--ink-soft)]">
            <span>已恢复上次未提交的内容</span>
            <button type="button" onClick={resetForm} className="text-[var(--muted)] underline hover:text-[var(--ink)]">
              清空
            </button>
          </div>
        )}

        {/* ── 创作模式 ── */}
        <div className="flex gap-1 rounded-lg bg-[var(--surface-2)] p-1" role="group" aria-label="创作模式">
          {MODES.map((m) => (
            <button
              key={m.key}
              type="button"
              onClick={() => changeMode(m.key)}
              aria-pressed={mode === m.key}
              className={`flex-1 rounded-md px-3 py-1.5 text-sm font-medium transition ${
                mode === m.key
                  ? "bg-[var(--surface)] text-[var(--ink)] shadow-sm"
                  : "text-[var(--muted)] hover:text-[var(--ink)]"
              }`}
            >
              {m.label}
            </button>
          ))}
        </div>
        <p className="text-[11px] text-[var(--muted)]">
          {mode === "normal"
            ? "普通创作：填风格 + 歌词（或主题），最完整可控。"
            : mode === "instrumental"
              ? "纯器乐：仅用描述生成，由 Mureka 专用端点出曲，无歌词无人声。"
              : "灵感创作：一句话 Prompt to Song，可叠加官方风格标签，无需写歌词。"}
        </p>

        {/* ── 风格（必选，留空自动生成） ── */}
        {mode === "normal" && (
        <Field
          label="风格（必选）"
          hint="点击下面标签组合，或直接输入。留空将根据歌词与主题自动生成，生成前可随时修改。"
          action={
            <AssistBtn onClick={onOptimizeStyle} disabled={busy} active={assisting === "style"}>
              ✨ 生成风格
            </AssistBtn>
          }
        >
          <AutoTextarea
            value={style}
            onChange={setStyle}
            className="input"
            rows={2}
            placeholder="例：安静的敬拜钢琴曲，温暖的男声，缓慢而治愈的节奏 —— 留空将根据歌词与主题自动生成"
          />
          <div className="mt-2 space-y-1.5">
            {STYLE_GROUPS.map((g) => (
              <div key={g.label} className="flex flex-wrap items-center gap-1">
                <span className="mr-1 w-8 shrink-0 text-[11px] text-[var(--muted)]">{g.label}</span>
                {g.items.map((it) => {
                  const on = style
                    .split(/[,，、]/)
                    .map((x) => x.trim().toLowerCase())
                    .includes(it.cn.toLowerCase());
                  return (
                    <button
                      key={it.en}
                      type="button"
                      onClick={() => toggleTag(it.cn)}
                      className="chip rounded-full px-2 py-0.5 text-xs"
                      data-on={on}
                      aria-pressed={on}
                    >
                      {it.cn}
                    </button>
                  );
                })}
              </div>
            ))}
          </div>
          <div className="mt-1.5 flex items-center justify-between text-[11px]">
            <span className="text-[var(--muted)]">
              顺序建议：曲风 → 速度 → 情绪 → 乐器 → 人声。风格完全按你填的生成，平台不再叠加任何曲风。
            </span>
            <span className={promptTotal > MAX_PROMPT_CHARS ? "text-[var(--danger)]" : "text-[var(--muted)]"}>
              {promptTotal}/{MAX_PROMPT_CHARS}
            </span>
          </div>
        </Field>
        )}

        {/* ── 主题 / 情绪（各模式都需要描述） ── */}
        <Field
          label={
            mode === "instrumental"
              ? "描述（纯器乐）"
              : mode === "easy"
                ? "描述（灵感创作 · Prompt to Song）"
                : "主题 / 情绪"
          }
          hint={
            mode === "instrumental"
              ? "描述想要的器乐氛围、乐器与节奏，由 Mureka 专用端点生成，无歌词。"
              : mode === "easy"
                ? "一句话描述想听的歌，可叠加官方风格标签，无需填歌词。"
                : undefined
          }
          action={
            <AssistBtn onClick={onOptimizePrompt} disabled={busy} active={assisting === "prompt"}>
              ✨ 自动优化
            </AssistBtn>
          }
        >
          <input
            {...fieldProps}
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            className="input"
            placeholder="清晨的微光里，安静地献上感谢"
          />
        </Field>

        {/* ── 歌词 ── */}
        {mode === "normal" && (
        <Field
          label={instrumental ? "歌词（纯音乐不需要）" : "歌词（自定义 · 留空自动生成）"}
          action={
            <div className="flex gap-2">
              <AssistBtn
                onClick={onGenerateLyrics}
                disabled={busy || instrumental}
                active={assisting === "lyrics-gen"}
                title="根据主题自动生成歌词与标题（联网调用官方服务）"
              >
                ✨ 自动生成
              </AssistBtn>
              <AssistBtn
                onClick={onFormatLyrics}
                disabled={busy || instrumental}
                active={assisting === "lyrics-fmt"}
                variant="muted"
                title="本地规则：段落结构对齐 + 去空行 + 超限截断，不改写语义"
              >
                ✨ 自动优化
              </AssistBtn>
            </div>
          }
        >
          {!instrumental && (
            <div className="mb-1.5 flex flex-wrap gap-1">
              {SECTION_TAGS.map((t) => (
                <button
                  key={t.tag}
                  type="button"
                  onClick={() => insertSection(t.tag)}
                  className="chip rounded px-1.5 py-0.5 text-[11px]"
                  title={`在光标处插入${t.label}段落标记`}
                >
                  + {t.label}
                </button>
              ))}
            </div>
          )}
          <textarea
            ref={lyricRef}
            {...fieldProps}
            id={undefined}
            value={lyric}
            onChange={(e) => setLyric(e.target.value)}
            disabled={instrumental}
            className="input"
            rows={6}
            placeholder={"想自己写就直接填；留空将根据「风格 + 主题」自动生成完整歌词与标题。\n段落结构可用上方按钮插入。"}
          />
          <div className="mt-1 text-right text-[11px]">
            <span className={lyricLeft < 0 ? "text-[var(--danger)]" : lyricLeft < 300 ? "text-[var(--muted)] opacity-80" : "text-[var(--muted)]"}>
              {lyric.length}/{MAX_LYRIC_CHARS}
            </span>
          </div>
        </Field>
        )}

        {/* ── 灵感参考（可选，默认折叠，移出主流程） ── */}
        <div>
          <button
            type="button"
            onClick={() => setShowInspiration((v) => !v)}
            className="text-xs text-[var(--muted)] underline hover:text-[var(--ink)]"
          >
            {showInspiration ? "收起灵感参考" : "展开灵感参考（可选）"}
          </button>
          {showInspiration && (
            <div className="mt-2 rounded-lg bg-[var(--surface-2)] p-3">
              <p className="mb-2 text-[11px] text-[var(--muted)]">
                一键套用整套参数（风格 + 主题）。仅作快捷参考，不单独传给生成引擎。
              </p>
              <div className="flex flex-wrap gap-1.5">
                {INSPIRATIONS.map((ins) => (
                  <button
                    key={ins.name}
                    type="button"
                    onClick={() => applyInspiration(ins)}
                    className="chip rounded-full px-2.5 py-1 text-xs"
                  >
                    {ins.name}
                  </button>
                ))}
              </div>
            </div>
          )}
        </div>

        {/* ── 歌名 ── */}
        <Field
          label="歌名（可选）"
          action={
            <AssistBtn onClick={onSuggestTitle} disabled={busy} active={assisting === "title"}>
              ✨ 自动取名
            </AssistBtn>
          }
        >
          <input
            {...fieldProps}
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            className="input"
            placeholder="留空则自动生成"
          />
        </Field>

        {mode === "easy" && (
          <Field label="风格（官方枚举，可多选）" hint="灵感创作支持 Mureka 官方风格标签，可选；不填则由模型自由发挥。福音已内置。">
            <div className="mt-2 flex flex-wrap gap-1">
              {EASY_STYLES.map((it) => {
                const on = easyStyles.includes(it.cn);
                return (
                  <button
                    key={it.en}
                    type="button"
                    onClick={() => toggleEasyStyle(it.cn)}
                    className="chip rounded-full px-2 py-0.5 text-xs"
                    data-on={on}
                    aria-pressed={on}
                  >
                    {it.cn}
                  </button>
                );
              })}
            </div>
          </Field>
        )}

        {mode !== "instrumental" && (
          <div className="rounded-lg bg-[var(--surface-2)] p-3 text-sm">
            <div className="mb-1 flex items-center justify-between">
              <span className="text-[var(--muted)]">音色克隆（可选）</span>
              {vocalId && (
                <button
                  type="button"
                  onClick={clearVocal}
                  className="text-xs text-[var(--muted)] underline hover:text-[var(--danger)]"
                >
                  清除
                </button>
              )}
            </div>
            {vocalId ? (
              <p className="text-xs text-[var(--ink-soft)]">已绑定克隆音色（ID 末 6 位：{vocalId.slice(-6)}）</p>
            ) : (
              <label className="btn btn-secondary btn-sm cursor-pointer">
                {vocalCloning ? "克隆中…" : "🎙 上传音频克隆我的音色"}
                <input
                  type="file"
                  accept=".mp3,.m4a"
                  className="hidden"
                  disabled={vocalCloning}
                  onChange={onCloneVocal}
                />
              </label>
            )}
            {vocalCloneError && (
              <p className="mt-1 text-xs text-[var(--danger)]" role="alert">
                {vocalCloneError}
              </p>
            )}
            <p className="mt-1 text-[11px] text-[var(--muted)]">
              仅支持 mp3 / m4a，单文件 &lt;10MB。克隆音色用于本次生成（mureka-o2 模型会自动忽略）。
            </p>
          </div>
        )}

        {/* ── 高级选项 ── */}
        <div>
          <button
            type="button"
            onClick={() => setShowAdvanced((v) => !v)}
            className="text-xs text-[var(--muted)] underline hover:text-[var(--ink)]"
          >
            {showAdvanced ? "收起高级选项" : "展开高级选项（人声 / 下载格式）"}
          </button>
          {showAdvanced && (
            <div className="mt-2 space-y-3 rounded-lg bg-[var(--surface-2)] p-3 text-sm">
              <div className="flex flex-wrap items-center gap-3">
                {mode === "normal" && (
                  <label className="flex items-center gap-2">
                    <span className="text-[var(--muted)]">音色</span>
                    <select
                      value={vocal}
                      onChange={(e) => changeVocal(e.target.value)}
                      className="input max-w-[8rem]"
                      aria-label="人声音色"
                    >
                      <option value="">不限</option>
                      <option value="male">男声</option>
                      <option value="female">女声</option>
                    </select>
                  </label>
                )}
                <span className="text-xs text-[var(--muted)]">
                  {mode === "normal"
                    ? "音色会同步写入风格标签，避免相互矛盾"
                    : mode === "easy"
                      ? "灵感创作仅支持克隆音色（人声性别不适用）"
                      : "纯器乐无人声"}
                </span>
              </div>

              {/* GAP-010 下载格式：无损受订阅权益约束 */}
              <div>
                <div className="mb-1 flex flex-wrap items-center gap-2">
                  <span className="text-[var(--muted)]">下载格式</span>
                  <div className="flex gap-1" role="group" aria-label="下载格式">
                    {AUDIO_FORMATS.map((f) => {
                      const locked = f.lossless && !canDownload;
                      const on = audioFormat === f.key;
                      return (
                        <button
                          key={f.key}
                          type="button"
                          disabled={locked}
                          onClick={() => setAudioFormat(f.key)}
                          aria-pressed={on}
                          title={
                            locked
                              ? "无损格式（WAV / FLAC）需订阅后解锁"
                              : `下载时默认取 ${f.label}`
                          }
                          className={`chip rounded-full px-2.5 py-0.5 text-xs ${
                            locked ? "cursor-not-allowed opacity-50" : ""
                          }`}
                          data-on={on}
                        >
                          {f.label}
                          {locked && " 🔒"}
                        </button>
                      );
                    })}
                  </div>
                </div>
                <p className="text-[11px] text-[var(--muted)]">
                  格式在<strong>下载时</strong>生效：上游同时提供 MP3 / WAV / FLAC，
                  {canDownload
                    ? "你可任选（无损为订阅权益）。"
                    : "无损（WAV / FLAC）需订阅后解锁；免费档下载 MP3。"}
                </p>
              </div>
            </div>
          )}
        </div>

        <p className="text-xs text-[var(--muted)]">
          作品生成完成后，可在播放器上点「发布到广场」公开给所有人（未发布仅自己可见）。
        </p>

        <div className="flex gap-2">
          <button
            type="submit"
            disabled={loading || busy}
            className="btn btn-primary btn-lg flex-1"
          >
            {loading ? `生成中… ${elapsed} 秒` : "生成歌曲"}
          </button>
          <button
            type="button"
            onClick={resetForm}
            disabled={loading}
            className="btn btn-secondary"
          >
            清空
          </button>
        </div>

        {loading && (
          <p className="text-xs text-[var(--muted)]">
            通常需要 1–3 分钟，请保持页面打开。完成后会自动载入播放器。
          </p>
        )}
        {error && (
          <p className="text-sm text-[var(--danger)]" role="alert">
            {error}
          </p>
        )}
        {assistNote && (
          <p className="text-xs text-[var(--ink-soft)]" role="status" aria-live="polite">
            ✓ {assistNote}
          </p>
        )}
      </form>

      {/* ── 结果 + 衔接 ── */}
      {song && (
        <div className="space-y-3">
          <Player song={song} isOwner={song.is_owner ?? false} onSpawn={setSong} />

          {song.status === "completed" && (
            <div className="card flex flex-wrap items-center gap-2 p-3 text-sm enter">
              <span className="text-[var(--muted)]">接下来：</span>
              <button
                type="button"
                onClick={() => {
                  setSong(null);
                  setTimedOut(false);
                }}
                className="btn btn-primary btn-sm"
              >
                再来一首（沿用当前参数）
              </button>
              <Link href="/library" className="btn btn-secondary btn-sm">
                去我的作品
              </Link>
              <Link href="/plaza" className="btn btn-secondary btn-sm">
                去广场
              </Link>
            </div>
          )}

          {song.status === "failed" && (
            <div className="card border-[var(--danger)]/40 p-4 text-sm">
              <p className="text-[var(--danger)]">
                生成失败{song.moderation_note ? `：${song.moderation_note}` : "，请调整描述后重试"}。
              </p>
              <button
                type="button"
                onClick={() => {
                  void runGenerate();
                }}
                disabled={loading}
                className="btn btn-secondary btn-sm mt-2"
              >
                {loading ? "重试中…" : "按当前参数重新生成"}
              </button>
            </div>
          )}

          {timedOut && (
            <button
              onClick={onRefresh}
              className="btn btn-secondary"
            >
              仍在生成，点此刷新状态
            </button>
          )}
        </div>
      )}
    </div>
  );
}

// ── 小组件 ──────────────────────────────────────────────────────────────────
/** 自适应高度文本框：随内容增长，消除固定高度的滚动条/留白（ui-ux-pro-max：克制留白 + 自适应）。 */
function AutoTextarea({
  value,
  onChange,
  className,
  rows = 2,
  placeholder,
  disabled,
}: {
  value: string;
  onChange: (v: string) => void;
  className?: string;
  rows?: number;
  placeholder?: string;
  disabled?: boolean;
}) {
  const ref = useRef<HTMLTextAreaElement>(null);
  const field = useContext(FieldIdContext);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${el.scrollHeight}px`;
  }, [value]);
  return (
    <textarea
      ref={ref}
      id={field.id}
      aria-describedby={field.hintId}
      value={value}
      rows={rows}
      placeholder={placeholder}
      disabled={disabled}
      onChange={(e) => onChange(e.target.value)}
      className={className}
    />
  );
}

/** Field 自动生成的控件 id：避免每个调用点手写 id / aria-describedby 时漂移。 */
const FieldIdContext = createContext<{ id?: string; hintId?: string }>({});

/** 表单字段：label 用 id 与控件显式关联（a11y:label-has-associated-control），
 *  因此每个受控控件必须带上 `id={fieldId}` 与 `aria-describedby`（有 hint 时）。 */
function Field({
  label,
  hint,
  action,
  children,
}: {
  label: string;
  hint?: string;
  action?: React.ReactNode;
  children: React.ReactNode;
}) {
  const fieldId = useId();
  const hintId = `${fieldId}-hint`;
  // 通过 context 把 id 交给子控件，避免每处调用点手写 id 造成漂移
  return (
    <div>
      <div className="mb-1 flex items-center justify-between gap-2">
        <label htmlFor={fieldId} className="text-sm text-[var(--muted)]">
          {label}
        </label>
        {action}
      </div>
      <FieldIdContext.Provider value={{ id: fieldId, hintId: hint ? hintId : undefined }}>
        {children}
      </FieldIdContext.Provider>
      {hint && (
        <p id={hintId} className="mt-1 text-[11px] text-[var(--muted)]">
          {hint}
        </p>
      )}
    </div>
  );
}

function AssistBtn({
  onClick,
  disabled,
  active,
  children,
  title,
  variant = "primary",
}: {
  onClick: () => void;
  disabled?: boolean;
  active?: boolean;
  children: React.ReactNode;
  title?: string;
  variant?: "primary" | "muted";
}) {
  // AI 自动按钮：统一使用 .btn-ai（带微光扫过标识"智能"）。
  // muted 变体仅作为语义提示，外观仍属 AI 家族。
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      title={title}
      data-loading={active ? "true" : "false"}
      className="btn btn-ai btn-sm"
    >
      {active ? "处理中…" : children}
    </button>
  );
}
