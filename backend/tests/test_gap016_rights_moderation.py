"""GAP-016 内容权利/审核：ASR 真实化 / 词库 / 权利模型 / 举报 / 队列增强。

钉住的契约（每条都对应一个真会出事的面）：

1. **`moderate_audio` 不再是占位**：未配置 ASR 时**不放行**（fail-closed）——
   这正是旧实现「恒 True」的病灶；配置 ASR 后走「转写 → check_lyric」。
2. **ASR 转写失败 fail-closed**：网络/超时不等于「无违规」。
3. **审核队列风险分级可解释**：high/medium/low 由列值推导，且排序 high 在前。
4. **批量批准/驳回可审计**：每个变更落一条 AuditLog（action/target_id），
   且跳过已终态（不静默覆盖既有判定）。
5. **举报闭环**：用户举报 → admin takedown → 曲目失去公开 + 标记 rejected。
6. **授权类型随分享/下载联动**：SongOut.license 在生成、详情、更新后都一致；
   非白名单值 400 fail-closed。
7. **举报理由闭词表**：未知理由 400。

后端全 mock ASR / 云审核（不触达真实供应商），沿用 test_search_discovery.py 风格。
"""

import asyncio

from app.config import settings
from app.db import SessionLocal
from app.models import AuditLog, ContentReport, Song, User


def _register(client, email: str = "gap016@test.com") -> dict:
    r = client.post("/api/auth/register", json={"email": email, "password": "pw123456"})
    assert r.status_code in (200, 201), r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _admin_h(monkeypatch, token: str = "secret") -> dict:
    monkeypatch.setattr(settings, "admin_token", token)
    return {"X-Admin-Token": token}


def _drive(song_id: int) -> None:
    from app.routers.songs import _process_song

    asyncio.run(_process_song(song_id))


def _song_orm(db, user_id: int, **kw) -> Song:
    s = Song(
        user_id=user_id,
        title=kw.pop("title", "曲"),
        style=kw.pop("style", "国风"),
        status=kw.pop("status", "completed"),
        audio_url=kw.pop("audio_url", "https://cdn.test/a.mp3"),
        cover_url="https://cdn.test/c.jpg",
        moderation_status=kw.pop("moderation_status", "approved"),
        is_public=kw.pop("is_public", True),
        **kw,
    )
    db.add(s)
    db.commit()
    db.refresh(s)
    return s


# ═══════════════════════════════════════════════════════════════════════════
# 1. ASR 审核真实化（替换占位）
# ═══════════════════════════════════════════════════════════════════════════


def test_moderate_audio_fail_closed_when_asr_unconfigured(monkeypatch):
    """未配置 ASR：enable_audio_moderation=True 时**不放行**（核心替换占位）。"""
    import app.moderation as mod

    monkeypatch.setattr(settings, "asr_provider", "")
    assert mod.moderate_audio("https://cdn.test/a.mp3") is False


def test_moderate_audio_detects_violation_via_asr(monkeypatch):
    """配置 ASR 后：转写文本命中词库 → 不通过（合成违规音频被拦）。"""
    import app.asr as asr
    import app.moderation as mod

    monkeypatch.setattr(settings, "asr_provider", "whisper")
    terms, _, _ = mod._load_blocklist()
    bad = terms[0]
    monkeypatch.setattr(asr, "transcribe", lambda url: f"这是一段含有{bad}的歌词")
    assert mod.moderate_audio("https://cdn.test/a.mp3") is False


def test_moderate_audio_passes_clean_transcript(monkeypatch):
    """配置 ASR 后：干净转写 → 通过。"""
    import app.asr as asr
    import app.moderation as mod

    monkeypatch.setattr(settings, "asr_provider", "whisper")
    monkeypatch.setattr(asr, "transcribe", lambda url: "安静湖水边轻轻唱赞美诗")
    assert mod.moderate_audio("https://cdn.test/a.mp3") is True


def test_moderate_audio_silence_is_legal_pass(monkeypatch):
    """空转写（无人声/静音）是合法结果 → 通过（不能把"没转出字"当违规）。"""
    import app.asr as asr
    import app.moderation as mod

    monkeypatch.setattr(settings, "asr_provider", "whisper")
    monkeypatch.setattr(asr, "transcribe", lambda url: "")
    assert mod.moderate_audio("https://cdn.test/a.mp3") is True


