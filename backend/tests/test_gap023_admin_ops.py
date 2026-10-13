"""GAP-023 admin 运营扩展：用户管理 / 财务结算导出 / 特性开关。

钉住的契约（每条都对应一个会真出事的面）：

1. **运营商动作全部可审计**：封禁/解封/改档/退款/开关变更都落一条 AuditLog
   （`action` + `target_type` + `target_id`），而不是只留在访问日志里。
2. **封禁真的挡门**：被封账号无法登录；且**已签发令牌**在下次访问时被回收
   （订阅降回 free）—— 否则「封了他还能用」才是运维眼里的常态。
3. **改档拒绝未知档位**（fail-closed），且与 entitlements 单一真相源一致。
4. **角色调整显式 501**：本架构没有「用户角色」概念，不假装成功（拒绝幻觉）。
5. **财务导出是 CSV**：带 BOM（Excel 中文不乱码）、金额用结算等价人民币、
   发票台账只含已付订单且不伪造票号。
6. **退款幂等**：重复退款 409，不二次改写订单/订阅。
7. **特性开关判定确定性**：同一 (key, user) 恒定；deny 压过 allow；
   未登录在百分比灰度下不命中；min_tier 未知值不满足（fail-closed）。
8. **开关写端点 viewer 403 / 未授权 401**（沿用既有 RBAC 中间件）。
"""

from app.billing import service as billing_service
from app.config import settings
from app.db import SessionLocal
from app.feature_flags import _in_rollout, is_enabled, normalize_key
from app.billing.models import PaymentOrder
from app.models import AuditLog, FeatureFlag, User


def _register(client, email: str) -> dict:
    r = client.post("/api/auth/register", json={"email": email, "password": "pw123456"})
    assert r.status_code in (200, 201), r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _admin(monkeypatch, role: str = "super") -> dict:
    monkeypatch.setattr(settings, "admin_token", "secret")
    monkeypatch.setattr(settings, "admin_viewer_token", "viewer")
    return {"X-Admin-Token": "secret" if role == "super" else "viewer"}


def _uid(db, email: str) -> int:
    u = db.query(User).filter(User.email == email).first()
    assert u is not None
    return u.id


def _actions(action: str) -> list[AuditLog]:
    db = SessionLocal()
    try:
        return db.query(AuditLog).filter(AuditLog.action == action).order_by(AuditLog.id).all()
    finally:
        db.close()


# ══════════════════════════════════════════════════════════════════════════════
# 1. 鉴权面：越权 / 未授权
# ══════════════════════════════════════════════════════════════════════════════


def test_new_admin_endpoints_require_admin(client, monkeypatch):
    """未配置 admin_token → 503（环境门控，非 500）；配了但没令牌 → 401。"""
    monkeypatch.setattr(settings, "admin_token", "")
    assert client.get("/api/admin/users").status_code == 503

    monkeypatch.setattr(settings, "admin_token", "secret")
    for path in (
        "/api/admin/users",
        "/api/admin/settlement",
        "/api/admin/feature-flags",
    ):
        assert client.get(path, headers={}).status_code == 401, path


def test_viewer_cannot_mutate_ops_endpoints(client, monkeypatch):
    """viewer 只读：改档 / 封禁 / 开关写入一律 403（由既有 RBAC 中间件拦截）。"""
    _register(client, "viewer_target@test.com")
    vh = _admin(monkeypatch, "viewer")
    uid = _uid(SessionLocal(), "viewer_target@test.com")

    assert client.get("/api/admin/users", headers=vh).status_code == 200
    assert client.post(f"/api/admin/users/{uid}/ban", json={"reason": "x"}, headers=vh).status_code == 403
    assert client.post(f"/api/admin/users/{uid}/tier", json={"tier_key": "lite"}, headers=vh).status_code == 403
    assert client.post("/api/admin/feature-flags", json={"key": "x"}, headers=vh).status_code == 403
    assert client.delete("/api/admin/feature-flags/k", headers=vh).status_code == 403


# ══════════════════════════════════════════════════════════════════════════════
# 2. 用户管理
# ══════════════════════════════════════════════════════════════════════════════


