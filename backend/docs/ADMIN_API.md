# 后台控制 API 文档（多引擎路由 + 分层计费）

> 本文件由真实 FastAPI 代码（`app/routers/admin.py`、`app/schemas.py`、`app/orchestrator/selection.py`）导出生成，
> 与 `backend/openapi.json`（由 `app.main:app.openapi()` 自动生成）同源，非手搓。
> 机器可读的 OpenAPI 3.0 规范见 `backend/openapi.json`。

## 0. 总览

后台控制让你 **不重启、不改代码** 即可实时调整三件事：

| 能力 | 数据表 | 生效机制 |
| --- | --- | --- |
| 引擎开关 / 接入（Suno / Mock / ACE-Step 私有 / 腾讯云 MPS / 火山 …） | `engine_configs` | DB 驱动 + 30s TTL 缓存；每次写操作调用 `invalidate_config_cache()` 立即生效 |
| 内容分类 → 引擎 路由（如 `christian_worship → ace_step_private` 规避国内通用引擎对宗教词的误杀） | `routing_rules` | 同上 |
| 套餐分层计费（free / standard / sacred / enterprise） | `pricing_tiers` | 同上 |

**关键行为（拒绝幻觉，与代码一致）：**
- 路由命中的引擎若被后台**禁用**，自动回退到「默认启用引擎」（`is_default=True` 且 `enabled=True`），再回退 `settings.music_provider`。因此默认种子下 `christian_worship` 会回退到 `suno`（因为 `ace_step_private` 默认禁用，未接入 GPU），这是**正确行为**，不是 bug。
- `api_key` 明文存储（生产应接 KMS/环境变量加密，本层不做伪加密）；GET 接口仅返回 `api_key_set: bool`（是否配置），**绝不返回明文**。
- 所有解析逻辑在无配置时安全回退到 `settings.music_provider` / `suno`，不会导致生成中断。

## 1. 鉴权

所有 `/api/admin/*` 端点都需要管理密钥，通过请求头传递：

```
X-Admin-Token: <ADMIN_TOKEN>
```

- 后端配置项 `ADMIN_TOKEN`（`app/config.py` → `settings.admin_token`）。
- 使用 `hmac.compare_digest` 恒定时间比较，防时序侧信道。
- `admin_token` 未配置时，所有 admin 端点返回 **503**（`管理后台未启用（admin_token 未配置）`），而非 500。
- 令牌错误/缺失时返回 **401**（`无效的管理密钥`）。
- 前端 token 存于 `localStorage["admin_token"]`，由 `lib/api.ts` 的 `requestAdmin()` 自动注入。

### curl 通用模板

```bash
BASE=http://localhost:8000
ADMIN_TOKEN=your-admin-token

# 列出全部引擎
curl -H "X-Admin-Token: $ADMIN_TOKEN" $BASE/api/admin/engines
```

## 2. 引擎管理（`engine_configs`）

### 2.1 列出引擎
`GET /api/admin/engines`

响应：`EngineConfigOut[]`（按 priority 降序）。

```json
[
  {
    "provider": "suno",
    "display_name": "Suno (open.suno.cn)",
    "enabled": true,
    "is_default": true,
    "api_base": null,
    "api_key_set": true,
    "cost_per_song_cny": 0,
    "priority": 100,
    "notes": "商业 API；宗教词非确定性误拦…"
  }
]
```

### 2.2 获取单个引擎
`GET /api/admin/engines/{provider}` → 404 if not exist。

### 2.3 创建引擎
`POST /api/admin/engines`（201）

请求体 `EngineConfigCreate`：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `provider` | str | 是 | 引擎名，名空间：`suno`/`mock`/`musicgen`/`ace_step_private`/`tencent_mps`/`volcano` |
| `display_name` | str? | 否 | 展示名 |
| `enabled` | bool | 否 | 默认 true |
| `is_default` | bool | 否 | 默认 false；若设为 true 且启用，则作为回退默认引擎 |
| `api_base` | str? | 否 | API 地址 |
| `api_key` | str? | 否 | 密钥（仅写，GET 不回明文） |
| `cost_per_song_cny` | float? | 否 | 估算单价（成本报表/计费回退用） |
| `priority` | int? | 否 | 默认 0；越高越优先成为默认 |
| `notes` | str? | 否 | 备注 |

