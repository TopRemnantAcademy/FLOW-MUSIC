#!/usr/bin/env python3
"""生成 FLOW CCM 的 Android 图标与启动图。

设计来源（不是自行设计）：
    C:/Desktop/Project/FLOW/mobile/icons/icon-512.svg  —— 用户既有 FLOW 品牌标识
    深绿圆角底 #12362f / 米白圆 #f7f3ea / 主色 #1f6f55 / 强调金 #c6923e

产出：
    res/mipmap-*/ic_launcher.png            各密度启动图标（含圆角）
    res/mipmap-*/ic_launcher_round.png      圆形图标
    res/mipmap-*/ic_launcher_foreground.png 自适应图标前景（内容缩至安全区）
    res/drawable*/splash.png                各方向/密度启动图

用法： python scripts/gen-brand-assets.py
"""

import os
import math
import pathlib

from PIL import Image, ImageDraw

BG = (18, 54, 47, 255)
CREAM = (247, 243, 234, 255)
GREEN = (31, 111, 85, 255)
GOLD = (198, 146, 62, 255)
SPLASH_BG = (18, 54, 47, 255)

# 自适应图标：内容须落在中心 66% 安全区内（半径 512*0.66/2=169）
# 2026-10-04：本标记最远点 ≈191 → 需缩到 169/191≈0.885，留余量取 0.86，
# 与主 App(FLOW) 的 FG_SCALE 对齐，两个 App 家族观感一致。
SAFE = 0.86

DENSITIES = {  # 目录后缀 -> 传统图标边长(px)
    "mdpi": 48,
    "hdpi": 72,
    "xhdpi": 96,
    "xxhdpi": 144,
    "xxxhdpi": 192,
}
# 自适应图标前景按 108dp 画布
FG_DENSITIES = {"mdpi": 108, "hdpi": 162, "xhdpi": 216, "xxhdpi": 324, "xxxhdpi": 432}

SPLASH_SIZES = {  # 目录 -> (宽, 高)
    "drawable": (480, 320),
    "drawable-land-mdpi": (480, 320),
    "drawable-land-hdpi": (800, 480),
    "drawable-land-xhdpi": (1280, 720),
    "drawable-land-xxhdpi": (1600, 960),
    "drawable-land-xxxhdpi": (1920, 1280),
    "drawable-port-mdpi": (320, 480),
    "drawable-port-hdpi": (480, 800),
    "drawable-port-xhdpi": (720, 1280),
    "drawable-port-xxhdpi": (960, 1600),
    "drawable-port-xxxhdpi": (1280, 1920),
}


def _ring_stamp(d, cx, cy, r, width, fill, steps=360):
    """等宽圆环（沿圆周密集落圆点 = round stroke）。"""
    rw = width / 2.0
    for i in range(steps + 1):
        t = 2 * math.pi * i / steps
        x, y = cx + r * math.cos(t), cy + r * math.sin(t)
        d.ellipse([x - rw, y - rw, x + rw, y + rw], fill=fill)


def draw_mark(img: Image.Image, box: tuple, rounded_bg: bool = True) -> None:
    """在 img 的 box 区域绘制品牌标识。

    2026-10-04 改版：중독예방(成瘾预防) 主题 —— 「두 개의 고리가 풀린다」
      两个原本交扣的环正在分开 = 成瘾的循环被打破、正在脱离。
      与主 App(FLOW) 同一几何，见 scripts/gen-brand-icons.py。
        环 A（左下·实心）= 尚未脱离的旧循环
        环 B（右上·描边）= 已经脱开的新状态
        金点（B 环孔内）  = 焦点
    造型语言：实心块 + 描边块 + 中间留白，纯平色（极简几何标识）。
    """
    x0, y0, x1, y1 = box
    d = ImageDraw.Draw(img)
    w, h = x1 - x0, y1 - y0
    side = min(w, h)
    k = side / 512.0

    def U(v):  # viewBox x → 이미지 x
        return x0 + v * k + (w - side) / 2.0

    def V(v):  # viewBox y → 이미지 y
        return y0 + v * k + (h - side) / 2.0

    if rounded_bg:
        d.rounded_rectangle(box, radius=int(96 * k), fill=BG)

    # 环 A：实心环（外圆填奶白，内孔用底色/透明挖穿）
    ax, ay = U(188), V(314)
    ao, ai = 100 * k, 52 * k
    d.ellipse([ax - ao, ay - ao, ax + ao, ay + ao], fill=CREAM)
    d.ellipse([ax - ai, ay - ai, ax + ai, ay + ai], fill=BG if rounded_bg else (0, 0, 0, 0))

    # 环 B：等宽描边环
    bx, by = U(334), V(190)
    _ring_stamp(d, bx, by, 70 * k, 38 * k, CREAM)

    # 焦点金点
    dr = 22 * k
    d.ellipse([bx - dr, by - dr, bx + dr, by + dr], fill=GOLD)


def draw_foreground(img: Image.Image) -> None:
    """自适应图标前景：透明底 + 居中缩小的标识（无底块）。"""
    w, h = img.size
    inner = int(w * SAFE)
    ox, oy = (w - inner) // 2, (h - inner) // 2
    draw_mark(img, (ox, oy, ox + inner, oy + inner), rounded_bg=False)


def main() -> None:
    here = pathlib.Path(__file__).resolve().parent
    res = here.parent / "android" / "app" / "src" / "main" / "res"
    if not res.is_dir():
        raise SystemExit(f"❌ 未找到 res 目录: {res}")

    # 1) 传统图标 + 圆形图标
    for dens, px in DENSITIES.items():
        d = res / f"mipmap-{dens}"
        d.mkdir(parents=True, exist_ok=True)

        sq = Image.new("RGBA", (px, px), (0, 0, 0, 0))
        draw_mark(sq, (0, 0, px, px))
        sq.save(d / "ic_launcher.png")

        rnd = Image.new("RGBA", (px, px), (0, 0, 0, 0))
        mask = Image.new("L", (px * 4, px * 4), 0)
        ImageDraw.Draw(mask).ellipse([0, 0, px * 4 - 1, px * 4 - 1], fill=255)
        mask = mask.resize((px, px), Image.LANCZOS)
        base = Image.new("RGBA", (px, px), (0, 0, 0, 0))
        draw_mark(base, (0, 0, px, px))
        rnd.paste(base, (0, 0), mask)
        rnd.save(d / "ic_launcher_round.png")

    # 2) 自适应图标前景（108dp 系列）
    for dens, px in FG_DENSITIES.items():
        d = res / f"mipmap-{dens}"
        d.mkdir(parents=True, exist_ok=True)
        fg = Image.new("RGBA", (px, px), (0, 0, 0, 0))
        draw_foreground(fg)
        fg.save(d / "ic_launcher_foreground.png")

    # 3) 启动图
    for folder, (w, h) in SPLASH_SIZES.items():
        d = res / folder
        d.mkdir(parents=True, exist_ok=True)
        sp = Image.new("RGBA", (w, h), SPLASH_BG)
        side = int(min(w, h) * 0.28)
        mark = Image.new("RGBA", (side, side), (0, 0, 0, 0))
        draw_mark(mark, (0, 0, side, side))
        sp.paste(mark, ((w - side) // 2, (h - side) // 2), mark)
        sp.save(d / "splash.png")

    print("✅ 品牌资产已生成")
    print(f"   图标密度: {', '.join(DENSITIES)}")
    print(f"   自适应前景: {', '.join(FG_DENSITIES)}")
    print(f"   启动图: {len(SPLASH_SIZES)} 个方向/密度")


if __name__ == "__main__":
    main()
