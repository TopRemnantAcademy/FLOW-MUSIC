# `tools/naming/` · 命名规范校验 + 批准本冻结规则（#13）

> 规则的**唯一定义处**是文档，不是这里的代码：
> `00_docs/gates/gate_G2.md`（冻结定义）、`gate_G3.md` G3-10、`gate_G4.md` G4-10、
> `00_docs/glossary.md`（song_id）、`README.md`（文件命名 / Windows 路径风险规避）。
> 本目录把它们变成**可执行**的校验与冻结函数；冲突时以文档为准。

一句话：**#13 的命名与冻结不能只停在文档上。**
文档管不住任何一个文件名，函数管得住。

```bash
python -m pytest music_rebuild_project/tools/naming/tests/ -q    # 98 passed
```

---

## 放什么

| 文件 | 职责 |
|---|---|
| `naming.py` | 命名规范校验：song_id 形态、版本后缀 `vNNN`、G4-10 master 命名、禁用字符；返回**结构化违规列表** |
| `freeze.py` | 冻结与校验：写 `<file>.sha256`（gate_G2 四段格式）、拒绝原地覆盖、比对哈希、版本派生 |
| `tests/test_naming_freeze.py` | 以上两者的测试（tempfile，不碰真实媒体、不写数据目录） |

## 不放什么

- 任何音频 / 乐谱 / 工程文件
- 判定记录 → `08_qc/`
- 数据目录的读写 —— 本目录**只提供函数**，落点由调用方决定

## 只读规则

可迭代（L1）。工具**不写任何数据目录**：不替调用方决定文件落在哪。
测试一律用 `tempfile` 造临时文件（`tests/test_naming_freeze.py` 里有一条 AST 扫描的
用例把「模块不得写数据目录」钉死，而不是靠"我记得没写"）。

---

## 1. 用法

```python
import naming, freeze

# —— 命名校验：拿到违规列表，不是布尔 ——
[Violation(code='E_NO_VERSION', path='song01_review.musicxml', ...)]
naming.validate("song01_review.musicxml")                       # 交付物形态
naming.validate("song01_mix_v001.wav", kind="master_mix")       # G4-10
naming.validate("ground_truth_v002.musicxml", kind="frozen")    # 04_approved/ 冻结件
naming.validate("02_source/song1/x.tif", kind="repo_path")      # 相对路径 + 目录约定
naming.validate("song001", kind="song_id")                      # ≤99 两位，>99 三位

# —— 冻结 ——
freeze.freeze_file("/abs/04_approved/song01/ground_truth.musicxml", approver="张三")
#   → 写 <file>.sha256，并置只读位；已有记录则抛 AlreadyFrozenError（附 next_path）

r = freeze.verify_frozen("/abs/.../ground_truth.musicxml")
r.ok, r.status   # False, 'hash_mismatch' —— 被篡改

freeze.derive_next_path("/abs/.../ground_truth.musicxml")
#   → /abs/.../ground_truth_v002.musicxml
```

**为什么返回违规列表而不是抛异常/返回布尔**：调用方要一次拿到全部问题去写报告，
而不是修一个跑一次。唯一抛异常的情况是路径本身没法校验（绝对路径、`..` ）——
那时静默返回空列表等于假装合规。

### 违规码

| 码 | 触发 |
|---|---|
| `E_SONG_ID_FORM` | 不是 `songNN` / `songNNN`（`song1`、`song001`、`Song01`） |
| `E_NO_VERSION` | 正式交付物没带版本号 |
| `E_VERSION_FORM` | 版本号不是定长三位（`_v1`、`_v0001`） |
| `E_NAME_FORM` | 不符 `deliverables.md` 的交付物形态 |
| `E_MASTER_MIX_NAME` | 不符 G4-10 的 `<song_id>_mix_vNNN.wav` |
| `E_FROZEN_NAME` | 不是 gate_G2 认可的冻结件名 |
| `E_NON_ASCII` / `E_UPPER_CASE` / `E_SPACE` / `E_ILLEGAL_CHAR` / `E_RESERVED_NAME` / `E_SEGMENT_TOO_LONG` / `E_TRAILING_DOT_OR_SPACE` | README「Windows 路径风险规避」1~4 条 |
| `E_PATH_ESCAPE` | 相对路径里含 `..` |
| `E_HASH_RECORD_TARGET` | `<file>.sha256` 指向一个既非固定冻结名、也不带 `vNNN` 的名字 |

---

## 2. 哈希记录格式（逐字段按 gate_G2）

`gate_G2.md`「冻结定义」原文：

```text
04_approved/<song_id>/ground_truth.musicxml
04_approved/<song_id>/ground_truth.musicxml.sha256   # <sha256>  <file>  <utc_time>  <approver>
```

即**一行四段**，两个空格分隔：

```text
b9aae72f4ba52452c177f5528bbce127dedb90d8165d5ec237eeca7f2ad47c80  ground_truth.musicxml  2026-10-13T00:00:00Z  mock-approver
```

三个刻意的选择：

- `<file>` 是**文件名，不是路径** —— `04_approved/README.md` 与阶段1 e2e
  （`orchestrator.py` 里写这段的地方）都是这个口径；记录与被冻结件同目录，
  搬迁时不会分离。
- `<utc_time>` 是**秒精度 + 字面 `Z`**，不是 `datetime.isoformat()` 的 `+00:00`。
  冻结记录是**判据**，要能让人拿眼睛逐字符核对。
