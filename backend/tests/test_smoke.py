"""运行时冒烟：三条核心链路真实跑通（不依赖 GPU / 真实 Suno / S3）。

- /health
- /api/auth/me（本轮新增端点：注册 -> 拿 token -> 带 token 取当前用户；无 token 应 401）
- /api/songs/explore（匿名可访问；本轮后端化搜索的 q 参数不报错）

沿用 tests/conftest.py 的 `client` fixture：测试库 + MockProvider 注入，
全程不触达外部服务。把"能编译"（py_compile）升级为"能运行"。
"""

from fastapi.testclient import TestClient


def test_health(client: TestClient):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_auth_me_roundtrip(client: TestClient):
    email = "smoke_me@example.com"
    pw = "password123"
    reg = client.post("/api/auth/register", json={"email": email, "password": pw})
    assert reg.status_code == 200, reg.text
    token = reg.json()["access_token"]
    assert token

    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200, me.text
    body = me.json()
    assert body["email"] == email
    assert isinstance(body["id"], int)

    # 无凭证应被鉴权依赖拦截。注：注册已在共享 client 上种下 flow_auth cookie，
    # 故此处显式清 cookie 才能还原「无凭证」场景（header 认证时代 client 不持有令牌）。
    client.cookies.clear()
    noauth = client.get("/api/auth/me")
    assert noauth.status_code == 401


def test_explore_anonymous_and_q(client: TestClient):
    res = client.get("/api/songs/explore")
    assert res.status_code == 200
    assert isinstance(res.json(), list)

    # 后端化搜索的 q 参数：走参数化过滤，返回列表（不报错）
    res_q = client.get("/api/songs/explore?q=nevermatchxyz")
    assert res_q.status_code == 200
    assert isinstance(res_q.json(), list)