已存在则 **409**（`引擎已存在`）。

```bash
curl -X POST $BASE/api/admin/engines \
  -H "X-Admin-Token: $ADMIN_TOKEN" -H "Content-Type: application/json" \
  -d '{"provider":"ace_step_private","display_name":"ACE-Step 私有引擎","enabled":true,"is_default":false,"priority":200,"cost_per_song_cny":0.003}'
```

### 2.4 更新引擎
`PUT /api/admin/engines/{provider}` → 404 if not exist。

请求体 `EngineConfigUpdate`（全部可选，**仅传需要改的字段**，`exclude_unset` 语义）：

- `api_key`：传空字符串 `""` 或不传 = **不改**；传非空 = 覆盖。
- 其余字段传 `null` 表示不更新（因 Pydantic `exclude_unset` 只在请求体省略该键时跳过；传 `null` 会写入 null，请按需传值或省略键）。

```bash
# 启用私有引擎（让 christian_worship 真正路由到它，而非回退 suno）
curl -X PUT $BASE/api/admin/engines/ace_step_private \
  -H "X-Admin-Token: $ADMIN_TOKEN" -H "Content-Type: application/json" \
  -d '{"enabled":true}'
```

### 2.5 删除引擎
`DELETE /api/admin/engines/{provider}` → `{"ok":true}`；404 if not exist。
> 注意：删除默认引擎前请先设置其它引擎为 `is_default=true`，否则解析会回退到 `settings.music_provider`。

## 3. 路由规则（`routing_rules`，分类 → 引擎）

### 3.1 列出规则
`GET /api/admin/routing-rules` → `RoutingRuleOut[]`（按 priority 降序）。

### 3.2 创建规则
`POST /api/admin/routing-rules`（201）

`RoutingRuleCreate`：`category`(str, 必填)、`provider`(str, 必填)、`priority`(int, 默认 0)、`enabled`(bool, 默认 true)。

- 同一 `category` 可有多条规则；运行时取 **priority 最高且 enabled** 的那条。
- `category` 推荐值：`general` / `christian_worship` / `instrumental`（与 `Song.content_category` 对齐）。
- `provider` 必须指向已存在的 `engine_configs.provider`。

```bash
curl -X POST $BASE/api/admin/routing-rules \
  -H "X-Admin-Token: $ADMIN_TOKEN" -H "Content-Type: application/json" \
  -d '{"category":"christian_worship","provider":"ace_step_private","priority":200,"enabled":true}'
```

### 3.3 更新 / 删除规则
- `PUT /api/admin/routing-rules/{rule_id}` → `RoutingRuleUpdate`（全部可选）。
- `DELETE /api/admin/routing-rules/{rule_id}` → `{"ok":true}`。均 404 if not exist。

## 4. 套餐计费（`pricing_tiers`）

### 4.1 列出套餐
`GET /api/admin/pricing-tiers` → `PricingTierOut[]`（按 tier_key 排序）。

### 4.2 创建套餐
`POST /api/admin/pricing-tiers`（201）

`PricingTierCreate`：`tier_key`(str, 必填且唯一)、`tier_name`(str?)、`engine_provider`(str? 指向具体引擎做差异化收费)、`price_cny`(float?)、`credits_per_song`(int? 默认 1)、`description`(str?)、`enabled`(bool 默认 true)。

已存在则 **409**。

```bash
curl -X POST $BASE/api/admin/pricing-tiers \
  -H "X-Admin-Token: $ADMIN_TOKEN" -H "Content-Type: application/json" \
  -d '{"tier_key":"sacred","tier_name":"圣乐版","engine_provider":"ace_step_private","price_cny":2.0,"credits_per_song":1,"description":"合规通道差异化高价"}'
```