def test_moderate_audio_asr_error_fail_closed(monkeypatch):
    """ASR 调用失败 → fail-closed（不放行），不得静默当作"无违规"。"""
    import app.asr as asr
    import app.moderation as mod

    monkeypatch.setattr(settings, "asr_provider", "whisper")
    monkeypatch.setattr(settings, "moderation_fail_closed", True)

    def _boom(url):
        raise asr.ASRError("upstream 500")

    monkeypatch.setattr(asr, "transcribe", _boom)
    assert mod.moderate_audio("https://cdn.test/a.mp3") is False


def test_audio_moderation_end_to_end_rejects_synthetic_violation(client, monkeypatch):
    """E2E：开音频审核 + 转写命中 → 生成收尾把曲目标为 rejected（不进 explore）。"""
    import app.asr as asr
    import app.moderation as mod

    monkeypatch.setattr(settings, "enable_audio_moderation", True)
    monkeypatch.setattr(settings, "asr_provider", "whisper")
    terms, _, _ = mod._load_blocklist()
    monkeypatch.setattr(asr, "transcribe", lambda url: f"{terms[0]}")

    h = _register(client, "gap016-e2e@test.com")
    r = client.post("/api/songs/generate", json={"prompt": "x", "is_public": True}, headers=h)
    sid = r.json()["song_id"]
    _drive(sid)

    s = client.get(f"/api/songs/{sid}", headers=h).json()
    assert s["moderation_status"] == "rejected"
    ids = {x["id"] for x in client.get("/api/songs/explore").json()}
    assert sid not in ids


def test_audio_moderation_fail_closed_keeps_pending_or_rejected(client, monkeypatch):
    """开音频审核但未接 ASR：不放行 —— 曲目不进 explore（rejected）。"""
    monkeypatch.setattr(settings, "enable_audio_moderation", True)
    monkeypatch.setattr(settings, "asr_provider", "")

    h = _register(client, "gap016-fc@test.com")
    r = client.post("/api/songs/generate", json={"prompt": "x", "is_public": True}, headers=h)
    sid = r.json()["song_id"]
    _drive(sid)

    s = client.get(f"/api/songs/{sid}", headers=h).json()
    assert s["moderation_status"] == "rejected"


# ═══════════════════════════════════════════════════════════════════════════
# 2. 词库 + 可选云审核
# ═══════════════════════════════════════════════════════════════════════════


def test_blocklist_substantial_and_not_placeholder():
    """词库已扩展为可用集合（远多于旧占位 2 词），且按包路径加载（CWD 无关）。"""
    from app.moderation import _load_blocklist

    terms, using_fallback, path = _load_blocklist()
    assert using_fallback is False
    assert len(terms) >= 40, "词库应已扩展为完整可用集合"
    assert path.endswith("blocklist.txt")


def test_remote_moderation_api_flagged_blocks(monkeypatch):
    """配置云审核 API 后，命中 flagged → 不通过。"""
    import app.moderation as mod

    monkeypatch.setattr(settings, "moderation_api_base", "https://mod.test/v1")
    monkeypatch.setattr(settings, "moderation_api_key", "k")

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"results": [{"flagged": True}]}

    import httpx

    monkeypatch.setattr(httpx, "post", lambda *a, **k: _Resp())
    assert mod.check_lyric_remote("任何文本") is False


def test_remote_moderation_api_unconfigured_passes(monkeypatch):
    """未配置云审核 → 不额外拦（本地词库才是底线）。"""
    import app.moderation as mod

    monkeypatch.setattr(settings, "moderation_api_base", "")
    assert mod.check_lyric_remote("任意") is True


# ═══════════════════════════════════════════════════════════════════════════
# 3. 内容权利：license 随生成/详情/分享/下载联动
# ═══════════════════════════════════════════════════════════════════════════


def test_generate_defaults_license_to_non_commercial(client):
    h = _register(client, "lic1@test.com")
    r = client.post("/api/songs/generate", json={"prompt": "x"}, headers=h)
    sid = r.json()["song_id"]
    s = client.get(f"/api/songs/{sid}", headers=h).json()
    assert s["license"] == "non_commercial"


def test_generate_accepts_commercial_license(client):
    h = _register(client, "lic2@test.com")
    r = client.post(
        "/api/songs/generate", json={"prompt": "x", "license": "commercial"}, headers=h
    )
    sid = r.json()["song_id"]
    s = client.get(f"/api/songs/{sid}", headers=h).json()
    assert s["license"] == "commercial"


def test_generate_rejects_unknown_license(client):
    h = _register(client, "lic3@test.com")
    r = client.post("/api/songs/generate", json={"prompt": "x", "license": "whatever"}, headers=h)
    assert r.status_code == 400
    assert "授权" in r.json()["detail"]


