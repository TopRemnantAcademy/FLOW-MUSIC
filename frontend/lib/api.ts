// 前端 API 客户端：统一封装后端请求 + token 注入。
// [APK] APK 빌드(NEXT_PUBLIC_APK_BUILD=1)에서는 loopback fallback 을 번들에 남기지 않는다.
// 정적 익스포트는 빌드 시점에 API 주소가 굽혀지므로 fallback 이 묻으면 실기기에서
// 127.0.0.1(=기기 자신) 로 향해 100% 접속 실패한다. build-apk.mjs 가 NEXT_PUBLIC_API_BASE 를
// 강제하므로 도메인이 들어가지만, 잔여 localhost 리터럴 자체가 번들에 남는 것도
// preflight/빌드 자체검증이 막는다(미설정 시 빌드 단계에서 die). Web 은 APK_BUILD 가
// 미설정이므로 기존처럼 localhost:8000 개발 fallback 을 유지한다.
export const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE ||
  (process.env.NEXT_PUBLIC_APK_BUILD === "1" ? "" : "http://localhost:8000");

// 带 HTTP 状态码的错误，便于上层按状态码区分处理（如 401 引导重新登录）
export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

export type SongView = {
  id: number;
  title: string;
  status: string;
  audio_url?: string | null;
  cover_url?: string | null;
  lyric?: string | null;
  // 逐字时间轴：后端 lyrics_sync 列 = Mureka lyrics_sections 的 JSON 字符串（仅详情接口返回）。
  // 前端需 JSON.parse 后再交给 syncedLyrics.flattenSync 解析；列表/广场接口不携带此字段。
  lyrics_sync?: string | null;
  task_type?: string;
  is_public?: boolean;
  is_owner?: boolean;
  play_count?: number;
  // Freemium 试听门控：未订阅时后端只下发「前 N 秒片段」链接，完整音频 URL 不下发。
  preview_audio_url?: string | null;
  preview_seconds?: number | null;
  can_play_full?: boolean;   // 订阅后可播放完整版
  can_download?: boolean;    // 订阅后可下载完整音频
  // ── 广场互动（2026-09-10）：无文本互动，不做留言/评论 ──
  like_count?: number;
  favorite_count?: number;
  rating_avg?: number | null;   // 平均分 1.0–5.0；无人评分为 null
  rating_count?: number;
  liked_by_me?: boolean;
  favorited_by_me?: boolean;
  my_rating?: number | null;
  author_name?: string | null;  // 发布者标识（后端已脱敏，不出完整邮箱）
  // 发布后置：完成 + 审核通过才允许公开
  moderation_status?: string;
  moderation_note?: string | null;
};

export type AdminSongView = SongView & {
  user_email?: string | null;
  moderation_status?: string;
  moderation_note?: string | null;
  style?: string | null;
  prompt?: string | null;
  model_version?: string | null;
  duration?: number;
  created_at?: string;
  content_category?: string | null;
  engine_name?: string | null;
  cost_cny?: number | null;
  price_cny?: number | null;
  margin_cny?: number | null;
  tier_key?: string | null;
};

export type AdminStats = {
  total: number;
  by_status: Record<string, number>;
  by_moderation: Record<string, number>;
  total_cost_cny?: number;
  total_revenue_cny?: number;
  total_margin_cny?: number;
};

// —— 跨域账号桥接：FLOW 订阅者 ↔ 音乐用户（user_links，零 PII）——
// 后端 GET /api/admin/user-links 仅返回关联与订阅状态；email 绝不以原文出现。
export type UserLinkSubscription = {
  tier_key: string;
  provider: string;
  status: string;
  current_period_end: string | null;
};

export type UserLinkRow = {
  flow_subscriber_id: string;
  music_user_id: number;
  music_email_present: boolean;
  music_tier_key: string | null;
  subscription: UserLinkSubscription | null;
  linked_at: string | null;
};

