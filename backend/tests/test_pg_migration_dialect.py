"""PG 迁移方言兼容回归闸门（#106）。

本文件锁死两件在真实 PG 上曾经断链的事，全部**无需 PG 服务**即可断言：

1. **离线渲染不崩**：`alembic upgrade head --sql`（URL 为 postgresql+psycopg）
   必须整条链渲染成功。历史上 6 处迁移用 `sa.inspect(op.get_bind())` 做存在性
   幂等判断，offline 下 `op.get_bind()` 是 MockConnection（无反射）→
   渲染断在中途，产不出可审阅的 SQL。

2. **Boolean 默认值方言无关**：PG 要求 `true`/`false`，SQLite 习惯的
   `"0"/"1"/"false"` 字符串会渲染成 `DEFAULT '0'` / `DEFAULT 'false'`，
   PG 直接 `DatatypeMismatch: column ... is of type boolean but default
   expression is of type integer` → 整条 upgrade 回滚。
   断言离线 SQL 里不出现这类写法，且布尔列一律 `DEFAULT true|false`。

3. **数值默认值不带引号**：`server_default="0"` 在 PG 上渲染成
   `DEFAULT '0'`（text 字面量）。PG 能隐式 cast，故不报错，但属脏写法 ——
   默认值表达式与列类型（NUMERIC/INTEGER）不符，且 `alembic check` 在
   反射回读时无法区分。统一写 `sa.text("0")`。
4. **源码级防回退**：扫描 versions/*.py，禁止再写 SQLite-only 的布尔默认值
   字面量（`server_default="0"` 等），也禁止数值列上的带引号数字默认值。
   这是"下次别写回去"的硬闸门。
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parent.parent  # backend/
VERSIONS_DIR = BACKEND_ROOT / "migrations" / "versions"

# 沙箱无需真实 PG：offline(--sql) 只做渲染，不建连接。用户/pw/host 均为占位。
PG_OFFLINE_URL = "postgresql+psycopg://npc:npc@pg.invalid:5432/music_offline"


def _render_pg_sql() -> str:
    """以 PG URL 跑 `alembic upgrade head --sql`，返回渲染出的 SQL 原文。"""
    import os

    env = dict(os.environ)
    env["ALEMBIC_DB_URL"] = PG_OFFLINE_URL
    proc = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "alembic.ini", "upgrade", "head", "--sql"],
        cwd=BACKEND_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    # stderr 带上渲染日志；失败时把 stderr 尾部附进断言信息，便于定位断点。
    if proc.returncode != 0:
        tail = "\n".join(proc.stderr.splitlines()[-30:])
        pytest.fail(f"PG 离线渲染失败（returncode={proc.returncode}）:\n{tail}")
    return proc.stdout


@pytest.fixture(scope="module")
def pg_sql() -> str:
    """整条链只渲染一次，多个断言共享（渲染约 0.5s）。"""
    return _render_pg_sql()


def test_pg_offline_render_reaches_head(pg_sql):
    """① 渲染必须覆盖到链尾（当前单 head h1a2b3c4d5e6），而不是中途抛异常。"""
    assert "h1a2b3c4d5e6" in pg_sql, "离线 SQL 未渲染到 head revision"
    # 至少要真的产出 DDL，而不是只写了版本表。
    assert "CREATE TABLE" in pg_sql and "ALTER TABLE" in pg_sql


def test_pg_offline_no_sqlite_only_boolean_defaults(pg_sql):
    """② 布尔列默认值不得是 0/1/'false' —— 必须是 true/false。

    失败样例（修复前实拍）：
        ALTER TABLE users ADD COLUMN email_verified BOOLEAN DEFAULT 0 NOT NULL
        is_public BOOLEAN DEFAULT 'false' NOT NULL
    """
    bad = re.findall(
        r"(?i)\bBOOLEAN\b[^\n,;]*\bDEFAULT\s+('?(?:0|1|false|true)'?)\b", pg_sql
    )
    offenders = [v for v in bad if v not in ("true", "false")]
    assert not offenders, (
        f"PG 离线 SQL 出现 SQLite-only 布尔默认值: {offenders}\n"
        "症状: psycopg.errors.DatatypeMismatch (column is of type boolean but "
        "default expression is of type integer)"
    )
    # 反向确认：确实渲染出了布尔默认值（避免正则空匹配造成的假绿）。
    assert re.search(r"(?i)\bBOOLEAN\b[^\n,;]*\bDEFAULT\s+(true|false)\b", pg_sql), (
        "未在离线 SQL 中找到任何 BOOLEAN DEFAULT true/false —— 断言可能已失效"
    )


def test_pg_offline_key_boolean_columns_are_false(pg_sql):
    """②' 逐个钉死 issue 点名的关键列（防止有人只改一半）。"""
    expectations = {
        "email_verified": "false",
        "mfa_enabled": "false",
        "is_public": "false",
        "is_default": "false",
        "cancel_at_period_end": "false",
    }
    for column, expected in expectations.items():
        pattern = re.compile(
            rf"(?i)\b{column}\b\s+BOOLEAN\s+DEFAULT\s+{expected}\b"
        )
        assert pattern.search(pg_sql), (
            f"{column} 未渲染为 BOOLEAN DEFAULT {expected}（PG 上必须是 "
            f"{expected}，不能是 0/'false'）"
        )
    # enabled 语义为 True 的列（engine_configs / routing_rules / pricing_tiers）
    assert len(re.findall(r"(?i)\benabled\b\s+BOOLEAN\s+DEFAULT\s+true\b", pg_sql)) >= 3
    # playlists.is_public（#108 新增表）同样必须是 false，不能是 'false'
    assert re.search(r"(?i)\bis_public\b\s+BOOLEAN\s+DEFAULT\s+false\b", pg_sql)