def test_user_list_search_paging_and_no_credentials_leak(client, monkeypatch):
    h = _admin(monkeypatch)
    _register(client, "alpha@test.com")
    _register(client, "beta@test.com")

    r = client.get("/api/admin/users", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] >= 2
    assert {"total", "items"} <= set(body)

    row = next(i for i in body["items"] if i["email"] == "alpha@test.com")
    # 运营需要邮箱定位账号，但**任何凭据材料**都不得出现在响应里。
    for forbidden in ("hashed_password", "password", "mfa_secret", "reset_token", "verify_token"):
        assert forbidden not in row

    # 邮箱模糊搜索 + 按 id 搜索（运营手上只有一个信息）
    assert client.get("/api/admin/users?q=alpha", headers=h).json()["total"] == 1
    uid = _uid(SessionLocal(), "alpha@test.com")
    assert client.get(f"/api/admin/users?q={uid}", headers=h).json()["total"] >= 1

    # 分页：limit=1 时 total 仍是全集（前端要显示"共 N 条"）
    paged = client.get("/api/admin/users?limit=1&offset=0", headers=h).json()
    assert len(paged["items"]) == 1 and paged["total"] >= 2


def test_ban_blocks_login_and_revokes_entitlement(client, monkeypatch):
    """封禁 = 挡住登录 + 回收付费权限（已签发令牌下次访问即失效）。"""
    h = _admin(monkeypatch)
    user_h = _register(client, "banme@test.com")
    uid = _uid(SessionLocal(), "banme@test.com")

    # 先给一个付费档（模拟已订阅）
    db = SessionLocal()
    try:
        u = db.get(User, uid)
        u.tier_key = "standard"
        db.commit()
    finally:
        db.close()

    # 无理由封禁被拒（申诉/审计需要理由）
    assert client.post(f"/api/admin/users/{uid}/ban", json={"reason": "  "}, headers=h).status_code == 400

    r = client.post(f"/api/admin/users/{uid}/ban", json={"reason": "滥用生成"}, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["is_banned"] is True
    assert r.json()["ban_reason"] == "滥用生成"

    # ① 登录被挡（密码正确也不行）
    r = client.post("/api/auth/login", json={"email": "banme@test.com", "password": "pw123456"})
    assert r.status_code == 401
    assert "封禁" in r.json()["detail"]

    # ② 已签发令牌：下一次鉴权请求即回收付费档（无调度器，只能在访问时回收）
    assert client.get("/api/auth/me", headers=user_h).status_code == 200
    db = SessionLocal()
    try:
        assert db.get(User, uid).tier_key in (None, "", "free")
    finally:
        db.close()

    # ③ 审计留痕
    rows = _actions("user_ban")
    assert len(rows) == 1
    assert rows[0].target_type == "user" and rows[0].target_id == uid
    assert "滥用生成" in (rows[0].detail or "")
    assert rows[0].actor_role == "super"


def test_unban_restores_login_and_is_audited(client, monkeypatch):
    h = _admin(monkeypatch)
    _register(client, "unban@test.com")
    uid = _uid(SessionLocal(), "unban@test.com")
    client.post(f"/api/admin/users/{uid}/ban", json={"reason": "误封排查"}, headers=h)

    r = client.post(f"/api/admin/users/{uid}/unban", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["is_banned"] is False

    ok = client.post("/api/auth/login", json={"email": "unban@test.com", "password": "pw123456"})
    assert ok.status_code == 200
    assert _actions("user_unban")[0].target_id == uid


def test_change_tier_validates_and_audits(client, monkeypatch):
    h = _admin(monkeypatch)
    _register(client, "tier@test.com")
    uid = _uid(SessionLocal(), "tier@test.com")

    # 未知档位 fail-closed（白名单来自 entitlements.VALID_TIERS）
    bad = client.post(f"/api/admin/users/{uid}/tier", json={"tier_key": "godmode"}, headers=h)
    assert bad.status_code == 400

    ok = client.post(f"/api/admin/users/{uid}/tier", json={"tier_key": "sacred"}, headers=h)
    assert ok.status_code == 200
    assert ok.json()["tier_key"] == "sacred"

    # 同档位重复设置 → 409（不产生无意义的审计噪音）
    assert client.post(f"/api/admin/users/{uid}/tier", json={"tier_key": "sacred"}, headers=h).status_code == 409

    # 降回 free → 归一化为空（free 是"无订阅"的表示，不落库字符串）
    back = client.post(f"/api/admin/users/{uid}/tier", json={"tier_key": "free"}, headers=h)
    assert back.status_code == 200 and back.json()["tier_key"] is None

    details = [a.detail for a in _actions("user_tier_change")]
    assert any("free → sacred" in (d or "") for d in details)
    assert any("sacred → free" in (d or "") for d in details)


def test_change_role_is_501_not_fake_success(client, monkeypatch):
    """角色调整：本架构无「用户角色」概念 → 501 + 明确替代方案（不静默假装成功）。"""
    h = _admin(monkeypatch)
    _register(client, "role@test.com")
    uid = _uid(SessionLocal(), "role@test.com")

    assert client.post(f"/api/admin/users/{uid}/role", json={"role": "genius"}, headers=h).status_code == 400
    r = client.post(f"/api/admin/users/{uid}/role", json={"role": "admin"}, headers=h)
    assert r.status_code == 501
    assert "ADMIN_TOKEN" in r.json()["detail"]
    assert _actions("user_role_change") == []  # 没有发生的事不写审计


def test_user_detail_404(client, monkeypatch):
    h = _admin(monkeypatch)
    assert client.get("/api/admin/users/999999", headers=h).status_code == 404


# ══════════════════════════════════════════════════════════════════════════════
# 3. 财务：结算 / 导出 / 退款
# ══════════════════════════════════════════════════════════════════════════════


def _paid_order(email: str, *, amount_cny: float = 39.0, provider: str = "manual", status: str = "paid"):
    db = SessionLocal()
    try:
        u = db.query(User).filter(User.email == email).first()
        assert u is not None
        o = PaymentOrder(
            user_id=u.id,
            provider=provider,
            tier_key="standard",
            amount_cny=amount_cny,
            amount=amount_cny,
            currency="CNY",
            status=status,
            provider_order_id=f"po-{email}",
        )
        db.add(o)
        db.commit()
        db.refresh(o)
        return o.id
    finally:
        db.close()


def test_settlement_summary_and_csv_exports(client, monkeypatch):
    h = _admin(monkeypatch)
    _register(client, "fin@test.com")
    _paid_order("fin@test.com", amount_cny=39.0)
    _paid_order("fin@test.com", amount_cny=11.0, status="refunded")

    s = client.get("/api/admin/settlement", headers=h)
    assert s.status_code == 200, s.text
    body = s.json()
    assert body["paid_amount_cny"] == 39.0
    assert body["refunded_amount_cny"] == 11.0
    assert body["net_amount_cny"] == 28.0
    assert body["by_provider"].get("manual") == 39.0

    csvr = client.get("/api/admin/settlement/export", headers=h)
    assert csvr.status_code == 200
    assert csvr.headers["content-type"].startswith("text/csv")
    assert csvr.text.startswith("\ufeff")  # BOM：Excel 打开中文列名/邮箱不乱码
    assert "user_email" in csvr.text.splitlines()[0]
    assert "fin@test.com" in csvr.text
    assert "attachment" in csvr.headers["content-disposition"]
    assert "filename*=UTF-8''" in csvr.headers["content-disposition"]

    # 发票台账只含已付（refunded 不进开票额），且不伪造票号
    inv = client.get("/api/admin/invoices/export", headers=h)
    assert inv.status_code == 200
    lines = [ln for ln in inv.text.splitlines() if ln.strip()]
    assert len(lines) == 2  # header + 1 张待开票
    assert "to_issue" in lines[1]
    assert "invoice_no" not in inv.text.splitlines()[0]

    # 导出本身也留痕（谁在什么时候导了什么）
    assert _actions("settlement_export")[0].detail.startswith("导出订单明细")
    assert _actions("invoices_export")[0].target_type == "invoice"


def test_refund_marks_order_and_is_idempotent(client, monkeypatch):
    h = _admin(monkeypatch)
    # 用户与订单直接建库（本用例的测试面是退款状态机，不需要走注册/登录）
    _register(client, "refund@test.com")
    oid = _paid_order("refund@test.com", provider="manual")

    db = SessionLocal()
    try:
        u = db.query(User).filter(User.email == "refund@test.com").first()
        u.tier_key = "standard"
        db.commit()
        uid = u.id
    finally:
        db.close()

    r = client.post(f"/api/admin/orders/{oid}/refund", json={"order_id": oid, "reason": "用户申请"}, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "refunded"

    db = SessionLocal()
    try:
        assert db.get(PaymentOrder, oid).status == "refunded"
        # 权限回收：钱退了会员不能还在（账实不符是运营最常见的事故）
        assert db.get(User, uid).tier_key in (None, "", "free")
    finally:
        db.close()

    # 幂等：重复退款 409，不二次改写（body 可省略 —— 端点的 body 是可选依赖）
    again = client.post(f"/api/admin/orders/{oid}/refund", headers=h)
    assert again.status_code == 409

    detail = _actions("order_refund")[0].detail
    assert f"order={oid}" in detail and "用户申请" in detail

    assert client.post("/api/admin/orders/999999/refund", headers=h).status_code == 404


# ══════════════════════════════════════════════════════════════════════════════
# 4. 特性开关：判定语义
# ══════════════════════════════════════════════════════════════════════════════


def _flag(**kw) -> FeatureFlag:
    f = FeatureFlag(
        key=kw.pop("key", "k"),
        description="",
        enabled=kw.pop("enabled", True),
        rollout=kw.pop("rollout", 0),
        allow_user_ids=kw.pop("allow_user_ids", ""),
        deny_user_ids=kw.pop("deny_user_ids", ""),
        min_tier=kw.pop("min_tier", ""),
    )
    return f


def test_flag_decision_is_deterministic_and_deny_wins():
    f = _flag(rollout=100, allow_user_ids="7", deny_user_ids="7")
    # deny 压过 allow：灰度出事故时"把几个账号关掉"必须立刻生效
    assert is_enabled(f, user_id=7, tier_key="sacred") is False
    assert is_enabled(f, user_id=8, tier_key="sacred") is True

    # 确定性：同一 (key, user) 恒定（不能用随机数，否则用户每次刷新都在不同分支）
    f2 = _flag(key="song_stem_export", rollout=50)
    assert {is_enabled(f2, user_id=42, tier_key="free") for _ in range(20)} == {
        is_enabled(f2, user_id=42, tier_key="free")
    }
    assert _in_rollout("song_stem_export", 42, 50) in (True, False)


def test_flag_fail_closed_cases():
    # 缺失的开关 = 关闭
    assert is_enabled(None, user_id=1, tier_key="enterprise") is False
    # 平台级关闭 → 名单也不能开（enabled 是总闸）
    assert is_enabled(_flag(enabled=False, allow_user_ids="1"), user_id=1) is False
    # 百分比灰度对匿名（无 user_id）不命中 —— 灰度必须落在具体的人身上才能复核
    assert is_enabled(_flag(rollout=50), user_id=None) is False
    # 未知 min_tier → 不满足（拼错一个词不该把功能发给所有人）
    assert is_enabled(_flag(rollout=100, min_tier="goden"), user_id=1, tier_key="enterprise") is False
    # 档位门槛：free 不达 standard，sacred 达
    f = _flag(rollout=100, min_tier="standard")
    assert is_enabled(f, user_id=1, tier_key=None) is False
    assert is_enabled(f, user_id=1, tier_key="standard") is True
    assert is_enabled(f, user_id=1, tier_key="enterprise") is True
    # 全量开关（rollout=100 无门槛）对匿名也生效
    assert is_enabled(_flag(rollout=100), user_id=None) is True


def test_flag_key_normalization():
    assert normalize_key(" Song_Stem ") == "song_stem"
    for bad in ("", "song.stem", "Song-Stem", "x" * 65, "曲"):
        try:
            normalize_key(bad)
        except ValueError:
            continue
        raise AssertionError(f"应拒绝非法 key: {bad!r}")


# ══════════════════════════════════════════════════════════════════════════════
# 5. 特性开关：admin 端到端 + 前端读取
# ══════════════════════════════════════════════════════════════════════════════


def test_feature_flag_crud_preview_and_audit(client, monkeypatch):
    h = _admin(monkeypatch)

    # 新建：默认关闭（建开关不该顺手放量）
    r = client.post("/api/admin/feature-flags", json={"key": "Song_Stem", "description": "分轨导出"}, headers=h)
    assert r.status_code == 201, r.text
    assert r.json()["key"] == "song_stem" and r.json()["enabled"] is False

    # 重复 key → 409；非法 key / 越界 rollout → 400
    assert client.post("/api/admin/feature-flags", json={"key": "song_stem"}, headers=h).status_code == 409
    assert client.post("/api/admin/feature-flags", json={"key": "bad.key"}, headers=h).status_code == 400
    assert client.post("/api/admin/feature-flags", json={"key": "ok_key", "rollout": 101}, headers=h).status_code == 400

    # 更新
    up = client.put(
        "/api/admin/feature-flags/song_stem",
        json={"enabled": True, "rollout": 100, "allow_user_ids": "1,2", "min_tier": "standard"},
        headers=h,
    )
    assert up.status_code == 200, up.text
    body = up.json()
    assert body["enabled"] is True and body["rollout"] == 100 and body["min_tier"] == "standard"

    # 判定预览：逐层依据（靠读字段心算 hash+名单+档位几乎必错）
    pv = client.get("/api/admin/feature-flags/song_stem/preview?user_id=1&tier_key=standard", headers=h)
    assert pv.status_code == 200
    assert pv.json()["enabled"] is True
    assert pv.json()["steps"]["in_allow_list"] is True

    # 审计记录变更前后（灰度事故复盘靠这个）
    row = _actions("feature_flag_update")[0]
    assert row.target_id == body["id"] and "→" in (row.detail or "")
    assert _actions("feature_flag_create")[0].detail.startswith("song_stem")

    # 删除 → 404 之后
    assert client.delete("/api/admin/feature-flags/song_stem", headers=h).status_code == 200
    assert client.delete("/api/admin/feature-flags/song_stem", headers=h).status_code == 404
    assert client.get("/api/admin/feature-flags/song_stem/preview", headers=h).status_code == 404
    assert _actions("feature_flag_delete")[0].target_id == body["id"]


def test_feature_flags_mine_requires_login_and_only_reports_true(client, monkeypatch):
    h = _admin(monkeypatch)

    # ⚠ **先**断言未登录，再注册：注册会往共享 client 种 auth cookie（conftest 的
    #   client 是 session 级、cookies 只在测试**结束后**清），放到注册之后断言
    #   测出来的是"已登录"而不是"未登录"——那这条断言就白写了。
    assert client.get("/api/admin/feature-flags-mine").status_code == 401

    user_h = _register(client, "flaguser@test.com")
    uid = _uid(SessionLocal(), "flaguser@test.com")

    client.post("/api/admin/feature-flags", json={"key": "on_for_all", "enabled": True, "rollout": 100}, headers=h)
    client.post("/api/admin/feature-flags", json={"key": "off_everything", "enabled": False}, headers=h)
    client.post("/api/admin/feature-flags", json={"key": "allowlisted", "enabled": True, "allow_user_ids": str(uid)}, headers=h)

    r = client.get("/api/admin/feature-flags-mine", headers=user_h)
    assert r.status_code == 200, r.text
    flags = r.json()["flags"]
    # 只下发生效的 key："不存在"与"关闭"在灰度语义里是同一件事
    assert flags.get("on_for_all") is True
    assert flags.get("allowlisted") is True
    assert "off_everything" not in flags


def test_ban_revokes_via_lazy_path_not_only_login(client, monkeypatch):
    """封禁的**唯一**执行点若只在登录口，则"封禁前签发的令牌"会照常放行 —— 这里钉住反例。"""
    _admin(monkeypatch)
    _register(client, "lazy@test.com")
    uid = _uid(SessionLocal(), "lazy@test.com")
    db = SessionLocal()
    try:
        u = db.get(User, uid)
        u.tier_key = "sacred"
        db.commit()
    finally:
        db.close()

    # 直接调用服务层回收（与 get_current_user 同一路径），验证幂等
    db = SessionLocal()
    try:
        u = db.get(User, uid)
        u.is_banned = True
        db.commit()
        billing_service.enforce_ban(db, u)
        billing_service.enforce_ban(db, db.get(User, uid))  # 第二次：不重复写
        assert db.get(User, uid).tier_key in (None, "", "free")
    finally:
        db.close()
    assert _actions("user_ban") == []  # 服务层回收不写审计（审计属于 admin 动作）
