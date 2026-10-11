# CVE-2026-40347 告警过期核查（2026-09-25）

CNB 安全扫描（告警 ID `2103243173777756160`、TCA code_issue、`VUL_WARN`）报
`ai-music-platform/backend/requirements.txt:10` 的 `python-multipart@0.0.12`
存在 CVE-2026-40347，推荐修复版本 `0.0.26`。

**核查结论：过期扫描残留，无需改动。** 该 CVE 只要求 ≥ `0.0.26`，
而文件当前是 `python-multipart==0.0.32`（`:16`）。

## 依据

| 项 | 值 |
|---|---|
| 扫描 revision | `9873e0476b9d5598b0f7135c983740878795fbaf` |
| 扫描时点该文件内容 | `python-multipart==0.0.12`（commit `9873e04`） |
| 当前该文件内容 | `python-multipart==0.0.32`（`:16`，`7da4950` 落地；合入 master 前曾是 `0.0.31`） |
| CVE-2026-40347 修复版本 | `0.0.26` → **0.0.32 已覆盖** |

TCA 读的是**扫描时点**的文件内容，扫描结果不随文件改动自动关闭，
因此该告警文本描述的版本与当前文件必然错位（同类现象见 `docs/CNB_CI.md`
的「TCA 依赖扫描」一节）。

## 仓库内四处声明均 ≥ 0.0.26

```
ai-music-platform/backend/requirements.txt:16   python-multipart==0.0.32
requirements.txt:7                              python-multipart==0.0.31
requirements-313-local.txt:7                    python-multipart==0.0.31
requirements-deploy.txt:19                      python-multipart==0.0.32  ← Docker 实际安装
```

全仓已无 `0.0.12` 的任何声明，`0.0.12` 仅作为历史记录残留在本文与
`SECURITY_AUDIT_2026-09-13.md`（该文档记录的是 9-13 审计时点的状态）、
`docs/CNB_CI.md` 的溯源表中。

## 处置

- **不为本 CVE 改动版本号**：`0.0.32` = 已修复（≥ 0.0.26），且与小仓测试面、部署面同版。
- **不按卡片字面改成 `0.0.26`**：那是降级（`0.0.26` < `0.0.32`），任务约束禁止。
- **不降级**：`0.0.12` / `0.0.26` 均低于本仓四处声明的实际值。

> 关于 `0.0.31` → `0.0.32`：那一步是 master 上 CVE-2026-53539（`7da4950`）的处置结果，
> 与本 CVE 无关；合并 master 时保留 `0.0.32`（**上调，不是降级**），
> 因此本文件的结论「本 CVE 无需版本变更」依然成立。详见 `docs/CNB_CI.md`。

## 需要人工确认的一点

`docs/CNB_CI.md:67` 与 `:77` 的溯源表仍写着「`backend/requirements.txt:10` 确实是
`python-multipart==0.0.12`」，那是描述扫描 revision 时点的历史事实，不是当前状态的断言，
本次不改（改写会让「为什么告警版本与文件对不上」的解释失去支点）。

**过期扫描残留这一判定本身需人工确认**：若平台重扫后该告警仍不关闭，
说明扫描 revision 被固定在校验时点，需按平台侧规则手动归档。
