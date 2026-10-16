#!/usr/bin/env python3
"""g1_image_quality.py · G1 图像质量验收门（可执行，纯 stdlib，复用 classify_input）(#31)

把 ``00_docs/gates/gate_G1.md`` 的 G1-1~G1-9 **能纯 stdlib 判定的部分**逐条跑成
``PASS`` / ``PASS_WITH_NOTES`` / ``FAIL`` / ``SKIP``，输出机器可读报告（JSON 或文本）。

   结论 = 有 FAIL ? FAIL
        : 有 NOTE ? PASS_WITH_NOTES
        : 有 SKIP ? PASS（但报告里标注「仍需人工」）
        : 全 PASS

    退出码  0 = 无 FAIL（SKIP 不算失败）；1 = 有 FAIL；2 = 用法/读取错误。

## 与既有能力的分工（只 import，不重复实现）

| 依赖 | 复用内容 | 本脚本**不**做的事 |
|---|---|---|
| ``tools/omr/classify_input.py`` | ``classify_file``（容器/子类型/宽高/dpi/sha256/handwriting） | 不重新解析魔数/PDF 结构 |
| ``tools/omr/classify_input.py`` | ``Container`` / ``SubType`` 枚举 | — |

## 哪些检查可纯 stdlib 做，哪些必须 SKIP（诚实标注，不冒充）

| 项 | 本工具 | 原因 |
|---|---|---|
| G1-1 有效分辨率（dpi/短边） | **做**（栅格/扫描件） | dpi 来自魔数，短边来自像素尺寸 |
| G1-1 五线谱线距 ≥8px | **SKIP** | 需像素直方图/傅里叶，超出纯 stdlib；由人工抽检或 #30 输出 |
| G1-2 歪斜角 | **SKIP** | 需 #30 deskew 输出或像素分析 |
| G1-3 裁切完整性 | **SKIP** | 逐页人工勾选清单 |
| G1-4 页面顺序/缺页 | **SKIP** | 需页码序列；命名强约束不在本工具范围 |
| G1-5 文字可辨性 | **SKIP** | 人工抽读 |
| G1-6 音符符号可区分 | **SKIP** | 人工抽检 ≥5 处/页 |
| G1-7 单页信息密度 | **SKIP** | 需 OMR 计数（系统数/小节数） |
| G1-8 分类与准入 | **做** | 容器/子类型可客观判定；手写标记恒为 requires_human |
| G1-9 原始文件只读 | **做** | 与 ``source_manifest.txt`` 比对 sha256 |

> 设计原则（对齐 P5）：精度只由测量声明。凡本工具打 PASS 的项都基于可测字段；
> SKIP 项一律在报告末尾单列「仍需人工」清单，绝不把 SKIP 当 PASS 计入门结论的通过项。

用法::

    # 单文件
    python tools/checks/g1_image_quality.py path/to/page.png
    # 目录（02_source/<song_id>/originals/）
    python tools/checks/g1_image_quality.py 02_source/song01/originals/ --json
    # 指定可选阈值
    python tools/checks/g1_image_quality.py dir/ --dpi-min 300 --short-min 2400

判定记录应落到 ``08_qc/gate_g1_report.md``；阈值快照落 ``08_qc/gate_config.json``（本工具
``--json`` 会一并输出 ``config`` 块供照抄）。
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, asdict
from pathlib import Path

# 自举：保证以 `python -m music_rebuild_project.tools.checks.g1_image_quality`
# 直接运行时，`omr.classify_input` 也能解析（tests 另有 conftest 注入，这里兜底）。
_TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

# 复用既有分类器（同一 tools/ 目录）
from omr.classify_input import classify_file, Container, SubType  # noqa: E402

# ---- 默认阈值（唯一来源：gate_G1.md；可调，但调整须记录到 gate_config.json）---------
DPI_MIN = 300          # G1-1 硬线：dpi ≥ 300
SHORT_MIN = 2400       # G1-1 硬线：单页短边 ≥ 2400 px
DPI_RELAXED = 200      # G1-1 放宽带：dpi ∈ [200,300) 仅可 PASS_WITH_NOTES
SHORT_RELAXED = 1800   # G1-1 放宽带：短边 ∈ [1800,2400) 仅可 PASS_WITH_NOTES


@dataclass
class Config:
    dpi_min: int = DPI_MIN
    short_min: int = SHORT_MIN
    dpi_relaxed: int = DPI_RELAXED
    short_relaxed: int = SHORT_RELAXED


STATUS_ORDER = {"FAIL": 3, "NOTE": 2, "PASS": 1, "SKIP": 0}


def _worse(a: str, b: str) -> str:
    return a if STATUS_ORDER[a] >= STATUS_ORDER[b] else b


def check_g1_1(c: dict, cfg: Config) -> tuple[str, str]:
    """G1-1 有效分辨率（dpi / 短边）。线距 ≥8px 这一子项纯 stdlib 不可测 → SKIP。"""
    container = c.get("container")
    subtype = c.get("subtype")
    if container == Container.UNKNOWN:
        return "SKIP", "不支持容器，由 G1-8 判定准入"
    if subtype == SubType.VECTOR_PDF:
        return "SKIP", "矢量 PDF 走导出路径，不进 OMR（G1-8 判定准入）"
    dpi = c.get("dpi")
    w = c.get("width") or 0
    h = c.get("height") or 0
    short = min(w, h)
    if dpi is None:
        return "FAIL", f"无法测得 dpi（容器={container}）；不能证明 ≥ 阈值（短边现存 {short}px）"
    if dpi >= cfg.dpi_min and short >= cfg.short_min:
        return "PASS", f"dpi={dpi}≥{cfg.dpi_min}，短边={short}≥{cfg.short_min}（线距≥8px 须人工/P30 验证）"
    if dpi >= cfg.dpi_relaxed and short >= cfg.short_relaxed:
        return ("NOTE",
                f"dpi={dpi}∈[{cfg.dpi_relaxed},{cfg.dpi_min}) 或短边={short}∈[{cfg.short_relaxed},{cfg.short_min}) "
                f"→ 仅可 PASS_WITH_NOTES，须记录")
    return "FAIL", f"dpi={dpi} 或短边={short} 不足（门槛 dpi≥{cfg.dpi_min}/短边≥{cfg.short_min}）"


def check_g1_8(c: dict) -> tuple[str, str]:
    """G1-8 分类与准入：手写/低质/畸变页不得进自动 OMR。"""
    container = c.get("container")
    subtype = c.get("subtype")
    if container == Container.UNKNOWN:
        return "FAIL", "不支持的容器格式 → 转人工录入（#65）"
    if subtype == SubType.UNKNOWN:
        return "FAIL", "分类未识别（可能加密/压缩流）→ 需人工确认，不得自动 OMR"
    note = "handwriting 恒=requires_human：须人工确认是否手写，未确认前不得自动 OMR"
    return "PASS", f"容器={container} 子类型={subtype} 可准入；{note}"


def parse_manifest(manifest_path: Path) -> dict[str, str]:
    """解析 ``source_manifest.txt``：每行 `<sha256>  <relpath>`（兼容 sha256sum -c 格式）。"""
    out: dict[str, str] = {}
    for raw in manifest_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(None, 1)
        if len(parts) == 2:
            out[parts[1].lstrip("*").strip()] = parts[0]
    return out


def check_g1_9(source_dir: Path, files: list[dict]) -> tuple[str, str]:
    """G1-9 原始文件只读：与 source_manifest.txt 的 sha256 比对。"""
    manifest = source_dir / "source_manifest.txt"
    if not manifest.exists():
        return "SKIP", "无 source_manifest.txt，跳过只读校验（应由 #13 生成后补跑）"
    expected = parse_manifest(manifest)
    rels = []
    for c in files:
        try:
            rel = str(Path(c["path"]).relative_to(source_dir))
        except ValueError:
            rel = Path(c["path"]).name
        rels.append(rel)
        exp = expected.get(rel)
        if exp is None:
            return "FAIL", f"清单外新增文件：{rel}"
        if exp.lower() != c.get("sha256", "").lower():
            return "FAIL", f"哈希不一致：{rel}"
    if len(expected) > len(rels):
        return "FAIL", f"清单中存在 {len(expected) - len(rels)} 个缺失文件"
    return "PASS", f"全部 {len(rels)} 个文件哈希与 source_manifest.txt 一致"


def evaluate_path(path: Path, cfg: Config, auto: bool = False, provider=None) -> dict:
    """对单文件跑 G1-1 与 G1-8（G1-9 是目录级，另算）。

    ``auto=True`` 时追加 **实测自动判定**：G1-1L(线距)/G1-2/G1-3/G1-5/G1-6/G1-7。
    引擎不可用 → 该项保持 ``SKIP`` 并注明，绝不冒充 PASS（P5）。
    """
    c = classify_file(path)
    g1_1 = check_g1_1(c, cfg)
    g1_8 = check_g1_8(c)
    checks = {
        "G1-1": {"status": g1_1[0], "detail": g1_1[1]},
        "G1-8": {"status": g1_8[0], "detail": g1_8[1]},
    }
    auto_metrics = None
    if auto:
        from checks import g1_auto  # 懒加载：仅 --auto 时引入（重依赖在 g1_auto 内再懒加载）
        ac = g1_auto.auto_checks(path, provider=provider)
        auto_metrics = ac.pop("_metrics", None)
        checks.update(ac)
    file_status = _worse(g1_1[0], g1_8[0])
    for k in AUTO_FILE_KEYS:
        if k in checks:
            file_status = _worse(file_status, checks[k]["status"])
    return {
        "path": str(path),
        "container": c.get("container"),
        "subtype": c.get("subtype"),
        "width": c.get("width"),
        "height": c.get("height"),
        "dpi": c.get("dpi"),
        "sha256": c.get("sha256"),
        "auto_metrics": auto_metrics,
        "checks": checks,
        "status": file_status,
    }


def evaluate_dir(directory: Path, cfg: Config, auto: bool = False, provider=None,
                 expected_pages: int | None = None) -> dict:
    files = []
    errors = []
    for f in sorted(directory.rglob("*")):
        if not f.is_file() or f.name == ".gitkeep":
            continue
        if f.name == "source_manifest.txt":
            continue
        try:
            files.append(evaluate_path(f, cfg, auto=auto, provider=provider))
        except (ValueError, OSError) as e:
            errors.append({"path": str(f), "error": str(e)})
    g1_9 = check_g1_9(directory, files)
    rep = {
        "root": str(directory),
        "config": asdict(cfg),
        "mode": "auto" if auto else "static",
        "G1-9": {"status": g1_9[0], "detail": g1_9[1]},
        "files": files,
        "errors": errors,
    }
    if auto:
        from checks import g1_auto
        entries = [(Path(f["path"]).name, (f.get("auto_metrics") or {}).get("page_no")) for f in files]
        st, detail = g1_auto.verdict_order(entries, expected_pages=expected_pages)
        rep["G1-4"] = {"status": st, "detail": detail}
        rep["skipped_checks"] = _auto_skipped(files, rep)
    else:
        rep["skipped_checks"] = _STATIC_SKIPPED
    return rep


_STATIC_SKIPPED = [
    "G1-1 线距≥8px（需像素分析）",
    "G1-2 歪斜角（需 #30 deskew 输出）",
    "G1-3 裁切完整性（人工勾选）",
    "G1-4 页面顺序/缺页（需页码序列）",
    "G1-5 文字可辨性（人工抽读）",
    "G1-6 音符符号可区分（人工抽检）",
    "G1-7 单页信息密度（需 OMR 计数）",
]

AUTO_FILE_KEYS = ("G1-1L", "G1-2", "G1-3", "G1-5", "G1-6", "G1-7")

_SKIP_NOTES = {
    "G1-1L": "G1-1 线距≥8px（需 OMR 引擎）",
    "G1-2": "G1-2 歪斜角（需 OMR/deskew）",
    "G1-3": "G1-3 裁切完整性（需 OCR/CV 边界测量）",
    "G1-4": "G1-4 页面顺序/缺页（需页码 OCR）",
    "G1-5": "G1-5 文字可辨性（需 OCR）",
    "G1-6": "G1-6 音符符号可区分（需 OMR 符号）",
    "G1-7": "G1-7 单页信息密度（需 OMR 计数）",
}


def _auto_skipped(files: list[dict], rep: dict) -> list[str]:
    """auto 模式下动态「仍需人工」清单：只列**实际 SKIP** 的项（SKIP 绝不冒充 PASS）。"""
    out: list[str] = []
    for key, note in _SKIP_NOTES.items():
        if key == "G1-4":
            if rep.get("G1-4", {}).get("status") == "SKIP" and note not in out:
                out.append(note)
            continue
        if any(f["checks"].get(key, {}).get("status") == "SKIP" for f in files) and note not in out:
            out.append(note)
    return out


def gate_verdict(rep: dict) -> str:
    """目录级结论：任一项 FAIL → FAIL；否则有 NOTE → PASS_WITH_NOTES；否则 PASS。"""
    worst = "PASS"
    for f in rep["files"]:
        worst = _worse(worst, f["status"])
    # 目录级 G1-9 也计入
    worst = _worse(worst, rep["G1-9"]["status"])
    # 目录级 G1-4（--auto 时的跨页顺序/缺页）同样计入
    if "G1-4" in rep:
        worst = _worse(worst, rep["G1-4"]["status"])
    if worst == "FAIL":
        return "FAIL"
    if worst == "NOTE":
        return "PASS_WITH_NOTES"
    # 仍有 SKIP（G1-9 缺清单 或 各文件 G1-1 对矢量件 SKIP）→ 仍记为 PASS 但附人工清单
    return "PASS"


def _render_text(rep: dict) -> str:
    lines = [
        "G1 image quality gate",
        "  root: %s" % rep["root"],
        "  config: dpi_min=%(dpi_min)s short_min=%(short_min)s dpi_relaxed=%(dpi_relaxed)s short_relaxed=%(short_relaxed)s" % rep["config"],
        "  G1-9: %s - %s" % (rep["G1-9"]["status"], rep["G1-9"]["detail"]),
        "  files:",
    ]
    for f in rep["files"]:
        lines.append("    - %s" % f["path"])
        lines.append("        container=%s subtype=%s dpi=%s size=%sx%s -> %s"
                     % (f["container"], f["subtype"], f["dpi"], f["width"], f["height"], f["status"]))
        for k, v in f["checks"].items():
            lines.append("          %s: %s - %s" % (k, v["status"], v["detail"]))
    for e in rep.get("errors", []):
        lines.append("    ! error: %s - %s" % (e["path"], e.get("error")))
    lines.append("  verdict: %s" % gate_verdict(rep))
    lines.append("  still-requires-human:")
    for s in rep["skipped_checks"]:
        lines.append("    - %s" % s)
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="g1_image_quality.py",
        description="G1 图像质量验收门（复用 classify_input，纯 stdlib）。",
    )
    ap.add_argument("path", help="单文件或目录（目录会递归，跳过 .gitkeep 与 source_manifest.txt）")
    ap.add_argument("--dpi-min", type=int, default=DPI_MIN)
    ap.add_argument("--short-min", type=int, default=SHORT_MIN)
    ap.add_argument("--dpi-relaxed", type=int, default=DPI_RELAXED)
    ap.add_argument("--short-relaxed", type=int, default=SHORT_RELAXED)
    ap.add_argument("--json", action="store_true", help="emit JSON instead of text")
    ap.add_argument("--auto", action="store_true",
                    help="启用实测自动判定（OMR/OCR）；引擎不可用时对应项保持 SKIP（绝不冒充 PASS）")
    ap.add_argument("--expected-pages", type=int, default=None,
                    help="源声明总页数（G1-4 证明「缺页=0」的必要条件；不给则单页只能 NOTE）")
    args = ap.parse_args(argv)
    cfg = Config(dpi_min=args.dpi_min, short_min=args.short_min,
                 dpi_relaxed=args.dpi_relaxed, short_relaxed=args.short_relaxed)

    p = Path(args.path)
    if not p.exists():
        sys.stderr.write("path not found: %s\n" % p)
        return 2
    try:
        if p.is_dir():
            rep = evaluate_dir(p, cfg, auto=args.auto, expected_pages=args.expected_pages)
            rep["verdict"] = gate_verdict(rep)
        else:
            c = evaluate_path(p, cfg, auto=args.auto)
            rep = {
                "root": str(p),
                "config": asdict(cfg),
                "mode": "auto" if args.auto else "static",
                "G1-9": {"status": "SKIP", "detail": "单文件模式不做目录级只读校验"},
                "files": [c],
                "errors": [],
            }
            if args.auto:
                rep["G1-4"] = {"status": "SKIP", "detail": "单文件模式不做跨页顺序校验"}
                rep["skipped_checks"] = _auto_skipped([c], rep)
            else:
                rep["skipped_checks"] = [
                    "G1-1 线距≥8px", "G1-2 歪斜角", "G1-3 裁切完整性",
                    "G1-4 页面顺序/缺页", "G1-5 文字可辨性",
                    "G1-6 音符符号可区分", "G1-7 单页信息密度",
                ]
            rep["verdict"] = gate_verdict(rep)
    except (ValueError, OSError) as e:
        sys.stderr.write("read error: %s\n" % e)
        return 2

    if args.json:
        print(json.dumps(rep, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(_render_text(rep))
    return 0 if rep["verdict"] != "FAIL" else 1


if __name__ == "__main__":
    raise SystemExit(main())
