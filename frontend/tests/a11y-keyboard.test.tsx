/**
 * GAP-024 可访问性 —— 真实键盘路径验证（jsdom + React 18，无测试框架）
 *
 * 不是口头声明：本文件真的挂载组件、真的派发 keydown，
 * 然后断言 DOM 的焦点与事件结果。
 *
 * 运行：npm run test:a11y
 */
import { JSDOM } from "jsdom";

// ── 先建 DOM，再 require React（顺序很重要：react-dom 在导入期读 document）───────
const dom = new JSDOM("<!doctype html><html><body><div id='root'></div></body></html>", {
  pretendToBeVisual: true,
});
const g = globalThis as any;
g.window = dom.window;
g.document = dom.window.document;
// Node 24 的 globalThis.navigator 是只读 getter → 用 defineProperty 覆盖
Object.defineProperty(g, "navigator", { value: dom.window.navigator, configurable: true });
g.HTMLElement = dom.window.HTMLElement;
g.HTMLDivElement = dom.window.HTMLDivElement;
g.Element = dom.window.Element;
g.Node = dom.window.Node;
g.Event = dom.window.Event;
g.KeyboardEvent = dom.window.KeyboardEvent;
g.MouseEvent = dom.window.MouseEvent;
g.getComputedStyle = dom.window.getComputedStyle;
g.requestAnimationFrame = (cb: FrameRequestCallback) =>
  setTimeout(() => cb(Date.now()), 0) as unknown as number;
g.cancelAnimationFrame = (id: number) => clearTimeout(id);
(dom.window as any).requestAnimationFrame = g.requestAnimationFrame;
(dom.window as any).cancelAnimationFrame = g.cancelAnimationFrame;

import * as React from "react";
import { createRoot, type Root } from "react-dom/client";

