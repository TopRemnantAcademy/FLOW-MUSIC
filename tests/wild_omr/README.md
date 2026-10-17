# `tests/wild_omr/` · 野上传 → 全链路 OMR 的 fail-closed 断言（issue #169）

> 上游：#165（门禁固化）、#163（G1 自动判定补强）。
> 门阈值唯一定义处：`music_rebuild_project/00_docs/gates/gate_G1.md`。

**一句话**：把「用户随手拍/随手截的乐谱图丢进来」这件事固化成可重复的测试，
并且断言三处**都不许说谎**：门禁要真拦、OMR 要真跑得出、结构校验要真验得到。

## 为什么单独放一个包（而不是塞进 `backend/tests/omr_e2e/`）

`omr_e2e` 用的是**替身**（MockOMR 等），验证的是编排与门的契约；
这里验证的是**真实工具**的诚实性：真 `g1_image_quality.py`、真 `g1_auto` 护栏、
真 `score_xml_validate.py`。两者混在一起，读的人分不清"哪条只是 mock 自说自话"。

## 三处断言

| 断言 | 测试 | 被钉住的事实 |
|---|---|---|
| ① 门禁诚实 FAIL | `test_wild_upload_gate.py` | 短边不足 / dpi 测不到的野图 → G1 `FAIL`（不是 SKIP、不是 NOTE）。放宽成 PASS 立刻红。 |
| ② oemer 有超时护栏 | `test_wild_upload_oemer_timeout.py` | 超过 `OEMER_TIMEOUT_S`（默认 420s，可用环境变量覆盖）→ 杀掉整个进程组 → `EngineUnavailable`；`collect_metrics` 把它记进 `errors`，绝不凭空返回线距。检查点未预热同样 fail-closed。 |
| ③ XML 校验不静默 PASS | `test_wild_upload_xml_validate.py` | 无拍号来源时 `verdict = PASS_WITH_NOTES` + 覆盖 `0/N` + 明说"未执行"；有声明来源才跑满校验；音长真错了必须 `FAIL`。 |

外加 `test_fixtures_are_anonymized.py` 钉住 fixture 的两个性质：**是脱敏副本**、
**短边不足**。谁把 fixture 换回原图让测试好过一点，这里先红。

## fixture（脱敏副本，勿换回原图）

| 文件 | 说明 |
|---|---|
| `fixtures/song01_preprocessed_v001_blur300.jpeg` | 由 `02_source/song01/preprocessed/` 派生：**半分辨率 + 高斯模糊 + q50**，音符/歌词不可读；保留 JFIF 300 dpi（让失败只因短边不足，而非 dpi 不可测）。1240×1754。 |
| `fixtures/wild_song01.musicxml` | 手写 4 小节 4/4，**无 `<time>`** —— 模拟 oemer 0.1.8 实测输出形态。 |
| `fixtures/wild_song01.manifest.md` | 声明拍号 4/4（来源之一，用于第 ③ 条的对照组）。 |

SHA-256 冻结在 `test_fixtures_are_anonymized.py`；改 fixture 必须同步改那里并在 PR 里说明原因。

## 怎么跑

```bash
# 裸环境（只装 pytest）：①要 Pillow → 相关用例 skip；③要 music21 → 整个文件 skip；
# ②的替身包不需要 numpy/PIL，照常跑。**本包不挂 PR/push**，正因为「可跑的部分」取决于装了什么。
python -m pytest tests/wild_omr/ -q

# 完整（推荐在 omr-qa 镜像 / api_trigger 里）
python tests/wild_omr/smoke_omr.py \
  --image tests/wild_omr/fixtures/song01_preprocessed_v001_blur300.jpeg \
  --assert-fail-closed tests/wild_omr/
```

`smoke_omr.py` 与 `smoke_cv.py` 的差别：OMR 引擎对野上传报错是**预期**（该被 G1 拦下），
所以不能简单地「errors 非空就红」；它只对**未归类**的异常报红（超时/检查点缺失/
无 MusicXML/非零退出视为"引擎活着但拒绝"），再跑一遍 pytest 保证失败没被跳过掩盖。

## 诚实的边界（这里没验的）

- **oemer 推理质量**：野上传本来就该被 G1 拦下，所以本包不断言"识别得多准"。
  在低分辨率模糊图上要求识别率，等于自欺。
- **超时阈值是否合理**：默认 420s 是"实测 ~2.4min/页 × 3"的经验值，不是测量结论；
  真页面的耗时分布需要在一批真实样本上统计（未做）。
- **OCR 抽拍号**：断言 ③ 的 `engines=()` 刻意绕过 OCR，只锁"无来源 → 不许 PASS"这条链。