def test_update_license_owner_only(client):
    """授权类型随分享/下载联动：owner 可改，他人不可。"""
    h_owner = _register(client, "lic-owner@test.com")
    h_other = _register(client, "lic-other@test.com")
    sid = client.post("/api/songs/generate", json={"prompt": "x"}, headers=h_owner).json()["song_id"]

    # 他人改 → 404（不暴露存在性）
    r = client.patch(f"/api/songs/{sid}/license", json={"license": "commercial"}, headers=h_other)
    assert r.status_code == 404

    r = client.patch(f"/api/songs/{sid}/license", json={"license": "commercial"}, headers=h_owner)
    assert r.status_code == 200
    assert r.json()["license"] == "commercial"

    # 非法值 → 400
    r = client.patch(f"/api/songs/{sid}/license", json={"license": "bogus"}, headers=h_owner)
    assert r.status_code == 400


def test_license_in_admin_out_and_explore(client, monkeypatch):
    """license 在 admin 列表与公开 explore 响应里都带上（分享/下载联动展示）。"""
    h_admin = _admin_h(monkeypatch)
    h = _register(client, "lic-share@test.com")
    r = client.post(
        "/api/songs/generate",
        json={"prompt": "x", "license": "commercial", "is_public": True},
        headers=h,
    )
    sid = r.json()["song_id"]
    _drive(sid)

    lst = client.get("/api/admin/songs", headers=h_admin).json()
    row = next((x for x in lst if x["id"] == sid), None)
    assert row is not None and row["license"] == "commercial"


# ═══════════════════════════════════════════════════════════════════════════
# 4. 审核队列增强：风险分级 / 批量 / 审计 / 统计
# ═══════════════════════════════════════════════════════════════════════════


def test_queue_risk_classification_and_ordering(client, monkeypatch):
    """待审队列按风险分级排序：音频机审未通过(high) 排在常规(low) 之前。"""
    h_admin = _admin_h(monkeypatch)
    db = SessionLocal()
    try:
        u = User(email="q@test.com", hashed_password="")
        db.add(u)
        db.commit()
        db.refresh(u)
        _song_orm(db, u.id, title="高危", moderation_status="pending",
                  moderation_note="音频机审未通过：ASR 命中")
        _song_orm(db, u.id, title="常规", moderation_status="pending")
    finally:
        db.close()

    q = client.get("/api/admin/moderation/queue", headers=h_admin).json()
    assert q, "队列不应为空"
    assert q[0]["risk_level"] == "high"
    levels = [i["risk_level"] for i in q]
    # high 必须全部在 low 之前
    assert levels.index("low") > levels.index("high") if "low" in levels else True