// —— 用户侧用量/账单（GAP-005）：本人歌曲范围内的单位经济汇总 ——
export type MyBillingSong = {
  id: number;
  title: string;
  status: string;
  cost_cny: number;
  price_cny: number;
  margin_cny: number;
  tier_key: string | null;
  created_at: string | null;
};

export type MyBilling = {
  tier_key: string | null;
  total_songs: number;
  total_cost_cny: number;
  total_revenue_cny: number;
  total_margin_cny: number;
  daily_quota_used: number;
  daily_quota_limit: number;
  recent: MyBillingSong[];
};

// —— 订阅/套餐：真实支付基座（GAP-004）——
// ⚠ 旧 mock 升档接口 POST /api/auth/subscribe 已从本客户端移除：
//   config.py:176 billing_mode 默认 "live" → 该端点恒 410；enable_mock_subscribe 默认 False
//   （config.py:180，避免绕过支付直接升档）。账户页升级一律走 api.checkout。

// 真实支付流程（/api/billing/*）数据结构，与 backend/app/routers/billing.py 对齐。
export type CheckoutResp = {
  order_id: number;
  checkout_url: string; // manual 模式为平台内 /api/billing/orders/{id}/confirm；外部供应商为收银台 URL
  amount_cny: number;   // 结算等价人民币
  amount: number;       // 实际计费金额(按 currency)
  currency: string;     // CNY | USD | KRW
  tier_key: string;
  provider: string;
  // kakao(모바일 딥링크/앱 스킴) / naver(JS SDK 파라미터) / wechat(Native QR) 등
  // checkout_url 하나로 표현 안 되는 PG 별 handoff 정보. 없으면 null.
  provider_params?: Record<string, unknown> | null;
};

// GET /api/billing/providers 返回的单条(前端支付方式选择器 source of truth).
export type BillingProvider = {
  provider: string;
  flow: "redirect" | "qr" | "sdk" | "internal";
  settle: "webhook" | "approve" | "confirm";
  currency: string | null; // 该 PG 只受理的结算币种(None = 多币种)
  approve_endpoint: string | null; // approve 型才有
  is_default: boolean;
};

export type ProvidersResp = {
  providers: BillingProvider[];
  default: string;
  mode: string;
};

export type BillingOrder = {
  order_id: number;
  tier_key: string;
  amount_cny: number;
  amount: number;
  currency: string;
  status: string;
  provider: string;
  created_at: string;
};

export type BillingSubscription = {
  active: boolean;
  tier_key: string | null;
  provider?: string | null;
  status?: string | null;
  current_period_end?: string | null;
  cancel_at_period_end?: boolean | null;
};

// 已启用套餐(真实价格 source of truth = 后端 pricing_tiers)，与 /api/billing/plans 对齐。
export type BillingPlan = {
  tier_key: string;
  tier_name: string | null;
  price_cny: number;
  price_usd: number;
  price_krw: number;
  credits_per_song: number;
  description: string | null;
  enabled: boolean;
};

// —— 后台控制（多引擎路由 + 分层计费）数据结构，与后端 schemas.py 对齐 ——
export type EngineConfig = {
  provider: string;
  display_name?: string | null;
  enabled: boolean;
  is_default: boolean;
  api_base?: string | null;
  api_key_set: boolean; // 仅表示是否配置，不返回明文
  cost_per_song_cny: number;
  priority: number;
  notes?: string | null;
};

export type RoutingRule = {
  id: number;
  category: string;
  provider: string;
  priority: number;
  enabled: boolean;
};

export type PricingTier = {
  id: number;
  tier_key: string;
  tier_name?: string | null;
  engine_provider?: string | null;
  price_cny: number;
  credits_per_song: number;
  description?: string | null;
  enabled: boolean;
};

export type ConfigDashboard = {
  engines: EngineConfig[];
  routing_rules: RoutingRule[];
  pricing_tiers: PricingTier[];
  cost_by_engine: Record<string, number>;
  cost_by_category: Record<string, number>;
  revenue_by_tier: Record<string, number>;
  margin_by_tier: Record<string, number>;
  revenue_by_category: Record<string, number>;
  margin_by_category: Record<string, number>;
  total_cost_cny: number;
  total_revenue_cny: number;
  total_margin_cny: number;
  margin_rate: number;
  pending_moderation: number;
};

