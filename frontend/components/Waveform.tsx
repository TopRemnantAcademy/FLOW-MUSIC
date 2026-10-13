"use client";

/**
 * 波形 seek（GAP-013）
 *
 * ## 数据来源：**服务端给峰值**，前端只画
 *
 * spike 里原本打算「前端 fetch 音频 → decodeAudioData → 自己算峰值」，浏览器实测
 * 直接推翻了这条路（不是口味问题，是行不通）：
 *
 *   1. 前端 `fetch(audio_url)` 受页面 CSP 的 `connect-src` 管，而 security-headers.js
 *      写死 `connect-src 'self' {API_BASE}`；生产音频在 `STORAGE_PUBLIC_BASE`
 *      （CDN，与 API **不同源**）→ 被 CSP 拦。
 *      实测（chromium --headless + CDP `Network.loadingFailed`）：`blockedReason: "csp"`。
 *   2. 改用 `credentials: "include"` 绕，还会撞 CORS `PreflightInvalidAllowCredentials`
 *      （`ACAO: *` + include 是非法组合）。
 *   3. 连 wavesurfer 自带的 `load(url)` 也一样死在 CSP（实测 `Failed to fetch`、canvas 数 = 0）
 *      —— 它只是把 fetch 包进库里，不改变 CSP 事实。
 *   4. CSP 唯一放行音频的是 `media-src 'self' blob: https:`（`<audio src>` 能播），
 *      但 `<audio>` **没有**任何「读原始 PCM」的 API。
 *
 * 结论：峰值改由后端 ffmpeg 抽好（`GET /api/share/songs/{token}/peaks`，同源、CSP 放行、
 * 不白拉整曲），前端拿到一个 `number[]` 直接画 canvas。这也顺带把「渲染」这件事
 * 从「借 wavesurfer」降级成「自己画 20 行 canvas」—— 既然网络路径都要重来，
 * 再引一个 41KB 的库只为画柱子就不划算了，故本文件**不依赖 wavesurfer.js**。
 *
 * ## 点击 seek：实测校准过的两个坑
 *
 *   1. 点击位置 → 秒数**必须自己算**：`relativeX * duration`。wavesurfer 的
 *      `interaction` 载荷是 `relativeX * ws.getDuration()`，而 `ws.getDuration()`
 *      读它自己的 decodedData/media —— 我们没走它的 `load()`，该值恒为 0
 *      → 点击波形永远跳回 0（实测 5 个位置全返回 0.000s）。
 *   2. 生效时刻的 duration 可能还没到（metadata 要等），故用 ref 传当前值，
 *      避免回调闭包捕获到 0 之后永远 0。
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { API_BASE } from "@/lib/api";
import { busSeek, useAudioTime, useAudioStatus } from "@/lib/audioBus";
import { AB_MIN_SPAN, activeAbLoop, usePlayback } from "@/lib/playback";

export interface WaveformProps {
  /** 是否有可播音频。仅用于「没音频就不画波形」的判断 ——
   *  **不**用它 fetch（跨源音频被 CSP connect-src 拦，见文件头）。 */
  src: string | null;
  /** 已知时长（秒），用于在峰值到达前先画出进度比例 */
  duration?: number;
  height?: number;
  /** 无障碍标签 */
  label?: string;
  /** 峰值拉取地址。分享页给 `/api/share/songs/{token}/peaks`；不给则不画波形。 */
  peaksUrl?: string | null;
}

/** 服务端 peaks 端点的响应形状。 */
type PeaksResp = { song_id: number; buckets: number; duration: number; peaks: number[] };