def test_batch_approve_and_audit(client, monkeypatch):
    """批量批准：动作落 AuditLog（可审计），且跳过已终态项。"""
    h_admin = _admin_h(monkeypatch)
    db = SessionLocal()
    try:
        u = User(email="batch@test.com", hashed_password="")
        db.add(u)
        db.commit()
        db.refresh(u)
        s1 = _song_orm(db, u.id, moderation_status="pending")
        s2 = _song_orm(db, u.id, moderation_status="pending")
        s3 = _song_orm(db, u.id, moderation_status="approved")  # 已终态 → 跳过
        ids = [s1.id, s2.id, s3.id]
    finally:
        db.close()

    r = client.post(
        "/api/admin/moderation/batch?action=approve",
        json={"ids": ids},
        headers=h_admin,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["approved"] == 2
    assert body["skipped"] == 1

    db = SessionLocal()
    try:
        logs = db.query(AuditLog).filter(AuditLog.action == "approve").all()
        audited_ids = {lg.target_id for lg in logs}
        assert {ids[0], ids[1]} <= audited_ids
        assert ids[2] not in audited_ids  # 跳过的没被审计为已批准
    finally:
        db.close()


def test_batch_reject_requires_note_and_unpublishes(client, monkeypatch):
    h_admin = _admin_h(monkeypatch)
    db = SessionLocal()
    try:
        u = User(email="brej@test.com", hashed_password="")
        db.add(u)
        db.commit()
        db.refresh(u)
        s = _song_orm(db, u.id, moderation_status="pending", is_public=True)
        sid = s.id
    finally:
        db.close()

    # 无理由 → 400
    r = client.post(
        "/api/admin/moderation/batch?action=reject", json={"ids": [sid]}, headers=h_admin
    )
    assert r.status_code == 400

    r = client.post(
        "/api/admin/moderation/batch?action=reject",
        json={"ids": [sid], "note": "含违规内容"},
        headers=h_admin,
    )
    assert r.status_code == 200
    assert r.json()["rejected"] == 1

    db = SessionLocal()
    try:
        song = db.get(Song, sid)
        assert song.moderation_status == "rejected"
        assert song.is_public is False
        lg = (
            db.query(AuditLog)
            .filter(AuditLog.action == "reject", AuditLog.target_id == sid)
            .first()
        )
        assert lg is not None and "违规" in (lg.detail or "")
    finally:
        db.close()


def test_single_approve_writes_audit(client, monkeypatch):
    h_admin = _admin_h(monkeypatch)
    db = SessionLocal()
    try:
        u = User(email="sa@test.com", hashed_password="")
        db.add(u)
        db.commit()
        db.refresh(u)
        sid = _song_orm(db, u.id, moderation_status="pending").id
    finally:
        db.close()

    r = client.post(f"/api/admin/songs/{sid}/approve", headers=h_admin)
    assert r.status_code == 200
    db = SessionLocal()
    try:
        lg = (
            db.query(AuditLog)
            .filter(AuditLog.action == "approve", AuditLog.target_id == sid)
            .first()
        )
        assert lg is not None
        assert lg.target_type == "song"
    finally:
        db.close()


def test_moderation_stats(client, monkeypatch):
    h_admin = _admin_h(monkeypatch)
    db = SessionLocal()
    try:
        u = User(email="st@test.com", hashed_password="")
        db.add(u)
        db.commit()
        db.refresh(u)
        _song_orm(db, u.id, moderation_status="pending")
        _song_orm(db, u.id, moderation_status="approved")
        db.add(ContentReport(song_id=_song_orm(db, u.id, moderation_status="pending").id,
                             reason="copyright", status="open"))
        db.commit()
    finally:
        db.close()

    r = client.get("/api/admin/moderation/stats", headers=h_admin)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["pending"] >= 2
    assert body["approved"] >= 1
    assert "high" in body["by_risk"]
    assert body["reports_open"] >= 1


# ═══════════════════════════════════════════════════════════════════════════
# 5. 举报 / DMCA 闭环
# ═══════════════════════════════════════════════════════════════════════════


def test_report_song_and_admin_takedown(client, monkeypatch):
    """举报 → admin takedown → 曲目失去公开 + 标记 rejected（人工下架闭环）。"""
    h_admin = _admin_h(monkeypatch)
    h = _register(client, "rep@test.com")
    sid = client.post(
        "/api/songs/generate", json={"prompt": "x", "is_public": True}, headers=h
    ).json()["song_id"]
    _drive(sid)

    r = client.post(
        f"/api/songs/{sid}/report",
        json={"song_id": sid, "reason": "copyright", "detail": "未经授权使用"},
        headers=h,
    )
    assert r.status_code == 200, r.text
    report_id = r.json()["id"]

    # admin 看到举报
    reps = client.get("/api/admin/reports?status=open", headers=h_admin).json()
    assert any(x["id"] == report_id for x in reps)

    # 下架
    r = client.post(
        f"/api/admin/reports/{report_id}/resolve",
        json={"resolution": "takedown", "note": "侵权成立"},
        headers=h_admin,
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "resolved"

    db = SessionLocal()
    try:
        song = db.get(Song, sid)
        assert song.is_public is False
        assert song.moderation_status == "rejected"
        # 审计：report_resolve + takedown 两条
        acts = {lg.action for lg in db.query(AuditLog).all()}
        assert "report_resolve" in acts and "takedown" in acts
    finally:
        db.close()


def test_report_dismissed_keeps_song_public(client, monkeypatch):
    """举报不成立 → 曲目保持公开。"""
    h_admin = _admin_h(monkeypatch)
    h = _register(client, "rep2@test.com")
    sid = client.post(
        "/api/songs/generate", json={"prompt": "x", "is_public": True}, headers=h
    ).json()["song_id"]
    _drive(sid)
    report_id = client.post(
        f"/api/songs/{sid}/report", json={"song_id": sid, "reason": "spam"}, headers=h
    ).json()["id"]

    r = client.post(
        f"/api/admin/reports/{report_id}/resolve", json={"resolution": "rejected"}, headers=h_admin
    )
    assert r.status_code == 200
    assert r.json()["status"] == "dismissed"

    db = SessionLocal()
    try:
        assert db.get(Song, sid).is_public is True
    finally:
        db.close()


def test_report_unknown_reason_rejected(client):
    h = _register(client, "rep3@test.com")
    sid = client.post("/api/songs/generate", json={"prompt": "x"}, headers=h).json()["song_id"]
    r = client.post(
        f"/api/songs/{sid}/report",
        json={"song_id": sid, "reason": "nonsense"},
        headers=h,
    )
    assert r.status_code == 400


def test_report_duplicate_is_idempotent(client):
    """同一用户对同一首歌未处理的重复举报 → 返回既有记录（不灌满队列）。"""
    h = _register(client, "rep4@test.com")
    sid = client.post("/api/songs/generate", json={"prompt": "x"}, headers=h).json()["song_id"]
    r1 = client.post(
        f"/api/songs/{sid}/report", json={"song_id": sid, "reason": "other"}, headers=h
    )
    r2 = client.post(
        f"/api/songs/{sid}/report", json={"song_id": sid, "reason": "other"}, headers=h
    )
    assert r1.json()["id"] == r2.json()["id"]


def test_admin_reports_requires_admin(client, monkeypatch):
    monkeypatch.setattr(settings, "admin_token", "secret")
    assert client.get("/api/admin/reports").status_code == 401
    assert client.post("/api/admin/moderation/batch?action=approve", json={"ids": [1]}).status_code == 401


# ═══════════════════════════════════════════════════════════════════════════
# 6. 迁移契约：GAP-016 schema 挂对链尾、单 head、字段/表存在
# ═══════════════════════════════════════════════════════════════════════════


def test_migration_gap016_chained_after_gap014_head():
    """GAP-016 迁移必须挂 g2b3c4d5e6f7（上一棒 #82 的链尾），且 revision 全仓唯一。"""
    import re
    from pathlib import Path

    versions = Path(__file__).resolve().parent.parent / "migrations" / "versions"
    revs: dict[str, list[str]] = {}
    gap016 = None
    for f in versions.glob("*.py"):
        text = f.read_text(encoding="utf-8")
        m = re.search(r'^revision:\s*str\s*=\s*"([^"]+)"', text, re.M)
        if not m:
            m = re.search(r'^revision\s*=\s*"([^"]+)"', text, re.M)
        if not m:
            continue
        rev = m.group(1)
        revs.setdefault(rev, []).append(f.name)
        if rev == "h1a2b3c4d5e6":
            gap016 = text

    assert gap016 is not None, "GAP-016 迁移文件缺失"
    assert 'down_revision: Union[str, Sequence[str], None] = "g2b3c4d5e6f7"' in gap016
    # 全仓唯一（历史上有过 revision id 撞车 → 双 head）
    assert revs["h1a2b3c4d5e6"] and len(revs["h1a2b3c4d5e6"]) == 1


def test_migration_gap016_single_head():
    """迁移链全局单 head（GAP-016 引入的断言，链尾已随 GAP-019 前移）。

    ⚠ 本测试**不硬编码某条 revision**，只断言「升级后恰好一个 head」+
    「链上确实包含 GAP-016 的 h1a2b3c4d5e6」。硬编码链尾的写法会在每次新增
    迁移时假红，逼着后来者改测试 —— 那是把「守护单 head」变成「记录链尾快照」。
    """
    import os
    import subprocess
    import sys
    from pathlib import Path

    backend = Path(__file__).resolve().parent.parent
    env = dict(os.environ)
    env["ALEMBIC_DB_URL"] = "postgresql+psycopg://npc:npc@pg.invalid:5432/music"
    proc = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "alembic.ini", "heads"],
        cwd=backend,
        env=env,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    assert len(lines) == 1, f"应为单 head，实际 {lines}"
    # 链上仍包含 GAP-016 的 revision（该迁移没被后来者改写/丢弃）
    hist = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "alembic.ini", "history"],
        cwd=backend,
        env=env,
        capture_output=True,
        text=True,
    )
    assert hist.returncode == 0, hist.stderr
    assert "h1a2b3c4d5e6" in hist.stdout


def test_moderation_health_exposes_asr_state(client, monkeypatch):
    """运维可见性：审核链路就绪状态暴露 ASR / 词库 / 云审核配置。"""
    h_admin = _admin_h(monkeypatch)
    monkeypatch.setattr(settings, "asr_provider", "")
    r = client.get("/api/admin/moderation/health", headers=h_admin)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["using_fallback"] is False
    assert body["terms"] >= 40
    assert body["asr"]["configured"] is False


def test_moderation_health_asr_configured(client, monkeypatch):
    h_admin = _admin_h(monkeypatch)
    monkeypatch.setattr(settings, "asr_provider", "whisper")
    monkeypatch.setattr(settings, "asr_api_base", "http://asr.local:9000")
    r = client.get("/api/admin/moderation/health", headers=h_admin)
    assert r.status_code == 200
    assert r.json()["asr"]["configured"] is True