### 4.3 更新 / 删除套餐
- `PUT /api/admin/pricing-tiers/{tier_key}` → `PricingTierUpdate`（全部可选）。
- `DELETE /api/admin/pricing-tiers/{tier_key}` → `{"ok":true}`。均 404 if not exist。

计费解析（`resolve_cost_cny`）：优先取 `enabled` 套餐的 `price_cny` → 回退命中引擎的 `cost_per_song_cny` → 回退 0.0。

## 5. 仪表盘 + 成本报表 + 恢复默认

### 5.1 总览仪表盘
`GET /api/admin/config/dashboard` → `ConfigDashboardOut`：

```json
{
  "engines": [ "…EngineConfigOut[]" ],
  "routing_rules": [ "…RoutingRuleOut[]" ],
  "pricing_tiers": [ "…PricingTierOut[]" ],
  "cost_by_engine": { "suno": 0.0, "ace_step_private": 1.2 },
  "cost_by_category": { "general": 0.0, "christian_worship": 1.2 },
  "pending_moderation": 3
}
```

- `cost_by_engine` / `cost_by_category`：按 `Song.cost_cny` 聚合（已生成歌曲的实际成本归因）。
- `pending_moderation`：待审歌曲数。

### 5.2 恢复/补全默认配置
`POST /api/admin/config/seed` → 返回 `ConfigDashboardOut`（同 5.1）。

幂等：仅当对应表为空 / 该行不存在时插入默认种子（`selection.DEFAULT_ENGINES / DEFAULT_ROUTING / DEFAULT_TIERS`），**已存在的行不覆盖**，保留你后台已改的值。

```bash
curl -X POST $BASE/api/admin/config/seed -H "X-Admin-Token: $ADMIN_TOKEN"
```

## 6. 默认种子速查（首次启动注入）

引擎（provider / enabled / is_default / cost_per_song_cny）：
- `suno` 启用 / 默认 / 0
- `mock` 启用 / 否 / 0（CI/本地）
- `musicgen` 禁用 / 否 / 0（CC-BY-NC 不可商用，已排除）
- `ace_step_private` 禁用 / 否 / 0.003（Apache-2.0 可商用，本地 GPU 零误杀；启用后 `christian_worship` 走此）
- `tencent_mps` 禁用 / 否 / 0.22（云 API，需 AK，官方样例估算）
- `volcano` 禁用 / 否 / 0.36（云 API，需 AK，公开价估算）

路由（category → provider）：`general→suno`、`christian_worship→ace_step_private`、`instrumental→suno`。

套餐：`free`(0) / `standard`(0.36) / `sacred`(2.0, 指向 ace_step_private) / `enterprise`(0)。

> ⚠️ 成本数字为「官方样例/公开价估算」占位，真实单价以你接入时各家最新报价为准（拒绝幻觉：不臆造报价）。

## 7. 典型运维流程

**场景：让基督教圣乐真正走合规私有引擎、规避国内通用引擎误杀**
1. 接入 ACE-Step（GPU/部署），拿到 `api_base`/`api_key`。
2. `PUT /api/admin/engines/ace_step_private` 设 `enabled=true`、`api_base`、`api_key`。
3. 确认 `routing-rules` 已有 `christian_worship → ace_step_private` 且 `enabled=true`（默认种子已带）。
4. 调用 `GET /api/admin/config/dashboard` 验证 `cost_by_category.christian_worship` 开始归因到私有引擎。
5. 之后用户以 `content_category=christian_worship` 生成即可自动走合规通道——无需改代码、无需重启。

## 8. 错误码

| 状态码 | 含义 |
| --- | --- |
| 401 | 管理密钥缺失或错误 |
| 403 | （本系统未使用，鉴权为 401） |
| 404 | 资源不存在（引擎/规则/套餐） |
| 409 | 创建时主键冲突（引擎已存在 / 套餐已存在） |
| 422 | 请求体校验失败（Pydantic） |
| 503 | `admin_token` 未配置，后台功能未启用 |