export default function Waveform({
  src,
  duration = 0,
  height = 56,
  label = "波形",
  peaksUrl = null,
}: WaveformProps) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const [peaks, setPeaks] = useState<number[] | null>(null);
  const [failed, setFailed] = useState(false);
  const { duration: busDuration } = useAudioStatus();
  const t = useAudioTime();
  const { ab } = usePlayback();

  const total = busDuration || duration || 0;
  const loop = activeAbLoop(ab);

  // 回调里读「当前」时长：click 处理只绑一次，闭包会捕获旧值。
  const durationRef = useRef(0);
  durationRef.current = total;

  // ── 拉峰值（同源，CSP 放行）────────────────────────────────────────────
  useEffect(() => {
    if (!peaksUrl) {
      setPeaks(null);
      setFailed(false);
      return;
    }
    let canceled = false;
    setFailed(false);
    (async () => {
      try {
        const res = await fetch(`${API_BASE}${peaksUrl}`, { credentials: "include" });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data: PeaksResp = await res.json();
        if (canceled) return;
        if (!Array.isArray(data.peaks) || data.peaks.length === 0) throw new Error("empty peaks");
        setPeaks(data.peaks);
      } catch {
        if (!canceled) {
          // fail-closed：宁可没有波形，也不要一根随机跳动的柱子（不造假功能）。
          setPeaks(null);
          setFailed(true);
        }
      }
    })();
    return () => {
      canceled = true;
    };
  }, [peaksUrl]);

  // ── 画波形 + 进度 + AB 区间 ───────────────────────────────────────────
  const draw = useCallback(() => {
    const cv = canvasRef.current;
    if (!cv || !peaks || peaks.length === 0) return;
    const ctx = cv.getContext("2d");
    if (!ctx) return;

    // devicePixelRatio 缩放：否则高分屏上柱子发虚。CSS 尺寸由 style 控制，
    // 画布像素尺寸乘 dpr，再整体 scale，逻辑坐标就等于 CSS 像素。
    const dpr = Math.max(1, window.devicePixelRatio || 1);
    const cssW = cv.clientWidth || 300;
    const cssH = height;
    if (cv.width !== Math.round(cssW * dpr) || cv.height !== Math.round(cssH * dpr)) {
      cv.width = Math.round(cssW * dpr);
      cv.height = Math.round(cssH * dpr);
    }
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, cssW, cssH);

    const styles = getComputedStyle(cv);
    const playedColor = styles.getPropertyValue("--ink").trim() || "#111";
    const restColor = styles.getPropertyValue("--ink-soft").trim() || "#999";

    const n = peaks.length;
    const barW = Math.max(1, cssW / n);
    const mid = cssH / 2;
    const playedX = total > 0 ? (t / total) * cssW : 0;

    for (let i = 0; i < n; i++) {
      const x = i * barW;
      const h = Math.max(1, peaks[i] * cssH * 0.92);
      ctx.fillStyle = x + barW <= playedX ? playedColor : restColor;
      ctx.fillRect(x, mid - h / 2, Math.max(1, barW - 1), h);
    }

    // AB 区间描边（画在最上层，与 DOM 高亮双保险：canvas 重绘不会擦掉描边）。
    if (loop && total > 0) {
      const ax = (loop.a / total) * cssW;
      const bx = (loop.b / total) * cssW;
      ctx.fillStyle = restColor;
      ctx.globalAlpha = 0.15;
      ctx.fillRect(ax, 0, Math.max(1, bx - ax), cssH);
      ctx.globalAlpha = 1;
      ctx.fillStyle = playedColor;
      ctx.fillRect(ax, 0, 1, cssH);
      ctx.fillRect(bx - 1, 0, 1, cssH);
    }
  }, [peaks, height, total, t, loop?.a, loop?.b]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    draw();
  }, [draw]);

  // 容器尺寸变化后重画（窗口缩放 / 侧栏开合）
  useEffect(() => {
    const cv = canvasRef.current;
    if (!cv || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(() => draw());
    ro.observe(cv);
    return () => ro.disconnect();
  }, [draw]);

  // ── 点击 / 拖拽 seek ─────────────────────────────────────────────────
  const seekFromEvent = useCallback((clientX: number, el: HTMLElement) => {
    const d = durationRef.current;
    if (!d || d <= 0) return;
    const rect = el.getBoundingClientRect();
    if (rect.width <= 0) return;
    const ratio = Math.min(1, Math.max(0, (clientX - rect.left) / rect.width));
    busSeek(ratio * d);
  }, []);

  const onPointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    seekFromEvent(e.clientX, e.currentTarget);
    // 按下即捕获 → 拖动过程中持续 seek（练唱找点最常用这个手势）
    e.currentTarget.setPointerCapture?.(e.pointerId);
  };
  const onPointerMove = (e: React.PointerEvent<HTMLDivElement>) => {
    if (e.buttons !== 1) return;
    seekFromEvent(e.clientX, e.currentTarget);
  };

  const pct = total > 0 ? Math.min(100, Math.max(0, (t / total) * 100)) : 0;
  const aPct = loop && total > 0 ? (loop.a / total) * 100 : null;
  const bPct = loop && total > 0 ? (loop.b / total) * 100 : null;

  return (
    <div className="relative w-full" data-testid="waveform">
      <div
        role="slider"
        tabIndex={0}
        aria-label={label}
        aria-valuemin={0}
        aria-valuemax={Math.round(total)}
        aria-valuenow={Math.round(t)}
        aria-valuetext={`${t.toFixed(1)} / ${total.toFixed(1)} 秒`}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onKeyDown={(e) => {
          if (e.key === "ArrowRight") busSeek(Math.min(total, t + 5));
          else if (e.key === "ArrowLeft") busSeek(Math.max(0, t - 5));
        }}
        className="cursor-pointer select-none"
        style={{ height }}
      >
        <canvas
          ref={canvasRef}
          aria-hidden="true"
          className="block h-full w-full"
          style={{ height }}
        />
      </div>

      {/* AB 区间高亮（DOM 层，覆盖在 canvas 上；与 canvas 描边互为双保险） */}
      {aPct != null && bPct != null && (
        <div
          className="pointer-events-none absolute inset-y-0 border-x border-[var(--ink-soft)]"
          style={{ left: `${aPct}%`, width: `${Math.max(0, bPct - aPct)}%` }}
          aria-hidden="true"
        />
      )}

      {!peaks && !failed && src && (
        <p className="mt-1 text-xs text-[var(--muted)]">波形加载中…</p>
      )}
      {failed && (
        <p className="mt-1 text-xs text-[var(--muted)]">
          波形暂不可用，仍可拖动/点击上方进度条 seek
        </p>
      )}
      {ab && !loop && (
        <p className="mt-1 text-xs text-[var(--muted)]">
          A 点 {ab.a.toFixed(1)}s · 再点「B」结束区间（至少 {AB_MIN_SPAN}s）
        </p>
      )}
      {/* 进度兜底：没有波形时也要能看到进度（避免「控件全没了」） */}
      {!peaks && total > 0 && (
        <div className="absolute inset-x-0 bottom-0 h-0.5 bg-[var(--border)]" aria-hidden="true">
          <div className="h-full bg-[var(--ink)]" style={{ width: `${pct}%` }} />
        </div>
      )}
    </div>
  );
}