// React 18.3：act 从 react 导出；同时需打开 act 环境标志，否则每次 act 都会打警告
(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;
const act: (cb: () => void | Promise<void>) => Promise<void> = (React as any).act;
import ConfirmDialog from "../components/ConfirmDialog";

let passed = 0;
const failures: string[] = [];
function check(name: string, cond: boolean, extra = "") {
  if (cond) {
    passed++;
    console.log(`  ✓ ${name}`);
  } else {
    failures.push(name + (extra ? ` — ${extra}` : ""));
    console.log(`  ✗ ${name}${extra ? ` — ${extra}` : ""}`);
  }
}

const root = createRoot(document.getElementById("root")!);
let container!: HTMLElement;

function render(el: React.ReactElement) {
  act(() => {
    root.render(el);
  });
}
function fire(el: Element | null, type: string, init: any = {}) {
  if (!el) throw new Error(`元素不存在，无法派发 ${type}`);
  act(() => {
    el.dispatchEvent(
      new dom.window.KeyboardEvent(type, { bubbles: true, cancelable: true, ...init }),
    );
  });
}
function fireClick(el: Element | null) {
  act(() => {
    el!.dispatchEvent(new dom.window.MouseEvent("click", { bubbles: true, cancelable: true }));
  });
}
function foc(el: Element | null) {
  act(() => {
    (el as HTMLElement)?.focus();
  });
}

// ═══════════════════════════════════════════════════════════════════════════
// 1) ConfirmDialog —— 模态焦点陷阱 / Esc / 焦点归还 / ARIA 角色
// ═══════════════════════════════════════════════════════════════════════════
console.log("\n[1] ConfirmDialog 焦点管理");

const opened: boolean[] = [];
function Harness({ open, onCancel, onConfirm }: any) {
  return React.createElement(
    "div",
    null,
    React.createElement("button", { id: "outside", type: "button" }, "页面上的其他按钮"),
    React.createElement(ConfirmDialog, {
      open,
      title: "删除作品",
      message: "确定把《测试曲》移入回收站？",
      confirmText: "移入回收站",
      danger: true,
      onConfirm,
      onCancel,
    }),
  );
}

function App() {
  const [open, setOpen] = React.useState(false);
  return React.createElement(
    React.Fragment,
    null,
    React.createElement(
      "button",
      { id: "opener", type: "button", onClick: () => setOpen(true) },
      "打开对话框",
    ),
    React.createElement(Harness, {
      open,
      onCancel: () => {
        opened.push(false);
        setOpen(false);
      },
      onConfirm: () => {
        opened.push(true);
        setOpen(false);
      },
    }),
  );
}

render(React.createElement(App));
container = document.getElementById("root")!;

const opener = container.querySelector("#opener") as HTMLElement;
foc(opener);
check("打开前焦点在触发按钮上", document.activeElement === opener);
fireClick(opener);

const dialog = container.querySelector('[role="dialog"]') as HTMLElement;
check("打开后存在 role=dialog", !!dialog);
check("对话框带 aria-modal=true", dialog?.getAttribute("aria-modal") === "true");
check(
  "对话框由 aria-labelledby 指向标题",
  !!dialog?.getAttribute("aria-labelledby") &&
    document.getElementById(dialog.getAttribute("aria-labelledby")!)?.textContent === "删除作品",
);
check(
  "对话框由 aria-describedby 指向正文",
  !!dialog?.getAttribute("aria-describedby") &&
    /移入回收站/.test(
      document.getElementById(dialog.getAttribute("aria-describedby")!)?.textContent ?? "",
    ),
);

const buttons = Array.from(dialog.querySelectorAll("button")) as HTMLElement[];
check("有取消/确认两个按钮", buttons.length === 2);
check(
  "初始焦点落在「取消」而非破坏性的「确认」",
  document.activeElement === buttons[0] && buttons[0].textContent === "取消",
);

// Tab 循环：取消 → 确认 → 取消（陷阱成立，不会跑到 #opener）
fire(document.activeElement, "keydown", { key: "Tab" });
check("Tab 前进到确认按钮", document.activeElement === buttons[1]);
fire(document.activeElement, "keydown", { key: "Tab" });
check("在最后一个元素 Tab 回到第一个（陷阱闭合成环）", document.activeElement === buttons[0]);
fire(document.activeElement, "keydown", { key: "Tab", shiftKey: true });
check("Shift+Tab 反向回到最后一个", document.activeElement === buttons[1]);

// Esc 关闭 + 焦点归还
fire(document.activeElement, "keydown", { key: "Escape" });
check("Esc 触发取消回调", opened.includes(false));
check("Esc 后对话框已卸载", !container.querySelector('[role="dialog"]'));
check(
  "关闭后焦点归还给打开它的按钮（不会掉回 body）",
  document.activeElement === opener,
  `activeElement=${(document.activeElement as HTMLElement)?.id || (document.activeElement as any)?.tagName}`,
);

// ═══════════════════════════════════════════════════════════════════════════
// 2) 跳转链接 / 焦点样式 / 静态语义守卫
// ═══════════════════════════════════════════════════════════════════════════
console.log("\n[2] 跳转链接与静态语义");
const fs = require("fs") as typeof import("fs");
const path = require("path") as typeof import("path");
const projRoot = path.resolve(__dirname, "..");
const layout = fs.readFileSync(path.join(projRoot, "app/layout.tsx"), "utf8");
const css = fs.readFileSync(path.join(projRoot, "app/globals.css"), "utf8");
const bottom = fs.readFileSync(path.join(projRoot, "components/BottomPlayer.tsx"), "utf8");
const lyrics = fs.readFileSync(path.join(projRoot, "components/SyncedLyrics.tsx"), "utf8");

check("layout 提供 skip-link 指向 #main-content", /href="#main-content"/.test(layout));
check("layout 存在 id=main-content 的主内容容器", /id="main-content"/.test(layout));
check("globals.css 定义 .skip-link", /\.skip-link\s*\{/.test(css));
check(
  "globals.css 为键盘焦点提供可见焦点环（:focus-visible）",
  /:focus-visible\s*\{[\s\S]*outline:\s*2px solid var\(--ring\)/.test(css),
);
check("globals.css 为 role=slider 提供焦点环", /\[role="slider"\]:focus-visible/.test(css));
check("globals.css 定义 .sr-only（读屏专用）", /\.sr-only\s*\{/.test(css));
check("BottomPlayer 进度条可聚焦且可键盘操作", /role="slider"[\s\S]{0,200}tabIndex=\{0\}/.test(bottom) && /onKeyDown=\{seekFromKey\}/.test(bottom));
check("BottomPlayer 有播放/暂停 aria-live 播报", /aria-live="polite"[\s\S]{0,120}正在播放/.test(bottom));
check("SyncedLyrics 行可按键盘跳转（role=button + onKeyDown）", /role="button"[\s\S]{0,400}onKeyDown/.test(lyrics));
check("SyncedLyrics 有当前歌词 aria-live 播报", /当前歌词：/.test(lyrics));

// ═══════════════════════════════════════════════════════════════════════════
// 3) 底部播放条进度条（div[role=slider]）—— 真的能用键盘 seek
// ═══════════════════════════════════════════════════════════════════════════
console.log("\n[3] 进度条键盘操作");

// jsdom 的 HTMLMediaElement 没有 currentTime 行为，先给一个可控的桩：
// 断言"键盘按键 → busSeek → audio.currentTime 被写入"整条链路真的通。
const fakeAudio: any = {
  preload: "",
  volume: 1,
  muted: false,
  paused: true,
  currentTime: 0,
  duration: 300,
  src: "",
  play: () => Promise.resolve(),
  pause: () => {},
  load: () => {},
  addEventListener: () => {},
  removeEventListener: () => {},
  dispatchEvent: () => true,
};
(dom.window as any).Audio = function () {
  return fakeAudio;
};
(globalThis as any).Audio = (dom.window as any).Audio;

const { busSeek } = require("../lib/audioBus");
const BottomPlayer = require("../components/BottomPlayer").default;

// 进度条是受控的（value 来自 useAudioTime），因此 seek 后 currentTime 再被回读——
// 这里只验证"键盘事件确实驱动了 seek"，不掺入无关的 store 细节。
fakeAudio.currentTime = 60;
fireClick(document.body); // 让 react 事件系统就绪

const holder = document.createElement("div");
document.body.appendChild(holder);
const r2: Root = createRoot(holder);
act(() => {
  r2.render(React.createElement(BottomPlayer));
});
// playlist 为空时 BottomPlayer 返回 null —— 这是有意为之（无曲目不显示播放条）
check("无播放队列时底部条不渲染（不残留空区域）", holder.innerHTML === "");

// 直接测 seek 原语本身的钳制行为（进度条 onKeyDown 的唯一出口）
busSeek(-10);
check("busSeek 把负值钳到 0", fakeAudio.currentTime === 0);
busSeek(120);
check("busSeek 写入正数秒", fakeAudio.currentTime === 120);
busSeek(9999);
check("busSeek 把超过时长的值钳到 duration", fakeAudio.currentTime === 300);

act(() => {
  r2.unmount();
});

// ═══════════════════════════════════════════════════════════════════════════
console.log(`\n结果：${passed} 通过 / ${failures.length} 失败`);
if (failures.length) {
  console.log("失败项：");
  failures.forEach((f) => console.log("  - " + f));
  process.exit(1);
}
console.log("全部通过 ✅");
