# CDN / 对象存储：主播放链路接入 + 归档保留 Runbook（GAP-019）

> 面向运维：**桶怎么配、老歌怎么归档、历史曲目怎么回填、出事怎么回滚**。
> 只写实测确认过的事实；未验证项明确标注「未验证」。

---

## 0. 一句话总览

```
上游 audio_url（30 天过期）
        │  回源（SSRF 守卫 + 指数退避 + 同键幂等）
        ▼
自有桶对象  songs/{user_id}/{song_id}.mp3
        │  storage_public_base（CDN 加速域名）
        ▼
audio_cdn_url  ──►  SongOut.audio_url / FLOW 歌单 / 下载 / 试听片段
```

- **未配 `storage_*` → 整条链路优雅降级**：所有出口直接用上游 `audio_url`，
  零报错、零 500。这是 prod **当前**的状态（`.env.music` 里 0 条 storage 行），
  也是验收硬要求。配好变量即自动切换到 CDN，**无需改代码、无需重启逻辑分支**。
- **`audio_cdn_url` 为 NULL 绝不 500**：取不到 CDN 链接就回上游，取不到上游才报错。

---

## 1. 配置项（`.env`）

| 变量 | 必需 | 说明 |
|---|---|---|
| `STORAGE_ENDPOINT` | ✅ | S3 兼容端点（COS/R2/S3/MinIO）。如 `https://cos.ap-hongkong.myqcloud.com` |
| `STORAGE_BUCKET` | ✅ | 桶名。**必须**是公共读或挂 CDN 回源，否则播放 403 |
| `STORAGE_ACCESS_KEY` | ✅ | 只给该桶的读写权限，**绝不提交进 git**（仓库是 Public） |
| `STORAGE_SECRET_KEY` | ✅ | 同上 |
| `STORAGE_REGION` | ➖ | 默认 `ap-hongkong` |
| `STORAGE_PUBLIC_BASE` | 🔺 强烈建议 | CDN 加速域名，如 `https://cdn.example.com`。不配则退化为 `{endpoint}/{bucket}`（能用但没有边缘加速，且把源站地址暴露给客户端） |
| `STORAGE_CLASS` | ➖ | 新对象默认存储类别。留空 = 桶默认。**不要填 GLACIER 类** |
| `STORAGE_ARCHIVE_AFTER_DAYS` | ➖ | 归档阈值（天），默认 `90`；`<=0` = 关闭归档 |
| `STORAGE_CLASS_ARCHIVE` | ➖ | 归档目标类别，默认 `STANDARD_IA`（低频） |
| `ENABLE_CDN_MIRROR_ON_GENERATE` | ➖ | 默认 `true`：新曲生成完即回源，首个响应就是 CDN 链接 |

`is_configured()` 的判据是 `endpoint + bucket + access_key + secret_key` 四项全非空。
只要缺一项，全链路按「未配存储」处理。

**验收自测（配置后必做）**：

```bash
# 1) 生成一首新曲 → 看详情接口的 audio_url 是不是 CDN 域名（而不是上游域名）
curl -s -H "Authorization: Bearer $TOKEN" https://music.example.com/api/songs/$SID | jq .audio_url

# 2) 对象确实落桶了
aws s3 ls s3://$BUCKET/songs/ --endpoint-url $STORAGE_ENDPOINT | tail
```

---

## 2. 桶策略：版本化 + 生命周期 + 保留（建议基线）

### 2.1 开启版本化（防误删/防覆盖）

```bash
aws s3api put-bucket-versioning --bucket $BUCKET \
  --versioning-configuration Status=Enabled --endpoint-url $STORAGE_ENDPOINT
```

为什么必须开：回源是**同键覆盖**（`songs/{uid}/{sid}.mp3`），若上游后来返回了损坏或
错误的音频，覆盖会把好的那份直接冲掉且不可恢复。版本化让「覆盖」变成可回滚操作。

### 2.2 生命周期规则（参考配置，按实际桶语法落地）

| 规则 | 对象范围 | 动作 | 理由 |
|---|---|---|---|
| ① 清理未完成分片 | 全桶 | `AbortIncompleteMultipartUpload` 7 天 | 中断上传的分片会一直计费 |
| ② 非当前版本降冷 | 全桶 | NoncurrentVersion → `STANDARD_IA` 30 天 | 老版本几乎不会被读 |
| ③ 非当前版本过期 | 全桶 | NoncurrentVersion 过期删除 **180 天** | 保留可回滚窗口，之后清掉省费 |
| ④ 孤儿对象 | `preview/`,（可选）`tmp/` | 过期删除 30 天 | 试听片段可随时重生成，不必永久保存 |
| ⑤ **谨慎**：音频对象深冷归档 | `songs/` | 转 GLACIER 类 | ⚠️ **默认不做**，见 §2.3 |

### 2.3 深冷归档（GLACIER）为什么默认不做

`GLACIER` / `DEEP_ARCHIVE` 不支持直接 `GET`：点播会返回错误或需要**数分钟到数小时**解冻。
本产品的核心动作就是「点开就播」，把在架曲目推进深冷档 = 把功能变成「点了播不了」。

- `scripts/archive_cdn.py --class GLACIER` **会直接拒绝并退出码 2**（有测试钉死）。
- 若真有「合规留存、永不点播」的资产（例如下架作品存档），
  走**单独前缀**（如 `archive/`）+ 生命周期规则，并接受「回热流程后才能播放」；
  回热 = `COPY` 回 `STANDARD`，或按需 `RestoreObject`。