def test_pg_offline_and_sqlite_agree_on_column_set(pg_sql):
    """③ 方言只该改变量值的**写法**，不该改变 schema 结构。

    弱形式校验：离线 PG SQL 里出现的表名，应与迁移链建出的业务表一致。
    （强形式在 test_alembic.py 用真实 sqlite 库比对 metadata。）
    """
    import app.models  # noqa: F401  触发注册
    import app.billing.models  # noqa: F401
    from app.db import Base

    for table in Base.metadata.tables:
        assert re.search(rf"(?i)CREATE TABLE {re.escape(table)}\b", pg_sql), (
            f"表 {table} 未出现在 PG 离线 SQL 中"
        )


def test_no_sqlite_only_boolean_default_literals_in_sources():
    """④ 源码闸门：迁移里禁止再写 SQLite-only 的布尔默认值字面量。

    这是"下次别写回去"的硬约束 —— 上面的离线渲染断言只能拦住已存在的写法，
    这条拦住新增。判据：同一逻辑行里出现 Boolean 且 server_default 是
    0/1/false/true 的**字符串字面量**（正确写法是 sa.true()/sa.false()）。
    """
    literal_default = re.compile(r"""server_default\s*=\s*["'](0|1|false|true)["']""")
    # sa.text("0") / sa.text("1") 用在 Boolean 上同样是方言错误。
    text_zero_one = re.compile(r"""sa\.text\(\s*["'](0|1)["']\s*\)""")

    offenders: list[str] = []
    for path in sorted(VERSIONS_DIR.glob("*.py")):
        for lineno, line in enumerate(path.read_text().splitlines(), start=1):
            if "Boolean" not in line:
                continue
            if literal_default.search(line) or text_zero_one.search(line):
                offenders.append(f"{path.name}:{lineno}: {line.strip()}")

    assert not offenders, (
        "迁移中的布尔默认值必须方言无关（sa.true()/sa.false()）:\n"
        + "\n".join(offenders)
    )


def test_pg_offline_numeric_defaults_are_unquoted(pg_sql):
    """⑤ 数值默认值不得渲染成带引号的字符串字面量。

    `server_default="0"` 会被 SQLAlchemy 当成**字符串**，PG 渲染出
    `DEFAULT '0'`（text 类型），靠 PG 的隐式 cast 才落到 NUMERIC/INTEGER 上。
    能跑，但是脏写法：默认值表达式与列类型不符，回读时也难辨原意。
    正确写法 `sa.text("0")` 渲染为 `DEFAULT 0`。

    这里按 issue 给出的 gate 口径直接断言（`DEFAULT '0'` / `DEFAULT '1'` 必须为空）。
    """
    offenders = re.findall(r"DEFAULT '(?:0|1)'", pg_sql)
    assert not offenders, (
        f"PG 离线 SQL 出现带引号的数值默认值 {offenders} —— 应改为无引号字面量"
        "（迁移里写 sa.text(\"0\")）"
    )
    # 反向确认：确实渲染出了无引号的数值默认值（避免正则空匹配造成的假绿）。
    assert re.search(r"\bDEFAULT 0\b", pg_sql), (
        "未在离线 SQL 中找到任何无引号 DEFAULT 0 —— 断言可能已失效"
    )


def test_no_quoted_numeric_default_literals_in_sources():
    """⑥ 源码闸门：数值列上的 server_default 不得是带引号的数字字符串。

    上面的离线渲染断言只拦「已渲染出的」写法；这条拦住**新增**的写法。
    判据：同一逻辑行里出现 Numeric/Integer/Float 列且 server_default 是
    数字字符串字面量（如 "0" / "1" / "0.0"）。正确写法 sa.text("0")。

    ⚠ 本闸门只看 server_default 右侧，不误伤 String 列的默认值
      （server_default="" / "mp3" / "CNY" 等是正常的字符串默认值）。
    """
    numeric_col = re.compile(
        r"sa\.(?:Numeric|DECIMAL|Integer|BigInteger|SmallInteger|Float|REAL)\b"
    )
    quoted_num = re.compile(r"""server_default\s*=\s*["'][0-9]+(?:\.[0-9]+)?["']""")

    offenders: list[str] = []
    for path in sorted(VERSIONS_DIR.glob("*.py")):
        for lineno, line in enumerate(path.read_text().splitlines(), start=1):
            if line.lstrip().startswith("#") or not numeric_col.search(line):
                continue
            if quoted_num.search(line):
                offenders.append(f"{path.name}:{lineno}: {line.strip()}")

    assert not offenders, (
        "数值列的默认值不得写成带引号的数字字符串（PG 渲染成 DEFAULT '0'）:\n"
        "改用 sa.text(\"0\"):\n" + "\n".join(offenders)
    )