async function request<T = any>(path: string, options: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(options.headers as Record<string, string>),
  };
  // [SEC] 2026-09-13：认证令牌存于 HttpOnly cookie，浏览器随同源请求自动携带（credentials: include），
  // 不再从 localStorage 读取并注入 Authorization。仅 sync scope 令牌由调用方显式传入 Authorization。
  const explicitAuth = !!(options.headers as Record<string, string> | undefined)?.Authorization;

  const res = await fetch(`${API_BASE}${path}`, { ...options, headers, credentials: "include" });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    // 全局 401 处理：默认令牌过期/被吊销时广播登出事件，由 AuthProvider 回退未登录态。
    // （HttpOnly cookie 无法由 JS 清除，登出走 /api/auth/logout 由后端删除。）
    if (res.status === 401 && !explicitAuth) {
      try {
        window.dispatchEvent(new Event("auth:unauthorized"));
      } catch {
        /* SSR / 隐私模式忽略 */
      }
    }
    throw new ApiError((err as any).detail || "请求失败", res.status);
  }
  return res.json();
}

// —— FLOW APP 同步：scope 限定(sync:flow)令牌自签（🔴A 闭合） ——
// sync 端点（/api/v1/songs/{id}/sync-flow 等）拒绝全权限 JWT ——
// 后端 get_current_user_scoped fail-closed（auth.py），这是设计内行为。
// Web 端流程：先用全权限 JWT 自签短期 scope 令牌（POST /api/auth/flow-token，
// sub=自己，能力严格收敛），再持令牌调 sync。令牌只存内存（短寿命、
// 页面刷新即重签），绝不落 localStorage。
let flowScopeToken = "";

async function ensureFlowScope(force = false): Promise<string> {
  if (typeof window === "undefined") return "";
  if (flowScopeToken && !force) return flowScopeToken;
  const r = await request<{ access_token: string }>("/api/auth/flow-token", { method: "POST" });
  flowScopeToken = r.access_token;
  return flowScopeToken;
}

async function requestAdmin<T = any>(path: string, options: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(options.headers as Record<string, string>),
  };
  // [SEC] 2026-09-13：管理员密钥存于 flow_admin HttpOnly cookie，浏览器自动携带（credentials: include），
  // 不再从 localStorage 读取并注入 X-Admin-Token。显式传入 Authorization 的情况（无）保持兼容。
  const res = await fetch(`${API_BASE}${path}`, { ...options, headers, credentials: "include" });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new ApiError((err as any).detail || "管理请求失败", res.status);
  }
  return res.json();
}