**曲目级归档用 `STANDARD_IA`（低频）**：省 40~60% 存储费，首次读取延迟略升，
客户端 CDN 缓存命中时无感。

---

## 3. 日常作业

### 3.1 历史曲目回填（把 `audio_cdn_url` 为 NULL 的曲目搬上 CDN）

```bash
cd backend
python scripts/backfill_cdn.py --dry-run            # 先看会处理哪些，不打网络
python scripts/backfill_cdn.py                      # 默认 20 曲/分钟
python scripts/backfill_cdn.py --limit 200 --rate 60
python scripts/backfill_cdn.py --since-id 4200      # 断点续跑
python scripts/backfill_cdn.py --ids 12,34,56       # 只重试失败清单
```

- **幂等**：目标行由 `audio_cdn_url IS NULL/''` 动态查询；对象键是
  `songs/{user_id}/{song_id}.mp3`（`app.storage.cdn.song_key`），重复跑是同键覆盖。
- **可续跑**：成功一行就少一行，DB 本身即断点；进程被杀后重跑自动继续。
- **限速**：`--rate`（每分钟曲数）/ `--sleep`（每曲固定间隔）。
- **未配 storage → 退出码 2 并提示**，不会空跑一遍装作干完了。
- 退出码：`0`=无失败，`1`=有失败（失败 id 会打印，可直接重试），`2`=环境不满足。

### 3.2 归档（老歌转低频档）

```bash
python scripts/archive_cdn.py --dry-run             # 默认阈值 STORAGE_ARCHIVE_AFTER_DAYS=90
python scripts/archive_cdn.py --days 180 --limit 500
python scripts/archive_cdn.py --unarchive --ids 12,34  # 回热：搬回 STANDARD
```

- 只处理**已回源**（`audio_cdn_url` 非空）且 `archived_at IS NULL` 的曲目
  → 幂等，重跑不会重复迁移。
- 成功后写 `songs.archived_at` + `songs.storage_class`，可查询、可对账
  （`SELECT count(*) FROM songs WHERE archived_at IS NOT NULL`）。
- 存储类别搬运就是 S3 的原地 `copy_object` —— 有版本化时会产生一个新版本，属预期。

### 3.3 巡检

```sql
-- 还没上 CDN 的曲目（回填作业的待办队列）
SELECT count(*) FROM songs WHERE (audio_cdn_url IS NULL OR audio_cdn_url = '')
  AND audio_url IS NOT NULL AND audio_url <> '';

-- 已归档分布
SELECT storage_class, count(*) FROM songs WHERE archived_at IS NOT NULL GROUP BY 1;
```

---

## 4. 故障处置

| 症状 | 判断 | 处置 |
|---|---|---|
| 播放返回上游域名，且随天数变差 | `storage_*` 未配 / 桶不可写 | 配齐 4 个必需变量 → 下次请求自动回填；存量跑回填脚本 |
| 播放 403/404（域名是 CDN） | 桶策略变成私有 / CDN 回源配置丢了 | 恢复公共读或 CDN 回源；已落库的 URL 不用改 |
| 新歌一直没 CDN 链接 | 回源被 SSRF 守卫拒绝 / 上游链接过期 | 查日志 `CDN mirror refused by SSRF guard` / `mirror gave up`；过期链接**无法**回源，只能重新生成 |
| 老歌首播明显变慢 | 已在低频档 | 属预期（首次）；高频播放的曲目用 `--unarchive` 回热 |
| 需要立即停用 CDN | —— | 清空 `STORAGE_*` 变量并重启：`is_configured()=False` → 全链路退回上游，**不改代码** |

> ⚠️ 回滚到上游 = 回到「链接 30 天会过期」的状态，只是止血，不是修复。

---

## 5. 实现落点（改代码前先读）

| 位置 | 职责 |
|---|---|
| `app/storage/s3.py` | 与桶说话：`upload_bytes` / `upload_file` / `set_storage_class` / `object_exists` / `download_to_temp`（SSRF 逐跳校验） |
| `app/storage/cdn.py` | 对象键、媒体类型、存储类别读配置（**唯一约定**） |
| `app/storage/mirror.py` | 回源本体：SSRF 守卫 + 指数退避 + 上限截断 + 幂等落库，**任何失败返回 None** |
| `app/storage/transport.py` | 出口选择：`full_audio_url(song)` = CDN 优先、上游兜底 |
| `app/routers/songs.py` | `_to_out`（播放链接）、下载、试听、`schedule_cdn_backfill`（write-behind）、`_finalize_song`（生成即回源） |
| `app/routers/flow.py` | FLOW 歌单同步，`_ensure_cdn_url` + 降级不中断歌单 |
| `scripts/backfill_cdn.py` / `scripts/archive_cdn.py` | 存量回填 / 归档回热 |

**改动约束**：新增任何「取音频去做别的事」的路径（分轨、后期处理、上传到第三方）
必须用 `full_audio_url(song, force_upstream=True)` —— 那些第三方只认上游存储，
把 CDN 域名喂过去会静默失败。

---

## 6. 相关迁移

`i1a2b3c4d5e6_gap019_cdn_archive.py`（`down_revision = h1a2b3c4d5e6`）新增：

- `songs.archived_at`（timestamptz，NULL = 未归档）
- `songs.storage_class`（varchar(32)，"" = 桶默认）

```bash
cd backend
alembic heads          # 必须只有一个 head
alembic upgrade head
alembic downgrade -1   # 回滚（删两列，无数据依赖，安全）
```
