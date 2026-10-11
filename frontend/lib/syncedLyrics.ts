// 逐字同步歌词核心逻辑（无 DOM 依赖，可独立单测 / Node 直跑）。
//
// 数据源：Mureka lyrics_sections（后端 lyrics_sync 列 = json.dumps 字符串）：
//   [
//     { section_type, start, end, lines: [
//       { start, end, text, words: [ { start, end, text } ] }
//     ]}
//   ]
// 契约容错：自动识别常见 key 命名（start/startTime/begin/t0；end/endTime/finish/t1；
//   text/word/content/lyric；words/word；lines/rows），因为后端 mureka.py 原样 json.dumps
//   存储、从不解析，前端解析器必须容忍 Mureka 可能的字段变化。
// 与 FLOW APP mobile/utils/syncedLyrics.core.js 同契约、同算法（单一事实源的两端实现）。

export type RawWord = {
  start?: number | string | null;
  end?: number | string | null;
  text?: string | null;
  words?: RawWord[] | null;
  word?: RawWord[] | null;
  [k: string]: unknown;
};
export type RawLine = {
  start?: number | string | null;
  end?: number | string | null;
  text?: string | null;
  lines?: RawLine[] | null;
  rows?: RawLine[] | null;
  words?: RawWord[] | null;
  word?: RawWord[] | null;
  [k: string]: unknown;
};
export type RawSection = {
  start?: number | string | null;
  end?: number | string | null;
  text?: string | null;
  tag?: string | null;
  section_type?: string | null;
  lines?: RawLine[] | null;
  rows?: RawLine[] | null;
  [k: string]: unknown;
};

export type FlatWord = { text: string; start: number; end: number; line: number; wi: number };
export type FlatLine = { text: string; start: number; end: number };
export type FlatSync = {
  ok: boolean;
  error?: string;
  words: FlatWord[];
  lines: FlatLine[];
  duration: number;
};

function num(v: unknown, d: number | null): number | null {
  const n = typeof v === "string" ? parseFloat(v) : v;
  return typeof n === "number" && isFinite(n) ? n : d;
}
function str(v: unknown): string {
  return typeof v === "string" ? v : v == null ? "" : String(v);
}

function pickStart(o: any): number | null {
  const v =
    o.start != null ? o.start
    : o.startTime != null ? o.startTime
    : o.begin != null ? o.begin
    : o.t0 != null ? o.t0
    : null;
  return num(v, null);
}
function pickEnd(o: any): number | null {
  const v =
    o.end != null ? o.end
    : o.endTime != null ? o.endTime
    : o.finish != null ? o.finish
    : o.t1 != null ? o.t1
    : null;
  return num(v, null);
}
function pickText(o: any): string {
  return str(
    o.text != null ? o.text
    : o.word != null ? o.word
    : o.content != null ? o.content
    : o.lyric != null ? o.lyric
    : ""
  );
}
function pickWords(o: any): any[] | null {
  if (Array.isArray(o.words)) return o.words;
  if (Array.isArray(o.word)) return o.word;
  return null;
}
function pickLines(section: any): any[] | null {
  if (Array.isArray(section.lines)) return section.lines;
  if (Array.isArray(section.rows)) return section.rows;
  return null;
}

/**
 * flattenSync(raw)
 *  raw: JSON 字符串或已解析对象（lyrics_sync 字符串 / lyrics_sections 数组 / lines 数组 / word 数组）。
 * 返回 { ok, words:[{text,start,end,line,wi}], lines:[{text,start,end}], duration }。
 *  words 按 start 升序；line=所属行索引；wi=行内词序。
 */