export const api = {
  register: (email: string, password: string): Promise<{ access_token: string }> =>
    request("/api/auth/register", { method: "POST", body: JSON.stringify({ email, password }) }),
  login: (email: string, password: string): Promise<{ access_token: string }> =>
    request("/api/auth/login", { method: "POST", body: JSON.stringify({ email, password }) }),
  // ── Google OAuth ──
  googleConfig: () => request<{ enabled: boolean; client_id: string }>("/api/auth/google/config"),
  loginWithGoogle: (idToken: string): Promise<{ access_token: string }> =>
    request("/api/auth/google", { method: "POST", body: JSON.stringify({ id_token: idToken }) }),
  // [SEC] 2026-09-13：登出由后端清除 HttpOnly cookie（JS 无法自清）。
  logout: () => request("/api/auth/logout", { method: "POST" }),
  // [SEC] 管理员登录/登出：密钥经后端校验后写入/清除 flow_admin HttpOnly cookie。
  adminLogin: (key: string) =>
    requestAdmin("/api/auth/admin-login", { method: "POST", body: JSON.stringify({ key }) }),
  adminLogout: () => requestAdmin("/api/auth/admin-logout", { method: "POST" }),
  me: () => request<{ id: number; email: string }>("/api/auth/me"),
  meBilling: () => request<MyBilling>("/api/songs/me/billing"),
  // —— 真实支付基座（GAP-004 step-2）：账户页升级走此流程，不再直接切档 ——
  checkout: (tierKey: string, idempotencyKey?: string, currency?: string) =>
    request<CheckoutResp>("/api/billing/checkout", {
      method: "POST",
      body: JSON.stringify({
        tier_key: tierKey,
        ...(idempotencyKey ? { idempotency_key: idempotencyKey } : {}),
        ...(currency ? { currency } : {}),
      }),
    }),
  confirmOrder: (orderId: number) =>
    request<{ status: string; tier_key: string; order_id: number }>(
      `/api/billing/orders/${orderId}/confirm`,
      { method: "POST" }
    ),
  myOrders: () => request<BillingOrder[]>("/api/billing/orders"),
  mySubscription: () => request<BillingSubscription>("/api/billing/subscription"),
  // —— 套餐列表(各币种真实标价) + 自助取消/恢复 ——
  plans: () => request<BillingPlan[]>("/api/billing/plans"),
  cancelSubscription: () =>
    request("/api/billing/subscription/cancel", { method: "POST" }),
  reactivateSubscription: () =>
    request("/api/billing/subscription/reactivate", { method: "POST" }),
  // —— approve 型 PG(kakao / naver): 无 webhook, 需前端回跳后把 pg_token / paymentId 送回服务端 승인 ——
  // 注意: 这两个端点绝不以客户端自述升档, 而是服务端向各自供应商发起 approve 调用成功后才 승격.
  approveKakao: (orderId: number, pgToken: string) =>
    request<{ status: string; tier_key: string; order_id: number }>(
      "/api/billing/kakao/approve",
      { method: "POST", body: JSON.stringify({ order_id: orderId, pg_token: pgToken }) }
    ),
  approveNaver: (orderId: number, paymentId: string) =>
    request<{ status: string; tier_key: string; order_id: number }>(
      "/api/billing/naver/approve",
      { method: "POST", body: JSON.stringify({ order_id: orderId, payment_id: paymentId }) }
    ),
  // 可用支付方式清单(前端选择器 + 币种硬约束 source of truth, 与后端 require_currency 同一份 PROVIDER_CURRENCY).
  providers: () => request<ProvidersResp>("/api/billing/providers"),
  generate: (body: Record<string, unknown>) =>
    request("/api/songs/generate", { method: "POST", body: JSON.stringify(body) }),
  getSong: (id: number) => request<SongView>(`/api/songs/${id}`),
  listSongs: (limit?: number, offset?: number) => {
    const qs =
      limit != null || offset != null
        ? "?" +
          new URLSearchParams({
            ...(limit != null ? { limit: String(limit) } : {}),
            ...(offset != null ? { offset: String(offset) } : {}),
          }).toString()
        : "";
    return request<SongView[]>(`/api/songs${qs}`);
  },
  // [2026-09-10 连接性修复] flow 路由挂载在 /api/v1 前缀(routers/flow.py:32)，原 /api 路径 404
  // [2026-09-10 鉴权接通] sync 端点要求 scope 限定(sync:flow) JWT(auth.py fail-closed)：
  // 自签 → 调用；令牌过期(401)自动重签一次再试。
  syncFlow: async (id: number) => {
    let scope = await ensureFlowScope();
    try {
      return await request(`/api/v1/songs/${id}/sync-flow`, {
        method: "POST",
        headers: { Authorization: `Bearer ${scope}` },
      });
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) {
        scope = await ensureFlowScope(true);
        return await request(`/api/v1/songs/${id}/sync-flow`, {
          method: "POST",
          headers: { Authorization: `Bearer ${scope}` },
        });
      }
      throw e;
    }
  },
  downloadSong: async (id: number) => {
    // 订阅者下载完整音频。必须带认证，故不能用 <a href> 直连；
    // 取 blob 后用本地 URL 触发下载，文件名由后端 Content-Disposition 决定。
    // [SEC] 2026-09-13：认证由 flow_auth HttpOnly cookie 自动携带（credentials: include），
    // 不再手动注入 Authorization（localStorage 令牌已废弃）。
    const res = await fetch(`${API_BASE}/api/songs/${id}/download`, { credentials: "include" });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new ApiError((err as any).detail || "下载失败", res.status);
    }
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  },
  previewObjectUrl: async (id: number): Promise<string | null> => {
    // 未订阅者的试听片段。对象存储未配置时后端走 /api/songs/{id}/preview（需鉴权），
    // 而 <audio src> 无法携带 Authorization 头 → 取 blob 转成本地 object URL 播放。
    // 返回 null 表示试听片段暂不可用（fail-closed，前端提示而非回退完整音频）。
    // [SEC] 2026-09-13：认证由 HttpOnly cookie 自动携带。
    const res = await fetch(`${API_BASE}/api/songs/${id}/preview`, { credentials: "include" });
    if (!res.ok) return null;
    const blob = await res.blob();
    return URL.createObjectURL(blob);
  },
  extendSong: (id: number, body: Record<string, unknown> = {}) =>
    request(`/api/songs/${id}/extend`, { method: "POST", body: JSON.stringify(body) }),
  coverSong: (id: number, body: Record<string, unknown> = {}) =>
    request(`/api/songs/${id}/cover`, { method: "POST", body: JSON.stringify(body) }),
  remixSong: (id: number, body: Record<string, unknown> = {}) =>
    request(`/api/songs/${id}/remix`, { method: "POST", body: JSON.stringify(body) }),
  // ── 编辑工具 ──
  // ⚠ 已移除的后期处理包装（crop / speed / aligned-lyrics / whole-song / sound）不是漏做，
  //   而是刻意不接：它们依赖 engine capability，而 orchestrator/base.py:82-89 明确写着
  //   crop / speed / whole_song / aligned_lyrics / sound / upload **仅 SunoProvider 与
  //   MockProvider 提供**，线上引擎是 Mureka → songs.py._require_capability 恒 501。
  //   为一组在当前引擎下永不成功的按钮写 UI，等于把「坏掉的功能」当成功能交付。
  //   后端路由保留不动；当引擎链上出现 capable provider 时补回这几行即可：
  //     cropSong(id, body)      → POST /api/songs/{id}/crop
  //     speedSong(id, body)     → POST /api/songs/{id}/speed
  //     alignedLyrics(id, body) → POST /api/songs/{id}/aligned-lyrics
  //     wholeSong(id, body)     → POST /api/songs/{id}/whole-song
  //   ⚠ 后端 fail-closed 返回 501，届时前端须按「暂不可用」处理，不得降级成静默成功。
  // /upload：上传参考音频（audio_url）→ 返回 Mureka custom_id（schemas.py:75-80；
  //   供 /song/extend 的 upload_audio_id 用，mureka.py:439；不建 Song、不走审核）。
  uploadSong: (body: { audio_url: string }) =>
    request<{ custom_id: string }>("/api/songs/upload", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  // ── 阶段 2：Mureka 新增能力（后端 2026-09-12 接通，端点规格见 PLAN_H5_AND_MUREKA_GAPS.md B.5.3）──
  // 引擎未实现时后端统一 501，前端应把它当「该功能暂不可用」处理，不要降级成静默成功。
  instrumental: (body: { prompt: string; title?: string; model_version?: string }) =>
    request<{ song_id: number; status: string }>("/api/songs/instrumental", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  easyGenerate: (body: {
    prompt: string;
    styles?: string[];
    title?: string;
    model_version?: string;
    vocal_id?: string; // 克隆音色（mureka-o2 后端会自动剔除）
  }) =>
    request<{ song_id: number; status: string }>("/api/songs/easy-generate", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  // 改写区间 edit_end - edit_start 必须 ≥3000ms（官方硬约束），否则 400。
  regionEdit: (id: number, body: { lyrics: string; edit_start: number; edit_end: number }) =>
    request<{ song_id: number; status: string }>(`/api/songs/${id}/region-edit`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  // 同步接口：直接返回 zip_url，**会过期**（expires_at 为秒级 Unix 时间戳，
  // expires_at_iso 为后端换算好的 ISO 字符串，前端直接展示即可）。
  stem: (id: number, model: string = "audio-separation-2") =>
    request<{
      zip_url: string | null;
      midi_zip_url: string | null;
      expires_at: number | null;
      expires_at_iso?: string | null;
    }>(
      `/api/songs/${id}/stem?model=${encodeURIComponent(model)}`,
      { method: "POST" },
    ),
  // ⏸ 产品暂停（2026-09-13 决策）：后端 `enable_vocal_clone` 默认 False → 本接口返回 501。
  //    ⚠️ **代码保留、禁止删除**；重新上线只需后端置 ENABLE_VOCAL_CLONE=true（前端无需改动）。
  vocalClone: (file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    // [SEC] 2026-09-13：认证由 flow_auth HttpOnly cookie 自动携带（credentials: include）。
    return fetch(`${API_BASE}/api/songs/vocal-clone`, {
      method: "POST",
      credentials: "include",
      body: fd, // ⚠️ 不要手动设 Content-Type，浏览器需自带 multipart boundary
    }).then(async (res) => {
      if (!res.ok)
        throw new ApiError((await res.json().catch(() => ({}))).detail || "音色克隆失败", res.status);
      return (await res.json()) as { vocal_id: string };
    });
  },
  // ⚠ legacy：/api/songs/explore 已被 /api/plaza 取代（plaza 是其严格超集：尊重
  //   enable_moderation、hot/new/top 排序、带互动态+作者名；本端点固定播放量排序、
  //   _to_out(s) 无互动字段）。全站无消费者，保留仅为 API 兼容；新 UI 一律用 plaza()。
  explore: (limit?: number, offset?: number, q?: string) => {
    const params: Record<string, string> = {};
    if (limit != null) params.limit = String(limit);
    if (offset != null) params.offset = String(offset);
    if (q != null && q.trim() !== "") params.q = q.trim();
    const qs = Object.keys(params).length
      ? "?" + new URLSearchParams(params).toString()
      : "";
    return request<SongView[]>(`/api/songs/explore${qs}`);
  },
  play: (id: number) =>
    request<SongView>(`/api/songs/${id}/play`, { method: "POST" }),
  deleteSong: (id: number) =>
    request(`/api/songs/${id}`, { method: "DELETE" }),
  // ── 广场（原「发现」）：排行 + 无文本互动（赞/收藏/星标），不做留言 ──
  plaza: (opts: { sort?: string; limit?: number; offset?: number; q?: string } = {}) => {
    const params: Record<string, string> = {};
    if (opts.sort) params.sort = opts.sort;
    if (opts.limit != null) params.limit = String(opts.limit);
    if (opts.offset != null) params.offset = String(opts.offset);
    if (opts.q && opts.q.trim() !== "") params.q = opts.q.trim();
    const qs = Object.keys(params).length
      ? "?" + new URLSearchParams(params).toString()
      : "";
    return request<SongView[]>(`/api/plaza${qs}`);
  },
  likeSong: (id: number) =>
    request<SongView>(`/api/plaza/songs/${id}/like`, { method: "POST" }),
  favoriteSong: (id: number) =>
    request<SongView>(`/api/plaza/songs/${id}/favorite`, { method: "POST" }),
  rateSong: (id: number, score: number) =>
    request<SongView>(`/api/plaza/songs/${id}/rate`, {
      method: "POST",
      body: JSON.stringify({ score }),
    }),
  myFavorites: () => request<SongView[]>("/api/plaza/favorites"),
  // ── AI 一键优化助手 ──
  // assistLyrics = AI 生成歌词+标题（文案 LLM 优先，降级音乐引擎官方端点）；
  // assistStyle(mode) = 风格标签整理 | 主题扩写（文案 LLM 优先，降级本地规则）；
  // assistLyricsFormat = 本地格式对齐（不改写语义，刻意不走 AI）。
  assistLyrics: (prompt: string) =>
    request<{ title: string; lyrics: string; engine: string }>("/api/songs/assist/lyrics", {
      method: "POST",
      body: JSON.stringify({ prompt }),
    }),
  assistStyle: (
    text: string,
    opts: { forceGospel?: boolean; mode?: "style" | "prompt"; lyrics?: string } = {},
  ) =>
    request<{ optimized: string; notes: string[] }>("/api/songs/assist/style", {
      method: "POST",
      body: JSON.stringify({
        text,
        force_gospel: opts.forceGospel ?? false,
        mode: opts.mode ?? "style",
        lyrics: opts.lyrics ?? "",
      }),
    }),
  assistLyricsFormat: (text: string) =>
    request<{ optimized: string; notes: string[] }>("/api/songs/assist/lyrics/format", {
      method: "POST",
      body: JSON.stringify({ text }),
    }),
  updateVisibility: (id: number, isPublic: boolean) =>
    request<SongView>(`/api/songs/${id}`, {
      method: "PATCH",
      body: JSON.stringify({ is_public: isPublic }),
    }),
  // —— 管理后台（X-Admin-Token 鉴权）——
  adminStats: () => requestAdmin<AdminStats>("/api/admin/stats"),
  adminSongs: (moderationStatus?: string, status?: string, limit?: number, offset?: number) => {
    const params: Record<string, string> = {};
    if (moderationStatus) params.moderation_status = moderationStatus;
    if (status) params.status = status;
    if (limit != null) params.limit = String(limit);
    if (offset != null) params.offset = String(offset);
    const qs = Object.keys(params).length
      ? "?" + new URLSearchParams(params).toString()
      : "";
    return requestAdmin<AdminSongView[]>(`/api/admin/songs${qs}`);
  },
  adminApprove: (id: number) =>
    requestAdmin(`/api/admin/songs/${id}/approve`, { method: "POST" }),
  adminReject: (id: number, note?: string) =>
    requestAdmin(`/api/admin/songs/${id}/reject`, {
      method: "POST",
      body: JSON.stringify({ note: note || "" }),
    }),
  // —— 后台控制：引擎 / 路由 / 计费（X-Admin-Token 鉴权）——
  // ⚠ adminEngines / adminGetEngine / adminRoutingRules / adminPricingTiers 已移除：
  //   三个列表端点被 api.adminDashboard 完全覆盖——ConfigDashboard.engines /
  //   .routing_rules / .pricing_tiers 就是它们的最简只读形态，music-config 页走的是
  //   dashboard，留着只是同一契约的第二份、随时会漂移的副本；adminGetEngine 更是没有
  //   替代品之外的用途（不存在「只看一个引擎」的 UI 需求）。后端端点保留不动。
  //   真要单独读取时补回即可：一行 requestAdmin。
  adminCreateEngine: (body: Record<string, unknown>) =>
    requestAdmin<EngineConfig>("/api/admin/engines", { method: "POST", body: JSON.stringify(body) }),
  adminUpdateEngine: (provider: string, body: Record<string, unknown>) =>
    requestAdmin<EngineConfig>(`/api/admin/engines/${encodeURIComponent(provider)}`, {
      method: "PUT",
      body: JSON.stringify(body),
    }),
  adminDeleteEngine: (provider: string) =>
    requestAdmin(`/api/admin/engines/${encodeURIComponent(provider)}`, { method: "DELETE" }),
  adminCreateRoutingRule: (body: Record<string, unknown>) =>
    requestAdmin<RoutingRule>("/api/admin/routing-rules", { method: "POST", body: JSON.stringify(body) }),
  adminUpdateRoutingRule: (ruleId: number, body: Record<string, unknown>) =>
    requestAdmin<RoutingRule>(`/api/admin/routing-rules/${ruleId}`, {
      method: "PUT",
      body: JSON.stringify(body),
    }),
  adminDeleteRoutingRule: (ruleId: number) =>
    requestAdmin(`/api/admin/routing-rules/${ruleId}`, { method: "DELETE" }),
  adminCreatePricingTier: (body: Record<string, unknown>) =>
    requestAdmin<PricingTier>("/api/admin/pricing-tiers", { method: "POST", body: JSON.stringify(body) }),
  adminUpdatePricingTier: (tierKey: string, body: Record<string, unknown>) =>
    requestAdmin<PricingTier>(`/api/admin/pricing-tiers/${encodeURIComponent(tierKey)}`, {
      method: "PUT",
      body: JSON.stringify(body),
    }),
  adminDeletePricingTier: (tierKey: string) =>
    requestAdmin(`/api/admin/pricing-tiers/${encodeURIComponent(tierKey)}`, { method: "DELETE" }),
  adminDashboard: () => requestAdmin<ConfigDashboard>("/api/admin/config/dashboard"),
  adminSeedConfig: () => requestAdmin<ConfigDashboard>("/api/admin/config/seed", { method: "POST" }),
  // —— 管理后台：账单对账 + 审计日志（后端 admin.py 的 /billing、/audit 早已存在，此前前端从未包装）——
  adminBilling: () => requestAdmin<Record<string, unknown>>("/api/admin/billing"),
  adminAudit: (limit?: number) =>
    requestAdmin<Record<string, unknown>[]>(
      `/api/admin/audit${limit != null ? `?limit=${limit}` : ""}`,
    ),
  // —— 跨域账号桥接只读查询（后端 admin.py 的 /user-links，P0 新增）——
  adminUserLinks: (opts?: {
    subscriberId?: string;
    musicUserId?: number;
    limit?: number;
  }) => {
    const params: Record<string, string> = {};
    if (opts?.subscriberId) params.subscriber_id = opts.subscriberId;
    if (opts?.musicUserId != null) params.music_user_id = String(opts.musicUserId);
    if (opts?.limit != null) params.limit = String(opts.limit);
    const qs = Object.keys(params).length
      ? "?" + new URLSearchParams(params).toString()
      : "";
    return requestAdmin<UserLinkRow[]>(`/api/admin/user-links${qs}`);
  },
};

// ── FLOW 主站 지원 티켓(2026-10-04) ───────────────────────────────────────────
// CCM 앱의 「문의하기」는 **음악 백엔드가 아니라 FLOW 主站** support_ticket 테이블에 쌓인다.
// 이유: 읽는 곳(admin 운영 콘솔)이 主站 한 곳뿐이라 두 백엔드에 나누면 관리가 갈라진다.
// CORS 는 主站 설정에 https://music.anistar.studio 가 이미 포함돼 있다(2026-10-04 확인).
export const GOSPEL_API_BASE =
  process.env.NEXT_PUBLIC_GOSPEL_API_BASE || "https://anistar.studio";

export type ContactResult = { ticket_id: string; status: string; duplicate: boolean };

/** 留言 전송 — 主站 POST /support/tickets (type='contact').
 *  로그인 불필요(익명은 서버가 IP 핑거프린트로 sub_id 를 대체) + 24h 중복 차단 내장. */
export async function sendContactMessage(
  content: string,
  contact?: string
): Promise<ContactResult> {
  const res = await fetch(`${GOSPEL_API_BASE}/support/tickets`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "omit", // 主站은 다른 origin → cookie 안 실어 보낸다(불필요한 인증 정보 노출 방지)
    body: JSON.stringify({
      type: "contact",
      content: content.trim(),
      ...(contact && contact.trim() ? { contact: contact.trim() } : {}),
    }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error((err as { detail?: string }).detail || `HTTP ${res.status}`);
  }
  return (await res.json()) as ContactResult;
}
