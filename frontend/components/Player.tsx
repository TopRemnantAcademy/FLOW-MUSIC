"use client";

import { useEffect, useRef, useState } from "react";
import { api, SongView, SpawnParams } from "@/lib/api";
import { canPlayNext, canPlayPrevious, currentSong } from "@/lib/playlist";
import {
  audioElementRef,
  busLoad,
  busPause,
  busPlay,
  busSeek,
  busToggle,
  onBusEnded,
  useAudioStatus,
} from "@/lib/audioBus";
import {
  advance,
  back,
  cycleRepeat,
  consumeAutostart,
  playFrom,
  replaceCurrent,
  toggleShuffle,
  usePlayback,
} from "@/lib/playback";
import SyncedLyrics from "@/components/SyncedLyrics";

const POLL_INTERVAL_MS = 5000;
const MAX_POLLS = 40;

// 生成状态中文显示（后端枚举 pending|processing|completed|failed，见 orchestrator/base.py:44；未知值原样回退）
const STATUS_CN: Record<string, string> = {
  pending: "等待中",
  processing: "生成中",
  completed: "已完成",
  failed: "失败",
};
const statusCn = (s?: string | null) => (s ? STATUS_CN[s] ?? s : "");

export default function Player({
  song,
  onSpawn,
  isOwner = false,
  queue,
  onReplaceSong,
}: {
  song: SongView;
  onSpawn?: (s: SongView) => void;
  isOwner?: boolean;
  /** 所在页面的完整列表，用于锚定播放队列。缺省 = 只有这一首（如生成结果页），不出上下首 */
  queue?: SongView[];
  /** 自动连播 / 点上一首下一首时，由页面把正在播放的曲目切到这首（页面需提供 state setter） */
  onReplaceSong?: (s: SongView) => void;
}) {
  const { playlist, prefs, autostart } = usePlayback();
  const [local, setLocal] = useState<SongView>(song);
  const [syncState, setSyncState] = useState<"idle" | "syncing" | "synced" | "failed">("idle");
  const [msg, setMsg] = useState("");
  const [spawning, setSpawning] = useState<"" | "extend" | "cover" | "remix">("");
  // extend 衔接方向。此前 handleSpawn 固定发 `{}`，用户无从选择：
  // 后端 SpawnReq 也没有该字段，即使传了也会被 pydantic 拒绝 → 「从头部衔接」
  // 在 UI 上完全不存在（GAP-149 的「假功能」形态）。mureka-8 下该字段必填。
  const [extendType, setExtendType] = useState<"tail" | "head">("tail");
  const [downloading, setDownloading] = useState(false);
  // GAP-010：下载格式。曲目自带偏好（audio_format）+ 实际可用格式（available_formats）。
  // 无损缺失时不列出对应选项，避免「选了才 409」。
  const [downloadFormat, setDownloadFormat] = useState<string>("mp3");
  const [publishing, setPublishing] = useState(false);
  const [playing, setPlaying] = useState(false);
  // 需鉴权的试听端点（对象存储未配置时的回退）→ 取 blob 再播。
  // O4 优化：预览 blob 不再在挂载时 eagerly 拉取。列表页（广场/我的作品/首页）每个
  // <Player> 都会挂载，20+ 实例会在加载时同时发起 GET /api/songs/{id}/preview，
  // 造成 20+ 并发 blob 拉取。改为「用户点击播放时」才惰性拉取（见 onPlay）。
  const [previewSrc, setPreviewSrc] = useState<string | null>(null);
  const [previewFailed, setPreviewFailed] = useState(false);
  const canceledRef = useRef(false);
  const previewUrlRef = useRef<string | null>(null); // 当前 blob URL，便于卸载/换歌时回收
  // 逐字歌词组件需要直接驱动音频元素（读 currentTime / 跳转）—— 现在是全局总线元素
  const { playing: audioPlaying } = useAudioStatus();
  // 逐字歌词按需补全去重标记（见 ensureDetail）
  const detailFetchedRef = useRef(false);

  useEffect(() => {
    setLocal(song);
  }, [song]);

  // GAP-010：曲目切换时把下载格式同步为曲目偏好（后端 audio_format，默认 mp3）。
  useEffect(() => {
    setDownloadFormat(song.audio_format || "mp3");
  }, [song.audio_format, song.id]);

  useEffect(() => {
    return () => {
      canceledRef.current = true;
      if (previewUrlRef.current) URL.revokeObjectURL(previewUrlRef.current);
    };
  }, []);

  // 切歌（audio_url/id 变化）时重置预览并回收旧 blob
  useEffect(() => {
    if (previewUrlRef.current) {
      URL.revokeObjectURL(previewUrlRef.current);
      previewUrlRef.current = null;
    }
    setPreviewSrc(null);
    setPreviewFailed(false);
    detailFetchedRef.current = false; // 换歌后允许重新补全逐字歌词
  }, [local.audio_url, local.id]);

  // ── 播放队列 ────────────────────────────────────────────────────────────
  // 队列锚定的是「用户点播放那一刻所在页面的列表」，而不是一个凭空造出来的全局队列：
  // library=我的作品、plaza=广场榜、HomeFeed=首页 feeds，三份榜单不同，混用会串台。
  const list = queue && queue.length > 0 ? queue : [song];
  const queueIndex = list.findIndex((x) => x.id === song.id);
  const inQueue = !!playlist && playlist.songs.some((s) => s.id === song.id);
  const isCurrent = inQueue && playlist?.currentSongId === song.id;
  const queuePos = inQueue && playlist ? playlist.currentIndex + 1 : 0;
  const queueLen = playlist?.songs.length ?? 0;
  const canPrev = canPlayPrevious(playlist);
  const canNext = canPlayNext(playlist, prefs);

  // 自动连播：队列已前移，等本卡片拿到新的 song 后自动起播（一次性）
  useEffect(() => {
    if (!autostart || playlist?.currentSongId !== song.id) return;
    consumeAutostart();
    void onPlay();
    // 仅依赖 autostart / song.id：onPlay 内部会继续处理鉴权预览与播放计数
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autostart, song.id]);

  // 一曲播完 → 由队列决定是否连播。本卡片只在「自己就是当前曲目」时响应，
  // 否则底部条/别的卡片触发的 ended 也会把它切走。
  useEffect(
    () =>
      onBusEnded(() => {
        if (!isCurrent) return;
        if (prefs.repeat === "one") {
          busSeek(0);
          busPlay();
          return;
        }
        const nxt = advance();
        if (!nxt) busPause(); // 顺序模式放完列表 → 停住，不自动跳
      }),
    [isCurrent, prefs.repeat],
  );

  // 设置预览 blob 并维护引用以便回收
  const applyPreview = (u: string | null) => {
    if (previewUrlRef.current && previewUrlRef.current !== u) {
      URL.revokeObjectURL(previewUrlRef.current);
    }
    previewUrlRef.current = u;
    setPreviewSrc(u);
  };

  // 逐字歌词按需补全：列表/广场/首页接口恒返 lyrics_sync=null（仅详情端点带时间轴），
  // 故用户首次播放时拉一次详情并合并；之后不再重复请求；失败不缓存（允许下次重试）。
  // 这修复了「Web 在 library/plaza/home 浏览流里逐字歌词永不显示」的根因。
  async function ensureDetail() {
    if (detailFetchedRef.current || !local.id || local.lyrics_sync) return;
    detailFetchedRef.current = true;
    try {
      const d = await api.getSong(local.id);
      setLocal((p) => ({
        ...p,
        lyrics_sync: d.lyrics_sync ?? p.lyrics_sync,
        lyric: p.lyric ?? d.lyric,
        cover_url: p.cover_url ?? d.cover_url,
      }));
    } catch {
      detailFetchedRef.current = false;
    }
  }

  async function onPlay() {
    void ensureDetail(); // 首次播放时补全逐字歌词（幂等）
    // 首次播放（或本曲尚未入队）→ 用所在页面列表锚定队列。
    // playFrom 会把 autostart 置 true，这里立刻消费掉：真正的起播由本函数下方的逻辑负责，
    // 否则会起播两次（第二次还会多打一次 /api/songs/{id}/play）。
    if (!inQueue || playlist?.currentSongId !== song.id) {
      playFrom(list, queueIndex >= 0 ? queueIndex : 0);
      consumeAutostart();
    }
    try {
      const updated = await api.play(local.id);
      setLocal((p) => ({ ...p, play_count: updated.play_count ?? p.play_count }));
      const url = local.audio_url || "";
      // 直接 http 直链：音频总线可直接播，无需 blob 回退
      if (!url.startsWith("/api/")) {
        busLoad(url);
        return;
      }
      // 需鉴权端点：惰性拉取 blob 后再播（O4，避免挂载即拉取）
      if (previewSrc) {
        busLoad(previewSrc);
        return;
      }
      const u = await api.previewObjectUrl(local.id);
      if (u) {
        applyPreview(u);
        busLoad(u);
      } else {
        setPreviewFailed(true);
      }
    } catch (e: any) {
      if (e?.status === 401) setMsg("请先登录后再播放（右上角）");
      else setMsg(e?.message || "播放失败");
    }
  }

  async function onSync() {
    if (!local.id) return;
    setSyncState("syncing");
    setMsg("");
    try {
      await api.syncFlow(local.id);
      setSyncState("synced");
      setMsg("已同步到 FLOW 应用（手机端歌单刷新即可拉取）");
    } catch (e: any) {
      setSyncState("failed");
      setMsg(e?.message || "同步失败");
    }
  }

  async function handleSpawn(task: "extend" | "cover" | "remix") {
    if (!local.id) return;
    setSpawning(task);
    setMsg("");
    // 仅 extend 携带方向；cover/remix 带上会被后端忽略，不如不带以保持请求语义清晰。
    const params: SpawnParams = task === "extend" ? { extend_type: extendType } : {};
    try {
      const fn =
        task === "extend" ? api.extendSong : task === "cover" ? api.coverSong : api.remixSong;
      const { song_id } = await fn(local.id, params);
      let s: SongView | null = null;
      for (let i = 0; i < MAX_POLLS; i++) {
        if (canceledRef.current) break;
        await new Promise((r) => setTimeout(r, POLL_INTERVAL_MS));
        if (canceledRef.current) break;
        s = await api.getSong(song_id);
        if (s.status === "completed" || s.status === "failed") break;
      }
      if (s && s.status === "completed") {
        setLocal(s);
        // 新对象就地回填队列，保持当前位置不变（否则续写完会被踢回列表第一首）
        replaceCurrent(s);
        onSpawn?.(s);
        setMsg(`已生成${task === "extend" ? "续写" : task === "cover" ? "翻唱" : "混音"}，已载入播放器`);
      } else if (s && s.status === "failed") {
        setMsg("生成失败，请稍后重试");
      } else {
        setMsg("生成超时，请稍后在我的作品查看");
      }
    } catch (e: any) {
      // 分状态码提示：此前一律「生成失败」，用户分不清「额度不足」与「引擎不支持」，
      // 也不知道该充值还是该换歌。后端语义：400=参数/审核、401=登录、402=额度、
      // 429=限流、501=当前引擎不支持该能力。
      const status = e?.status;
      if (status === 402) setMsg("额度不足或未订阅，请升级套餐后重试");
      else if (status === 429) setMsg("操作过于频繁，请稍候再试");
      else if (status === 501) setMsg("当前引擎不支持该能力，请换个方式创作");
      else setMsg(e?.message || "生成失败");
    } finally {
      setSpawning("");
    }
  }

  async function handleDownload() {
    if (!local.id) return;
    setDownloading(true);
    setMsg("");
    try {
      await api.downloadSong(local.id, downloadFormat);
      setMsg(`已开始下载完整音频（${downloadFormat.toUpperCase()}）`);
    } catch (e: any) {
      if (e?.status === 403) setMsg("订阅后可下载完整音频");
      else if (e?.status === 409) setMsg(e?.message || "该曲目没有所选格式");
      else setMsg(e?.message || "下载失败");
    } finally {
      setDownloading(false);
    }
  }

  // 可用下载格式：优先用后端 available_formats（如实反映上游是否返了无损链接），
  // 缺字段时退化为仅 mp3（保守，不撒谎）。
  const dlFormats: string[] =
    local.available_formats && local.available_formats.length
      ? local.available_formats
      : ["mp3"];
  // 用户偏好无效（例如偏好 flac 但本曲无 flac）时回落到 mp3，避免点了才 409。
  const activeDlFormat = dlFormats.includes(downloadFormat)
    ? downloadFormat
    : (dlFormats[0] ?? "mp3");

  // 发布**后置**：只有生成完成 + 审核通过的作品才允许公开到广场。
  // 后端 PATCH /api/songs/{id} 同样强制该约束（未完成时返回 400 并说明当前状态）。
  async function onTogglePublish() {
    if (!local.id) return;
    setPublishing(true);
    setMsg("");
    try {
      const next = !local.is_public;
      const updated = await api.updateVisibility(local.id, next);
      setLocal(updated);
      replaceCurrent(updated);
      onSpawn?.(updated);
      setMsg(next ? "已发布到广场，所有人可见" : "已取消公开，仅自己可见");
    } catch (e: any) {
      setMsg(e?.message || "发布失败");
    } finally {
      setPublishing(false);
    }
  }

  const completed = local.status === "completed";
  const generating = !local.audio_url && !completed;
  // 已完成但无音频 = 试听片段生成失败（后端 fail-closed，不会回退完整链接）
  const audioMissing = completed && (!local.audio_url || previewFailed);
  // 卡片内不再有 <audio>：播放由全局总线承载，卡片只负责「起播 / 暂停」按钮与歌词。
  // 这样底部条、逐字歌词、连播指向的是同一个音频实例。

  return (
    <div className="card p-4">
      {local.status && local.status !== "completed" && (
        <p className="mb-2 text-sm text-[var(--muted)]">状态：{statusCn(local.status)}</p>
      )}

      {generating ? (
        <p className="text-sm text-[var(--muted)]">生成中…（状态：{statusCn(local.status || "pending")}）</p>
      ) : audioMissing ? (
        <p className="text-sm text-[var(--muted)]">⚠ 试听片段暂不可用，请稍后重试或订阅后收听完整版。</p>
      ) : (
        <>
          {local.cover_url && (
            <img
              src={local.cover_url}
              alt={local.title || ""}
              loading="lazy"
              onError={(e) => {
                (e.currentTarget as HTMLImageElement).style.display = "none";
              }}
              className="mb-3 h-40 w-40 rounded-xl border border-[var(--border)] object-cover"
            />
          )}
          <div className="mb-2 flex items-center gap-2">
            <h3 className="font-semibold">{local.title || "未命名"}</h3>
            {audioPlaying && (
              <span className="eq text-[var(--ink-soft)]" aria-hidden="true">
                <span /><span /><span /><span /><span />
              </span>
            )}
          </div>
          {isCurrent && (
            <p className="mb-2 text-xs text-[var(--ink-soft)]">
              正在播放 · 队列 {queuePos}/{queueLen || 1}
            </p>
          )}
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <button
              type="button"
              onClick={() => (isCurrent ? busToggle() : void onPlay())}
              className="btn btn-primary btn-sm"
              aria-label={isCurrent && audioPlaying ? "暂停" : "播放"}
            >
              {isCurrent && audioPlaying ? "暂停" : "播放"}
            </button>
          </div>
          {local.can_play_full === false && (
            <p className="mt-1 text-xs text-[var(--muted)]">
              试听前 {local.preview_seconds ?? 60} 秒 · 订阅后可收听并下载完整版
            </p>
          )}
          {/* 传输控件只出现在「正在播的那张」卡片上：列表有 20 张卡时，
              每卡都挂一排上下首按钮既是视觉噪音，也会让 DOM 多出几十个按钮。 */}
          {isCurrent && (
            <div className="mt-2 flex flex-wrap items-center gap-2">
              <button
                type="button"
                onClick={() => {
                  const p = back();
                  if (p) onReplaceSong?.(p);
                }}
                disabled={!canPrev}
                className="btn btn-secondary btn-sm"
              >
                上一首
              </button>
              <button
                type="button"
                onClick={() => {
                  const n = advance();
                  if (n) onReplaceSong?.(n);
                }}
                disabled={!canNext}
                className="btn btn-secondary btn-sm"
              >
                下一首
              </button>
              <button
                type="button"
                onClick={toggleShuffle}
                aria-pressed={prefs.shuffle}
                className={`btn btn-sm ${prefs.shuffle ? "btn-primary" : "btn-secondary"}`}
              >
                随机{prefs.shuffle ? "开" : "关"}
              </button>
              <button
                type="button"
                onClick={cycleRepeat}
                className="btn btn-secondary btn-sm"
                title="点击切换：关 → 列表循环 → 单曲循环"
              >
                {prefs.repeat === "none" ? "循环关" : prefs.repeat === "all" ? "列表循环" : "单曲循环"}
              </button>
            </div>
          )}
          <SyncedLyrics
            audioRef={audioElementRef}
            raw={local.lyrics_sync ?? null}
            plainLyric={local.lyric ?? null}
          />
        </>
      )}

      <div className="mt-3 flex flex-wrap gap-2">
        {!generating && (
          <button
            onClick={onPlay}
            className="btn btn-primary btn-sm"
          >
            播放 +1（{local.play_count || 0}）
          </button>
        )}
        {local.id != null && !generating && (
          <>
            {isOwner && (
              <>
                {completed ? (
                  <button
                    onClick={onTogglePublish}
                    disabled={publishing}
                    className={`btn btn-sm ${local.is_public ? "btn-secondary" : "btn-primary"}`}
                  >
                    {publishing
                      ? "处理中…"
                      : local.is_public
                      ? "取消公开"
                      : "发布到广场"}
                  </button>
                ) : (
                  <span className="px-1 py-1.5 text-xs text-[var(--muted)]">
                    生成完成后可发布到广场
                  </span>
                )}
                <button
                  onClick={onSync}
                  disabled={syncState === "syncing" || syncState === "synced"}
                  className="btn btn-secondary btn-sm"
                >
                  {syncState === "synced"
                    ? "已同步到 FLOW ✓"
                    : syncState === "syncing"
                    ? "同步中…"
                    : "同步到 FLOW 应用"}
                </button>
                {/* 衔接方向选择器：Mureka /song/extend 的 extend_type（mureka-8 必填）。
                    没有它，「从头部衔接」这条能力在界面上根本不存在。 */}
                <label className="flex items-center gap-1 text-xs text-[var(--muted)]">
                  衔接方向
                  <select
                    value={extendType}
                    onChange={(e) => setExtendType(e.target.value as "tail" | "head")}
                    disabled={spawning !== ""}
                    className="rounded border border-[var(--border)] bg-[var(--surface)] px-1 py-0.5 text-xs"
                    aria-label="续写衔接方向"
                  >
                    <option value="tail">从尾部衔接</option>
                    <option value="head">从头部衔接</option>
                  </select>
                </label>
                <button
                  onClick={() => handleSpawn("extend")}
                  disabled={spawning !== ""}
                  className="btn btn-secondary btn-sm"
                >
                  {spawning === "extend" ? "续写中…" : "续写"}
                </button>
              </>
            )}
            <button
              onClick={() => handleSpawn("cover")}
              disabled={spawning !== ""}
              className="btn btn-secondary btn-sm"
            >
              {spawning === "cover" ? "翻唱中…" : "翻唱"}
            </button>
            <button
              onClick={() => handleSpawn("remix")}
              disabled={spawning !== ""}
              className="btn btn-secondary btn-sm"
            >
              {spawning === "remix" ? "混音中…" : "混音"}
            </button>
            {local.can_download && (
              <div className="flex items-center gap-1">
                {/* GAP-010：多格式时给选择器；仅 mp3 时不必徒增控件 */}
                {dlFormats.length > 1 && (
                  <select
                    value={activeDlFormat}
                    onChange={(e) => setDownloadFormat(e.target.value)}
                    disabled={downloading}
                    className="input max-w-[6.5rem]"
                    title="选择下载格式（无损为订阅权益）"
                  >
                    {dlFormats.map((f) => (
                      <option key={f} value={f}>
                        {f.toUpperCase()}
                      </option>
                    ))}
                  </select>
                )}
                <button
                  onClick={handleDownload}
                  disabled={downloading}
                  className="btn btn-secondary btn-sm"
                >
                  {downloading
                    ? "下载中…"
                    : `下载完整版${dlFormats.length > 1 ? `（${activeDlFormat.toUpperCase()}）` : ""}`}
                </button>
              </div>
            )}
          </>
        )}
      </div>
      {msg && <p className="mt-1 text-xs text-[var(--ink-soft)]">{msg}</p>}
    </div>
  );
}
