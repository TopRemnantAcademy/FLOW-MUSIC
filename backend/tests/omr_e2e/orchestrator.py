"""乐谱→音乐 编排器：A→B→C→[G2]→D→[G3]→E→F→[G4]→平台注册。

对齐 issue #9 的管道定义与 #122 的验收口径：
  阶段1→2→3→4 恒定顺序，**任一门 FAIL 即 halt**（fail-closed），
  报告里明确写出「停在哪一门、哪一项、实测值是多少」——绝不静默退出，也绝不编造绿灯。

与平台侧（Suno mock）编排的分工：
  - 本模块只管「乐谱→成品音频」这一段（真实世界靠 Audiveris/MuseScore/ACE/REAPER）；
  - 成品音频交给 `app.orchestrator`（`MockProvider`）注册成 Song，形成 FLOW-MUSIC 闭环。
  两段之间只通过「文件路径 + 元数据」耦合，所以把 provider 换成真实实现时，
  平台侧一行不用改。
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from typing import Any, Callable

from . import fixtures as fx
from .interfaces import (
    GATE_FAIL,
    GATE_PASS,
    Arranger,
    DAWEngine,
    Gate,
    GateReport,
    OMRProvider,
    ScoreProofer,
    VocalEngine,
)
from .mocks import (
    GateG1,
    GateG2,
    GateG3,
    GateG4,
    MockArranger,
    MockDAW,
    MockMuseScore,
    MockOMR,
    MockPreprocessor,
    MockVocalEngine,
    VOCAL_GAIN,
    dbfs,
    read_wav,
    seconds_for,
    select_winner,
    sha256_file,
    validate_arrangement,
    _master_pcm,
    write_wav,
)

#: 真实工具名（供 README / 报告说明「这一环真实世界用什么」）。
REAL_TOOLS = {
    "A": "ImageMagick / 扫描仪（#30 预处理）",
    "B": "Audiveris + 付费 OMR（#32/#33/#34）",
    "C": "MuseScore（#35 八步审校 + #36 往返）",
    "D": "ACE Studio / Synthesizer V（#43/#44/#45）",
    "E": "DAW MIDI 编排（#51/#55）",
    "F": "REAPER 脚本自动化（#58/#59/#60/#61）",
}


@dataclass
class PipelineResult:
    """一次端到端运行的全部产物与判定。

    `stage_reports` 逐阶段记录 `{status, summary}`，`gate_reports` 是四门的 GateReport；
    下游测试与评论里的「各门报告样例」直接取这两处，不做二次加工。
    """

    ok: bool
    song_id: str
    artifacts: dict[str, str] = field(default_factory=dict)
    gate_reports: dict[str, GateReport] = field(default_factory=dict)
    stage_reports: dict[str, dict] = field(default_factory=dict)
    reports: dict[str, Any] = field(default_factory=dict)
    halted_at: str | None = None
    halt_reason: str = ""
    registered_song_id: int | None = None

    def gate(self, name: str) -> GateReport:
        return self.gate_reports[name]

    def summary(self) -> dict:
        return {
            "ok": self.ok,
            "song_id": self.song_id,
            "halted_at": self.halted_at,
            "halt_reason": self.halt_reason,
            "gates": {k: v.status for k, v in self.gate_reports.items()},
            "artifacts": self.artifacts,
            "registered_song_id": self.registered_song_id,
        }


# ⚠ 这里**没有** PipelineHalt 异常：halt 由 `PipelineResult.halted_at` 表达。
# 门 FAIL 是预期结果（重拍 / 改谱 / 换引擎），不是异常。用返回值而不是异常，
# 就不会出现「某段忘了 try 就漏 halt」——那种漏法在测试里几乎看不出来。


def render_ground_truth_audio(truth_xml: str, out_path: str, *, measures: int, bpm: int) -> str:
    """把基准谱「渲染」成一个可测的确定性音频基准。

    这一步替换的是真实世界里「按 MIDI 导出参考音轨」。它存在的意义：
    让 G3 的音高/时长基准、E 段的编曲都有**同一份**素材可对齐，
    而不是让每个 mock 各自凭空造音频（那样时长与能量关系就对不上了）。
    """
    write_wav(out_path, _master_pcm(seconds_for(measures, fx.BEATS, bpm)))
    return out_path


def _fingerprint(wav_path: str) -> str:
    """渲染指纹：PCM 的 SHA-256（G3-8「参数可复现」的可测量形式）。"""
    info = read_wav(wav_path)
    h = hashlib.sha256()
    h.update(info["samples"].tobytes())
    return h.hexdigest()


def run_score_to_music(
    image_png: bytes,
    lyric: str,
    song_meta: dict,
    *,
    work_dir: str,
    register_song: Callable[[PipelineResult], int] | None = None,
    omr_candidates: list[OMRProvider] | None = None,
    proofer: ScoreProofer | None = None,
    vocal_engines: dict[str, VocalEngine] | None = None,
    arranger: Arranger | None = None,
    daw: DAWEngine | None = None,
    preprocess: dict | None = None,
    gate_g1: Gate | None = None,
    gate_g2: Gate | None = None,
    gate_g3: Gate | None = None,
    gate_g4: Gate | None = None,
) -> PipelineResult:
    """跑完整条流水线，返回产物 + 各门报告（含 halt 信息）。

    参数都可注入 —— 这是「可插拔」的落点：换成真实 provider 不需要改这个函数。
    `register_song` 是平台闭环钩子（由测试传入用 `TestClient` + `MockProvider` 的实现），
    本模块**不 import 任何 app.* 代码**、不碰网络、不碰 DB。
    """
    song_id = song_meta.get("song_id") or fx.SONG_ID
    bpm = int(song_meta.get("bpm", fx.BPM))
    measures = int(song_meta.get("measures", fx.MEASURES))
    baseline_measures = int(song_meta.get("baseline_measures", fx.BASELINE_MEASURES))
    os.makedirs(work_dir, exist_ok=True)

    result = PipelineResult(ok=False, song_id=song_id)
    truth = _truth_of(fx.CANON_MUSICXML)

    def artifact(name: str, path: str) -> str:
        result.artifacts[name] = path
        return path

    def stage(name: str, status: str, summary: str, **extra) -> None:
        result.stage_reports[name] = {"status": status, "summary": summary, "real_tool": REAL_TOOLS[name], **extra}

    def gate_and_halt(report: GateReport) -> bool:
        """记录门的结论并返回「是否可以继续」。

        返回布尔而不是抛异常：一个门失败是**预期内的结果**（重拍、改谱、换引擎），
        不是异常情况。用返回值表达，halt 的语义就不会被 try/except 的形状搞乱 ——
        也不会出现「某一段忘了包 try 就漏掉 halt」这种真事故。
        """
        result.gate_reports[report.gate] = report
        if report.status == GATE_FAIL:
            result.halted_at = report.gate
            result.halt_reason = (
                f"{report.gate} FAIL：{', '.join(report.failed_ids())}；"
                + "；".join(f"{it.id}={it.measured}" for it in report.items if it.failed)
            )
            return False
        return True

    # ---------------- 阶段 A：输入验证 / 图像预处理 → 门 G1 ----------------
    pre = preprocess or MockPreprocessor().preprocess(image_png, song_id=song_id)
    result.reports["preprocess_log"] = pre
    artifact("source_png", artifact_path(work_dir, "02_source", f"{song_id}_source_p001_v001.png", image_png))
    g1 = (gate_g1 or GateG1()).check(preprocess=pre)
    stage("A", g1[0], f"输入分类 + 预处理完成；G1 {g1[0]}：{_item_brief(g1[1])}", classify=pre.get("classify"))
    if not gate_and_halt(g1[1]):
        return result

    # ---------------- 阶段 B：OMR 候选 → 选胜者（#34） ----------------
    # A/B 的默认一对候选：两者都有错（真实世界不存在零错 OMR），胜负由计数决定。
    # 若两个候选都零错，选优就成了"谁都行" —— 那种"绿灯"没有信息量。
    candidates = omr_candidates or [
        MockOMR("audiveris", pitch_errors=1, rhythm_errors=0, lyric_errors=0),
        MockOMR("paid_api", pitch_errors=2, rhythm_errors=1, lyric_errors=1),
    ]
    scored = []
    raws = {}
    for cand in candidates:
        xml = cand.recognize(image_png, song_id=song_id)
        path = artifact_path(work_dir, "03_omr_raw", f"{song_id}_{cand.name}_v001.musicxml", xml)
        raws[cand.name] = path
        entry = {"name": cand.name, "raw_path": path, "xml_sha256": hashlib.sha256(xml.encode()).hexdigest()}
        if hasattr(cand, "score"):
            entry["score"] = cand.score(truth)
        else:  # 第三方实现：用与真值的逐项比对来评分（不依赖注入值）
            entry["score"] = _score_by_diff(xml, truth)
        scored.append(entry)
    selection = select_winner(scored)
    result.reports["omr_selection"] = {
        "winner": selection["winner"]["name"],
        "ranking": selection["ranking"],
        "candidates": scored,
        "basis": "#34：先比旋律+节奏错误数，再看歌词错误数（逐曲重测，不假设全局同优）",
    }
    winner_xml = _read(raws[selection["winner"]["name"]])
    stage(
        "B",
        GATE_PASS,
        f"OMR A/B 完成：胜者 {selection['winner']['name']}（{selection['ranking']}）",
        winner=selection["winner"]["name"],
    )

    # ---------------- 阶段 C：全数审校 → 门 G2 → ground_truth 冻结 ----------------
    proofer = proofer or MockMuseScore()
    # ⚠ 真实世界里「审校」就是**把出错的地方改对**（#35 八步）。mock 的替身也必须
    # 做完这一步，否则 G2-4「音高错误数 = 0」永远过不去 —— 那不是门严，是 mock 偷懒。
    # 因此 mock 的审校实现为「导出→再导入往返 + 按原谱（唯一裁判）校正 candidate」，
    # 并**如实记录**校了多少处（human_correction_time 的代理）。
    try:
        reviewed = proofer.proof(winner_xml, song_id=song_id)
        parse_error = ""
    except Exception as e:  # 真实世界 = MuseScore 打不开这个文件
        reviewed = winner_xml
        parse_error = f"{type(e).__name__}: {e}"
    corrections = _align_to_truth(reviewed, truth) if not parse_error else {"xml": reviewed, "count": 0, "kinds": []}
    reviewed = corrections["xml"]
    roundtrip_ok = _roundtrip_ok(reviewed)
    result.reports["review"] = {
        "corrections": corrections["count"],
        "kinds": corrections["kinds"],
        "roundtrip_ok": roundtrip_ok,
        "tool_parse_error": parse_error,
        "note": "mock 审校 = 往返一致 + 按原谱校正候选（真实实现是 MuseScore 里人工改谱）",
    }
    g2 = (gate_g2 or GateG2()).check(musicxml=reviewed, truth=truth, roundtrip_ok=roundtrip_ok)
    stage(
        "C",
        g2[0],
        f"审校完成（校正 {corrections['count']} 处"
        + (f"；打开文件报错 {parse_error}" if parse_error else "")
        + f"）；G2 {g2[0]}：{_item_brief(g2[1])}",
    )
    if not gate_and_halt(g2[1]):
        return result

    gt_path = artifact_path(work_dir, "04_approved", "ground_truth.musicxml", reviewed)
    gt_sha = sha256_file(gt_path)
    # 冻结定义（gate_G2.md）：`<sha256>  <file>  <utc_time>  <approver>`
    artifact_path(
        work_dir,
        "04_approved",
        "ground_truth.musicxml.sha256",
        f"{gt_sha}  ground_truth.musicxml  2026-10-13T00:00:00Z  mock-approver\n",
    )
    result.reports["ground_truth"] = {
        "sha256": gt_sha,
        "path": gt_path,
        "roundtrip_ok": roundtrip_ok,
        "measures": measures,
        "bpm": bpm,
    }

    # 参考音轨（真实世界 = 按 ground_truth 导出的 MIDI/参考音轨）
    render_ground_truth_audio(reviewed, os.path.join(work_dir, "04_approved", "reference.wav"),
                              measures=measures, bpm=bpm)

    # ---------------- 阶段 D：보컬 A/B → 门 G3 → 声乐冻结 ----------------
    engines = vocal_engines or {
        "ace": MockVocalEngine("ace", mode="cloud"),
        "synthv": MockVocalEngine("synthv", mode="offline"),
    }
    base_settings = {"bpm": bpm, "beats": fx.BEATS, "measures": baseline_measures, "seed": 20261013}
    renders = {}
    vocal_dir = os.path.join(work_dir, "05_vocals", song_id)
    for name, engine in engines.items():
        out = os.path.join(vocal_dir, f"pilot_{name}.wav")
        renders[name] = engine.render(reviewed, lyric, base_settings, out_path=out)
    scores, selected, reason = GateG3.score_ab(renders)
    result.reports["vocal_ab"] = {
        "scores": scores,
        "selected_engine": selected,
        "reason": reason,
        "dimensions": list(GateG3.AB_DIMENSIONS),
        "renders": {k: v.to_dict() for k, v in renders.items()},
    }
    if not selected:  # 两个引擎都不达标 → 门必然 FAIL，但仍要把报告给出来
        g3_fail = (gate_g3 or GateG3()).check(
            renders=renders,
            expected_seconds=seconds_for(baseline_measures, fx.BEATS, bpm),
            syllable_count=len(_syllables(reviewed)),
            mapping_errors=_lyric_mapping_errors(reviewed, lyric),
            onset_deviation_ms=0.0,
            pronunciation_scores={k: v["韩语发音明度"] for k, v in scores.items()},
            selected_engine="",
            selection_reason=reason,
            settings_ok=False,
            frozen_sha256="",
        )
        stage("D", g3_fail[0], f"보컬 A/B 未选出引擎；G3 {g3_fail[0]}：{_item_brief(g3_fail[1])}")
        gate_and_halt(g3_fail[1])
        return result
    selected_render = renders[selected]
    syl = _syllables(reviewed)
    mapping_errors = _lyric_mapping_errors(reviewed, lyric)
    expected_vocal_seconds = seconds_for(baseline_measures, fx.BEATS, bpm)
    approved = os.path.join(vocal_dir, "vocal_wav", "approved_vocal_pilot.wav")
    _copy(selected_render.wav_path, approved)
    approved_sha = sha256_file(approved)
    settings_path = os.path.join(vocal_dir, "vocal_settings.json")
    with open(settings_path, "w", encoding="utf-8") as f:
        json.dump(selected_render.settings | {"selected_engine": selected}, f, ensure_ascii=False, indent=2)
    # 哈希记录与 WAV **同目录同前缀**（真实流程里就是 `sha256sum > <file>.sha256`），
    # 放在别处会让「冻结的可信锚点」与它保护的文件分开漂移。
    with open(approved + ".sha256", "w", encoding="utf-8") as f:
        f.write(f"{approved_sha}  approved_vocal_pilot.wav\n")
    result.reports["vocal_settings"] = selected_render.settings

    g3 = (gate_g3 or GateG3()).check(
        renders=renders,
        expected_seconds=expected_vocal_seconds,
        syllable_count=len(syl),
        mapping_errors=mapping_errors,
        onset_deviation_ms=0.0,
        # 只把**参与 A/B 的引擎**分数送进门：G3-5 判的是「被采用的引擎」那条线，
        # 落选引擎即便明度更低也不该把整门拖红（#45 是「选定引擎 + 理由」的结论）。
        pronunciation_scores={
            k: scores[k]["韩语发音明度"] for k in engines
        },
        selected_engine=selected,
        selection_reason=reason,
        settings_ok=_settings_complete(selected_render.settings),
        frozen_sha256=approved_sha,
    )
    stage("D", g3[0], f"보컬 A/B 选定 {selected or '（无）'}；G3 {g3[0]}：{_item_brief(g3[1])}")
    if not gate_and_halt(g3[1]):
        return result
    artifact("approved_vocal_wav", approved)

    # ---------------- 阶段 E：编曲 ----------------
    arrangement = (arranger or MockArranger()).arrange(reviewed, lyric=lyric)
    problems = validate_arrangement(arrangement)
    if problems:
        result.halted_at = "E"
        result.halt_reason = "编曲结构非法：" + "；".join(problems)
        stage("E", GATE_FAIL, result.halt_reason)
        return result
    arr_path = artifact_path(
        work_dir, "06_arrangement", f"{song_id}_arrangement.json",
        json.dumps(arrangement, ensure_ascii=False, indent=2),
    )
    result.reports["arrangement"] = arrangement
    stage("E", GATE_PASS, f"编曲完成：{len(arrangement['sections'])} 段落 / {len(arrangement['chords'])} 小节和弦")

    # ---------------- 阶段 F：混音 → 门 G4 ----------------
    mix_dir = os.path.join(work_dir, "07_mix", song_id)
    mix = (daw or MockDAW()).mix(
        arrangement,
        {
            "approved_vocal_wav": approved,
            "reference_wav": os.path.join(work_dir, "04_approved", "reference.wav"),
            "ground_truth": gt_path,
        },
        out_dir=mix_dir,
    )
    # MockDAW 已经按 #13 命名（flow_demo_mix_v001.wav）产出 master，这里不再改名复制：
    # 复制到同名路径会把源文件截断成 0 字节（同名 copy 的经典陷阱），而 master 只是"最终成品"，
    # 没有"重命名"的需求 —— 真实流程里名字也是 DAW 导出时就定好的。
    master = mix["master"]
    assert os.path.basename(master) == "flow_demo_mix_v001.wav"
    qc = _qc_file_check(master, mix["stems"], approved, expected_vocal_seconds)
    result.reports["qc_file_check"] = qc
    listen = {s["name"]: True for s in arrangement["sections"]}
    deliverables = {
        "source_image": result.artifacts.get("source_png", ""),
        "ground_truth_musicxml": gt_path,
        "approved_vocal_wav": approved,
        "arrangement_json": arr_path,
        "master_wav": master,
    }
    g4 = (gate_g4 or GateG4()).check(
        master_path=master,
        stems=mix["stems"],
        vocal_path=approved,
        expected_seconds=expected_vocal_seconds,
        arrangement=arrangement,
        listen_checklist=listen,
        deliverables=deliverables,
    )
    artifact("master_wav", master)
    for name, path in mix["stems"].items():
        artifact(f"stem_{name}", path)
    artifact("arrangement_json", arr_path)
    artifact("ground_truth_musicxml", gt_path)
    stage("F", g4[0], f"混音完成；G4 {g4[0]}：{_item_brief(g4[1])}")
    if not gate_and_halt(g4[1]):
        return result

    # ---------------- 平台闭环：成品注册为 Song ----------------
    result.ok = True
    if register_song is not None:
        result.registered_song_id = register_song(result)
    return result


# ===========================================================================
# 辅助（都是纯函数，便于单独测）
# ===========================================================================
def artifact_path(work_dir: str, sub_dir: str, filename: str, content: str | bytes) -> str:
    """把产物写到 `<work_dir>/<sub_dir>/<filename>`，返回路径。

    子目录名刻意与 `music_rebuild_project/` 的目录约定一致
    （02_source / 03_omr_raw / 04_approved / 05_vocals / 06_arrangement / 07_mix），
    这样 mock 跑出来的产物结构，和真实流程的落位能一对一对照。
    """
    d = os.path.join(work_dir, sub_dir)
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, filename)
    # 防止手滑把 "a/b" 当文件名传进来（目录不存在 → 写到一半才炸）：
    # 名字里带分隔符一律先建目录，宁可看起来啰嗦。
    with open(path, "wb" if isinstance(content, bytes) else "w", **({} if isinstance(content, bytes) else {"encoding": "utf-8"})) as f:
        f.write(content)
    return path


def _copy(src: str, dst: str) -> str:
    os.makedirs(os.path.dirname(os.path.abspath(dst)), exist_ok=True)
    with open(src, "rb") as fi, open(dst, "wb") as fo:
        fo.write(fi.read())
    return dst


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()


def _truth_of(xml: str) -> dict:
    from .mocks import _xml_truth_values

    return _xml_truth_values(xml)


def _score_by_diff(xml: str, truth: dict) -> dict:
    """给「没有自报错误数」的第三方 OMR 候选打分：直接与真值 diff。

    这就是把 #34 的评分口子开放给真实 provider 的方式 —— 真实 Audiveris 不会告诉你
    它错了几个音，只能拿原谱（唯一裁判）去比。
    """
    from .mocks import _measure_durations, _pitch_errors, _xml_truth_values

    c = _xml_truth_values(xml)
    bad = [i + 1 for i, d in enumerate(_measure_durations(xml)) if d != fx.BEATS]
    return {
        "melody_rhythm_errors": _pitch_errors(c["pitches"], truth["pitches"]) + len(bad),
        "lyric_errors": len(set(truth["lyrics"]) - set(c["lyrics"])),
        "duration_errors": abs(c["measures"] - truth["measures"]),
    }


def _align_to_truth(xml: str, truth: dict) -> dict:
    """按原谱校正候选谱（审校的本质），并如实报告改了几处、改的是什么。

    实现上很朴素：逐音符比对 (step, octave)，不一致就把候选取值替换成原谱取值。
    之所以能这么做，是因为本包的 fixture 里「原谱」就是内置 canon 的字符串 ——
    真实世界里这一步是人拿原谱在 MuseScore 里改谱，机器替代不了，
    但**门要验的是「改完之后谱面对不对」**，所以这里把"改好"的结果喂给门，
    同时把"改了几处"记进报告（对应 G2-8 的人工修正耗时，作为工具选型轴）。
    """
    import re

    current = _truth_of(xml)["pitches"]
    target = truth["pitches"]
    kinds: list[str] = []
    for i, (cur, tgt) in enumerate(zip(current, target)):
        if tuple(cur) == tuple(tgt):
            continue
        old = f"<step>{cur[0]}</step>"
        new = f"<step>{tgt[0]}</step>"
        # 只替换第 i 个出现位置（以 step 出现顺序计）——避免把后面的同名字符一起改掉
        matches = [m.start() for m in re.finditer(re.escape(old), xml)]
        if i >= len(matches):
            continue
        pos = matches[i]
        xml = xml[:pos] + new + xml[pos + len(old):]
        if cur[1] != tgt[1]:
            oct_old = f"<octave>{cur[1]}</octave>"
            oct_new = f"<octave>{tgt[1]}</octave>"
            matches_o = [m.start() for m in re.finditer(re.escape(oct_old), xml)]
            if i < len(matches_o):
                pos_o = matches_o[i]
                xml = xml[:pos_o] + oct_new + xml[pos_o + len(oct_old):]
        kinds.append(f"音符#{i + 1} {cur[0]}{cur[1]}→{tgt[0]}{tgt[1]}")
    return {"xml": xml, "count": len(kinds), "kinds": kinds}


def _roundtrip_ok(xml: str) -> bool:
    """G2-6 导出-再导入往返一致：规范化 JSON diff（歌词/音符/小节/调号/拍号）。"""
    import xml.etree.ElementTree as ET

    def norm(text: str) -> dict:
        root = ET.fromstring(text)
        return {
            "measures": len(root.findall(".//measure")),
            "notes": len(root.findall(".//note")),
            "lyrics": [e.text or "" for e in root.findall(".//lyric/text")],
            "fifths": [e.text for e in root.findall(".//key/fifths")],
            "time": [(e.text, root.find(".//time/beat-type").text) for e in root.findall(".//time/beats")],
        }

    try:
        first = norm(xml)
        # 「再导入」= 把导出结果重新解析一遍再导出（MuseScore 的打开-另存为语义）
        second = norm(ET.tostring(ET.fromstring(xml), encoding="unicode"))
    except ET.ParseError:
        return False
    return first == second and first["lyrics"] == _truth_of(fx.CANON_MUSICXML)["lyrics"]


def _syllables(xml: str) -> list[str]:
    from .mocks import _xml_truth_values

    return [s for s in _xml_truth_values(xml)["lyrics"] if s]


def _lyric_mapping_errors(xml: str, lyric: str) -> int:
    """G3-3：音节-音符一一对应（本包 fixture 是 1 音符 ↔ 1 音节，可精确计）。

    真实世界里会有「1 音节-多音符」「延长」等情况，那时要给映射表；
    这里把「音符数 ≠ 音节数」以及「音节内容不一致」都算成错，宁严勿松。
    """
    expected = lyric.split()
    actual = _syllables(xml)
    if len(actual) != len(expected):
        return abs(len(actual) - len(expected)) or 1
    return sum(1 for a, b in zip(actual, expected) if a != b)


def _settings_complete(settings: dict) -> bool:
    """G3-8：引擎+版本 / 声库+版本 / 语言 / 主要参数 / 渲染模式 一个都不能少。"""
    need = (
        "engine",
        "engine_version",
        "voicebank",
        "voicebank_version",
        "language",
        "breathiness",
        "phoneme_mode",
        "dynamics",
        "mode",
    )
    return all(k in settings and settings[k] not in (None, "") for k in need)


def _qc_file_check(master: str, stems: dict, vocal: str, expected_seconds: float) -> dict:
    """#60 的文件验证脚本替身（覆盖/时长/频谱/静音/削波）。纯 stdlib。"""
    def probe(path: str) -> dict:
        info = read_wav(path)
        return {
            "exists": os.path.exists(path),
            "bytes": os.path.getsize(path),
            "duration": round(info["duration"], 3),
            "rms_dbfs": round(dbfs(info["rms"]), 2),
            "peak_dbfs": round(20.0 * __import__("math").log10(max(info["peak"], 1) / 32767.0), 2),
            "clipped_samples": info["clipped_samples"],
            "silent_blocks": info["silent_blocks"],
            "channels": info["channels"],
        }

    return {
        "master": probe(master),
        "stems": {k: probe(v) for k, v in stems.items()},
        "vocal": probe(vocal),
        "expected_seconds": expected_seconds,
        "tool": "mock-stdlib-wave（真实实现：#60 的 qc_file_check.py + ffprobe）",
    }


def _item_brief(report: GateReport) -> str:
    """把门报告压成一行（评论与 stage summary 用），细节留在 GateReport 里。"""
    failed = report.failed_ids()
    notes = [it.id for it in report.items if it.status != GATE_PASS]
    if failed:
        return f"FAIL 项 {failed}"
    if notes:
        return f"PASS_WITH_NOTES 项 {notes}"
    return f"{len(report.items)} 项全 PASS"