export function flattenSync(raw: string | object | null | undefined): FlatSync {
  let data: any = raw;
  if (typeof raw === "string") {
    try {
      data = JSON.parse(raw);
    } catch {
      return { ok: false, error: "invalid json", words: [], lines: [], duration: 0 };
    }
  }
  if (!data) return { ok: false, error: "empty", words: [], lines: [], duration: 0 };

  let sections: any[] | null = null;
  if (Array.isArray(data)) {
    // 顶层元素是「段」(含 lines/rows) 还是「行」(含 start/words)？
    if (data.length && (data[0].lines || data[0].rows)) sections = data;
    else if (data.length && (data[0].start != null || data[0].text != null || data[0].words))
      sections = [{ lines: data }];
    else sections = [];
  } else if (data.lyrics_sections) {
    sections = Array.isArray(data.lyrics_sections) ? data.lyrics_sections : null;
  } else if (data.lines || data.words || data.rows) {
    sections = [{ lines: data.lines || data.rows || data.words }];
  } else {
    return { ok: false, error: "unrecognized shape", words: [], lines: [], duration: 0 };
  }
  if (!sections) sections = [];

  const words: FlatWord[] = [];
  const lines: FlatLine[] = [];
  let duration = 0;
  let lineIdx = 0;

  for (const sec of sections) {
    const ls: any[] = pickLines(sec) || [];
    for (const ln of ls) {
      const ws: any[] = pickWords(ln) || [];
      const lineStart = pickStart(ln);
      const lineEnd = pickEnd(ln);
      const lineText = pickText(ln);
      let wordStart = -1;
      let wordEnd = -1;

      if (ws.length) {
        let wi = 0;
        for (const w of ws) {
          const s = pickStart(w);
          const e = pickEnd(w);
          const t = pickText(w);
          if (s == null && e == null) continue; // 跳过无时间词
          const wStart = s == null ? (wordEnd >= 0 ? wordEnd : lineStart) : s;
          const wEnd = e == null ? lineEnd : e;
          words.push({ text: t, start: wStart as number, end: wEnd as number, line: lineIdx, wi });
          if (wordStart < 0) wordStart = wStart as number;
          wordEnd = wEnd as number;
          if (wEnd != null && wEnd > duration) duration = wEnd;
          wi++;
        }
      }
      // 该行无 words 但有起止 → 整行作为单“词”兜底
      if (!ws.length && lineStart != null) {
        words.push({ text: lineText, start: lineStart as number, end: lineEnd as number, line: lineIdx, wi: 0 });
        wordStart = lineStart as number;
        wordEnd = lineEnd as number;
        if (lineEnd != null && lineEnd > duration) duration = lineEnd;
      }
      lines.push({
        text: lineText,
        start: wordStart >= 0 ? (wordStart as number) : (lineStart as number),
        end: wordEnd >= 0 ? (wordEnd as number) : (lineEnd as number),
      });
      lineIdx++;
    }
  }

  words.sort((a, b) => (a.start - b.start) || (a.line - b.line) || (a.wi - b.wi));
  return { ok: words.length > 0, words, lines, duration };
}

/**
 * findActiveWordIndex(words, tMs, hint)
 *  返回 tMs 落在 [start,end) 的词索引；否则 -1（含词间隙）。
 *  hint=上次位置 → 指针推进 O(1) 摊还；否则二分 O(log n)。
 */
export function findActiveWordIndex(words: FlatWord[], t: number, hint?: number): number {
  if (!words || !words.length) return -1;

  if (typeof hint === "number" && hint >= 0 && hint < words.length) {
    let i = hint;
    if (t >= words[i].start) {
      while (i + 1 < words.length && words[i + 1].start <= t) i++;
      if (t < words[i].end) return i;
      if (t >= words[i].end) {
        let j = i;
        while (j + 1 < words.length && words[j + 1].start <= t) j++;
        if (j < words.length && t < words[j].end) return j;
        return -1;
      }
      return -1;
    } else {
      while (i > 0 && words[i].start > t) i--;
      if (i >= 0 && t >= words[i].start && t < words[i].end) return i;
      return -1;
    }
  }

  let lo = 0, hi = words.length - 1, ans = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (words[mid].start <= t) { ans = mid; lo = mid + 1; }
    else hi = mid - 1;
  }
  if (ans < 0) return -1;
  if (t < words[ans].end) return ans;
  let j = ans;
  while (j + 1 < words.length && words[j + 1].start <= t) j++;
  if (j < words.length && t < words[j].end) return j;
  return -1;
}

/**
 * findActiveLineIndex(lines, tMs, hint)
 *  返回 tMs 落在 [start,end] 的行索引；否则 -1。hint 用于 O(1) 摊还推进。
 */
export function findActiveLineIndex(lines: FlatLine[], t: number, hint?: number): number {
  if (!lines || !lines.length) return -1;

  if (typeof hint === "number" && hint >= 0 && hint < lines.length) {
    let i = hint;
    if (t >= lines[i].start) {
      while (i + 1 < lines.length && lines[i + 1].start <= t) i++;
      if (t <= lines[i].end) return i;
      return -1;
    } else {
      while (i > 0 && lines[i].start > t) i--;
      if (i >= 0 && t >= lines[i].start) return i;
      return -1;
    }
  }

  let lo = 0, hi = lines.length - 1, ans = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (lines[mid].start <= t) { ans = mid; lo = mid + 1; }
    else hi = mid - 1;
  }
  if (ans < 0) return -1;
  if (t <= lines[ans].end) return ans;
  return -1;
}