- **不提供第五段**（`original_name` / `bytes` / `engine` 之类）。下游按四段解析，
  多一段就错位；想多记的请另开 sidecar。

解析是**严格**的：列数必须 4、sha256 必须 64 位小写十六进制、时间必须是
`YYYY-MM-DDTHH:MM:SSZ`。宽松解析在这里没有好处 —— 一个被截断的记录如果能
「勉强读出来」，它保护的就不是文件内容，而是一个误会。

---

## 3. 冻结语义

| 动作 | 行为 |
|---|---|
| `freeze_file(path, approver)` | 目标必须已存在且文件名合规 → 写 `<file>.sha256` + 置只读位。**已有记录 → `AlreadyFrozenError`**（附 `next_path`），不提供 `force=True` |
| `verify_frozen(path)` | 返回 `VerifyResult`，不抛异常。`status` ∈ `ok` / `missing_file` / `missing_record` / `hash_mismatch` / `bad_record` |
| `derive_next_path(path)` | 固定名从 `v002` 起（`ground_truth.musicxml` 就是 v001，再叫 v001 会出现两个 v001） |
| `check_no_overwrite(orig, cand)` | 写新版本前的显式守卫：目标若是已冻结件 → 拒绝 |

**为什么区分四种失败**：记录丢了要去找记录、文件没了要去找文件、哈希不符是
**被篡改**、记录非法说明流程漏了。混成一个 `False`，报告里就只剩「失败」两个字。

**为什么 `verify_frozen` 不抛异常**：「校验不通过」是预期结果之一，
门禁脚本要把它写进报告并继续扫剩下的文件，而不是在第一个坏文件上崩掉。
`verify_all(paths)` 同理，不短路。

---

## 4. 诚实的边界（这一点必须说清）

### 4.1 「只读不可变」在 Git / Windows 上做不到强保证

#13 说 G2 PASS 后把 `ground_truth.musicxml` 置为**只读不可变**。必须澄清：
**这是过程约束 + 检测机制，不是安全边界。**

| 手段 | 挡得住 | 挡不住 |
|---|---|---|
| `chmod 0444`（本工具默认做） | 手滑重命名/覆写、编辑器直接保存 | `os.chmod` 改回写位再写（见 `test_freeze_chmod_is_not_a_security_boundary`，这条测试**故意演示绕过**） |
| 哈希记录 `<file>.sha256` | 无 —— 它是**检测**，不是**阻止** | 什么都挡不住，但改了**一定看得出来** |
| Git 历史 | 已 commit 的内容 | 未提交的工作区改动；`--no-verify`；force push 后的本地历史 |
| `git` pre-commit hook（如 `core.hooksPath` 指向本仓 hooks） | 一次疏忽的提交 | 没配 hooksPath 的克隆（**默认不生效**）；`-n` |

所以本目录的定位是：**让「改了」这件事不可能悄悄发生**，而不是让「改不了」。
真实加固（文件系统级只读、`02_source/` + `04_approved/` 只读位巡检）归 #27。

### 4.2 Windows 上的差异

- `chmod 0444` 在 NTFS 上只映射到「只读」属性，语义比 POSIX 弱；
- `freeze.is_read_only()` 在 Windows 上**不可靠**（`stat.S_IWUSR` 的映射随平台/挂载方式变化），
  别拿它当判据，拿 `verify_frozen().ok`；
- 正因如此，本目录的字符规则（ASCII / 全小写 / 无空格 / 无保留名 / 段长 ≤ 40）
  是**硬拦**而不是「建议」：大小写不敏感这件事没法在事后补救，
  只能在命名时禁止。

### 4.3 工具能力边界

- **看不见 song_id 语义**：G4-10 的占位符是 `<song_id>`，而 song_id 由 `glossary.md`
  定义为 `songNN`、#122 的 e2e 实际用 `flow-demo-001`。工具只看文件名，
  没有足够信息区分「合法 song_id `flow-demo-001`」与「少写序号的 `song`」。
  因此 `song_mix_v001.wav` 目前**是**合规的 —— 这是已知边界，
  `tests` 里有一条用例把它写明（`test_master_rule_cannot_reject_short_song_id_like_song`），
  不写一条假装能拦的测试。要收紧就得让调用方把 song_id 传进来。
- **不校验内容**：`ground_truth.musicxml` 是不是合法 MusicXML、`approved_vocal_pilot.wav`
  能不能解码 —— 那些是 G2/G3 的事，不是命名与冻结的事。
- **不发明规则**：`.rpp` `.ardour` `.m4a` 等只扫通用字符规则，不套形态规则。
  没把握的规则写死，只会让人学会加 `--force`，规则就废了。

---

## 5. 交叉引用

- #13 版本命名与批准本冻结（本目录服务的对象）
- `gate_G2.md` 冻结定义 / G2-9、`gate_G3.md` G3-10、`gate_G4.md` G4-10 / G4-12
- `00_docs/glossary.md`（song_id / approved）、`00_docs/deliverables.md`（命名实例）
- `music_rebuild_project/README.md`「文件命名规范」「Windows 路径风险规避」
- #27 `02_source/` + `04_approved/` 只读加固（文件系统级；本目录只做检测）
- #37 冻结 + 哈希、#60 文件验证脚本、#14 风险登记（R-03 数据漂移）
