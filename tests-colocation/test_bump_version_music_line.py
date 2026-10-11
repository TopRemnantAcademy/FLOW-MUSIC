"""`scripts/bump-version.js` 의 음악(music) 버전선 지원 회귀 테스트.

배경(2026-09-25 실측):
  ``version.json`` 에는 ``music`` 전용 버전선(music / music.variants.{cn,play})이 있었고
  ``scripts/variants.js`` 도 ``MUSIC_VARIANTS``(com.gospelai.music[.cn|.play])를 정의했는데,
  ``scripts/bump-version.js`` 는 ``VARIANTS``(flow) 만 순회해 **음악 versionCode 를 올릴 방법이
  아예 없었다**. 결과적으로 음악 앱은 versionCode 가 1 에 고정되어 Google Play 재업로드가
  거부된다(동일 versionCode 재사용 불가 — Play 공식 규정).
  ``PLAN_MUSIC_APK_2026-09-13.md`` §301 이 이 공백을 이미 지적해 두었다.

검증 전략:
  스크립트를 **격리된 임시 디렉터리**에서 실제로 실행하고(``--dry`` 아님) version.json 변화를
  직접 단언한다. 실수로 저장소의 진짜 version.json 을 건드리지 않도록 scripts/ 사본 + 임시
  version.json 을 만들어 그 안에서만 실행한다.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = REPO_ROOT / "scripts"

# CI(python:3.13-slim) 에는 node 가 없다(실측: build cnb-j2k-1k3cgb54r, stage test-backend,
# 11 건 전부 FileNotFoundError). 이 테스트들은 Node 스크립트를 **실행**하므로 전제가 node 다.
#
# ⚠️ 모듈 레벨 skipif 가 **실제로 보장하는 것** 이다. 특정 헬퍼(run()) 안에서만 skip 하면
#    그 헬퍼를 거치지 않는 경로가 그대로 터진다 — 아래 _collect() 처럼 subprocess 를 직접
#    부르는 헬퍼가 대표적이며, 실제로 run()-only 가드로는 3 건이 red 로 남았다(실측).
#    모듈 전체를 묶으면 어떤 헬퍼가 node 를 부르든 "실패"가 아니라 "명시적 skip"이 된다.
pytestmark = pytest.mark.skipif(
    shutil.which("node") is None,
    reason="node 실행 파일이 없어 bump-version.js 동작을 검증할 수 없다",
)


@pytest.fixture
def sandbox(tmp_path: Path) -> Path:
    """scripts/{bump-version,variants}.js + version.json 을 가진 격리 작업장."""
    (tmp_path / "scripts").mkdir()
    for name in ("bump-version.js", "variants.js"):
        shutil.copy2(SCRIPTS / name, tmp_path / "scripts" / name)
    (tmp_path / "version.json").write_text(
        json.dumps(
            {
                "versionCode": 1,
                "versionName": "1.0.0",
                "ios": {"buildNumber": 1, "version": "1.0.0"},
                "variants": {
                    "cn": {"versionCode": 1, "versionName": "1.0.0"},
                    "play": {"versionCode": 1, "versionName": "1.0.0"},
                },
                "music": {
                    "versionCode": 1,
                    "versionName": "1.0.0",
                    "variants": {
                        "cn": {"versionCode": 1, "versionName": "1.0.0"},
                        "play": {"versionCode": 1, "versionName": "1.0.0"},
                    },
                },
                "tenants": {},
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return tmp_path


def run(sandbox: Path, *args: str) -> subprocess.CompletedProcess:
    """bump-version.js 를 실행한다.

    ⚠️ CI(python:3.13-slim) 에는 **node 가 없다**. 그대로 subprocess.run(["node", ...])
    를 부르면 FileNotFoundError 로 죽어(실측: build cnb-j2k-1k3cgb54r, stage test-backend,
    11 건 전부 red) "*코드 버그*" 처럼 보인다. variant 버전선 로직은 Node 로 작성된
    스크립트이므로 이 테스트의 전제 자체가 node 이며, 없으면 **명시적으로 skip** 한다.
    (skip 은 허위 통과가 아니고 junit 상 `skipped` 로 집계된다 — SC-24 가드와 같은 규약.)

    아래 가드는 이 헬퍼를 직접 호출하는 경로를 위한 것이고, 모듈 레벨 pytestmark 가
    최종 보증이다(다른 헬퍼가 node 를 부르는 경우까지 포함).
    """
    if shutil.which("node") is None:
        pytest.skip("node 없음 — scripts/bump-version.js(Node) 실행 불가 (CI python:3.13-slim)")
    return subprocess.run(
        ["node", "scripts/bump-version.js", *args],
        cwd=sandbox,
        capture_output=True,
        text=True,
    )


def load(sandbox: Path) -> dict:
    return json.loads((sandbox / "version.json").read_text(encoding="utf-8"))


def test_music_play_line_is_independent_from_flow_play(sandbox: Path):
    """--app=music --variant=play 는 music.variants.play 만 올리고 flow 의 variants.play 는 불변."""
    r = run(sandbox, "patch", "--app=music", "--variant=play")
    assert r.returncode == 0, r.stderr

    v = load(sandbox)
    # 음악 Play 선만 증가
    assert v["music"]["variants"]["play"] == {"versionCode": 2, "versionName": "1.0.1"}
    # flow Play 선 / 음악 루트 / flow 루트 는 모두 불변
    assert v["variants"]["play"] == {"versionCode": 1, "versionName": "1.0.0"}
    assert v["music"]["versionCode"] == 1
    assert v["versionCode"] == 1


def test_flow_play_line_is_independent_from_music_play(sandbox: Path):
    """역방향: --variant=play(app 기본 flow) 는 music 선을 건드리지 않는다."""
    r = run(sandbox, "patch", "--variant=play")
    assert r.returncode == 0, r.stderr

    v = load(sandbox)
    assert v["variants"]["play"] == {"versionCode": 2, "versionName": "1.0.1"}
    assert v["music"]["variants"]["play"] == {"versionCode": 1, "versionName": "1.0.0"}


def test_music_root_line_bumps(sandbox: Path):
    """--app=music(변종 미지정)은 music 루트 버전선을 올린다."""
    r = run(sandbox, "patch", "--app=music", "--platform=android")
    assert r.returncode == 0, r.stderr

    v = load(sandbox)
    assert v["music"]["versionCode"] == 2
    assert v["music"]["versionName"] == "1.0.1"
    # flow 루트는 불변
    assert v["versionCode"] == 1


def test_music_variant_inherits_music_root_not_flow_root(tmp_path: Path):
    """music 루트가 있는데 variants 자식이 없으면 music 루트를 물려받는다(flow 루트가 아니라)."""
    (tmp_path / "scripts").mkdir()
    for name in ("bump-version.js", "variants.js"):
        shutil.copy2(SCRIPTS / name, tmp_path / "scripts" / name)
    (tmp_path / "version.json").write_text(
        json.dumps(
            {
                "versionCode": 5,
                "versionName": "3.1.0",
                "music": {"versionCode": 9, "versionName": "4.0.0"},
                "variants": {},
                "tenants": {},
            }
        ),
        encoding="utf-8",
    )
    r = run(tmp_path, "patch", "--app=music", "--variant=play")
    assert r.returncode == 0, r.stderr
    assert load(tmp_path)["music"]["variants"]["play"] == {
        "versionCode": 10,
        "versionName": "4.0.1",
    }


def test_music_line_created_from_root_when_block_missing(tmp_path: Path):
    """music 블록 자체가 없으면 루트 버전선에서 시작한다(과거로 후퇴 금지)."""
    (tmp_path / "scripts").mkdir()
    for name in ("bump-version.js", "variants.js"):
        shutil.copy2(SCRIPTS / name, tmp_path / "scripts" / name)
    (tmp_path / "version.json").write_text(
        json.dumps({"versionCode": 7, "versionName": "2.3.0", "tenants": {}}),
        encoding="utf-8",
    )
    r = run(tmp_path, "patch", "--app=music", "--platform=android")
    assert r.returncode == 0, r.stderr
    assert load(tmp_path)["music"]["versionCode"] == 8


@pytest.mark.parametrize(
    "args",
    [
        ("patch", "--app=foo"),  # 미허용 앱
        ("patch", "--app=music", "--tenant=abc"),  # 음악은 테넌트 개념 없음
        ("patch", "--app=music", "--variant=zzz"),  # 미허용 변종
        ("patch", "--app=music", "--variant=play", "--platform=ios"),  # 변종은 android 전용
    ],
)
def test_invalid_combinations_are_rejected(sandbox: Path, args: tuple[str, ...]):
    """오배포를 막는 가드는 exit 1 로 실패해야 한다(조용한 통과 금지)."""
    r = run(sandbox, *args)
    assert r.returncode == 1, f"{args} 가 거부되지 않음: {r.stdout}{r.stderr}"
    # version.json 은 훼손되지 않아야 한다
    assert load(sandbox)["music"]["variants"]["play"]["versionCode"] == 1


def test_dry_run_does_not_write(sandbox: Path):
    """--dry 는 출력만 하고 version.json 을 쓰지 않는다."""
    before = (sandbox / "version.json").read_text(encoding="utf-8")
    r = run(sandbox, "patch", "--app=music", "--variant=play", "--dry")
    assert r.returncode == 0, r.stderr
    assert (sandbox / "version.json").read_text(encoding="utf-8") == before


# ══════════════════════════════════════════════════════════════════════
# collect-build.js — 음악 APK 가 FLOW 본체와 구분되는 이름으로 보관돼야 한다
#
# 같은 뿌리의 두 번째 결함: 음악 APK 도 variant 키('default'/'cn'/'play')를 본체와
# 공유하는데 접두어가 'flow-ai-' 로 고정돼 있었다. 그래서 com.gospelai.music.play 로
# 만든 APK 가 builds/flow-ai-play-… 로 저장되어 **본체 Play APK 와 파일명이 같아진다**
# (variants.js 의 appByAppId() 는 있었지만 아무도 호출하지 않았다).
# ══════════════════════════════════════════════════════════════════════
def _collect(sandbox: Path, app_id: str, code: int, name: str) -> subprocess.CompletedProcess:
    """가짜 gradle 산출물 + output-metadata.json 을 만들고 collect-build 를 실행한다."""
    out = sandbox / "android" / "app" / "build" / "outputs" / "apk" / "release"
    out.mkdir(parents=True, exist_ok=True)
    (out / "output-metadata.json").write_text(
        json.dumps(
            {"elements": [{"versionCode": code, "versionName": name}], "applicationId": app_id}
        ),
        encoding="utf-8",
    )
    (out / "app-release.apk").write_bytes(b"fake-apk")
    return subprocess.run(
        ["node", "scripts/collect-build.js", "--type=apk"],
        cwd=sandbox,
        capture_output=True,
        text=True,
    )


def test_music_apk_named_distinctly_from_flow_apk(sandbox: Path):
    """음악 Play APK 와 본체 Play APK 는 **다른 파일명** 이어야 한다.

    둘 다 variant 키가 'play' 이므로 접두어로 분리하지 않으면 구분이 불가능하다.
    이름이 같으면 "음악을 올리려다 본체를 업로드" 하는 배포 사고가 난다.
    """
    scripts = sandbox / "scripts"
    shutil.copy2(SCRIPTS / "collect-build.js", scripts / "collect-build.js")

    r1 = _collect(sandbox, "com.gospelai.app.play", 9, "1.6.0")
    assert r1.returncode == 0, r1.stderr
    r2 = _collect(sandbox, "com.gospelai.music.play", 4, "1.2.0")
    assert r2.returncode == 0, r2.stderr

    names = sorted(p.name for p in (sandbox / "builds").iterdir())
    assert "flow-ai-play-v1.6.0-c9.apk" in names, names
    assert "flow-music-play-v1.2.0-c4.apk" in names, names
    # 접두어 차이만이 아니라 파일이 2개여야 한다(덮어쓰기로 뭉개지지 않았는지)
    assert len(names) == 2, names


def test_all_registered_packages_get_expected_prefix(sandbox: Path):
    """variants.js 에 등록된 6개 패키지가 모두 올바른 접두어로 명명되는지 확인한다."""
    shutil.copy2(SCRIPTS / "collect-build.js", sandbox / "scripts" / "collect-build.js")
    cases = [
        ("com.gospelai.app", "flow-ai-default"),
        ("com.gospelai.app.cn", "flow-ai-cn"),
        ("com.gospelai.app.play", "flow-ai-play"),
        ("com.gospelai.music", "flow-music-default"),
        ("com.gospelai.music.cn", "flow-music-cn"),
        ("com.gospelai.music.play", "flow-music-play"),
    ]
    for i, (app_id, expected) in enumerate(cases, start=1):
        r = _collect(sandbox, app_id, i, "1.0.0")
        assert r.returncode == 0, (app_id, r.stderr)
        assert f"{expected}-v1.0.0-c{i}.apk" in r.stdout, (app_id, r.stdout)


def test_unregistered_package_is_rejected(sandbox: Path):
    """variants.js 에 없는 패키지는 조용히 flow-ai 로 떨어지지 않고 실패해야 한다."""
    shutil.copy2(SCRIPTS / "collect-build.js", sandbox / "scripts" / "collect-build.js")
    r = _collect(sandbox, "com.example.unknown", 1, "1.0.0")
    assert r.returncode != 0, "미등록 패키지가 통과했다"
    builds = sandbox / "builds"
    assert not builds.exists() or not list(builds.iterdir())


# ══════════════════════════════════════════════════════════════════════
# 版本線の「発明」禁止 — music 版本线不得凭空造出版本名
#
# 背景(2026-09-26 実測):
#   `ensureBlock` は music 版本線を新規作成するとき、上位ブロックに versionName が
#   無ければ `'1.0.0'` を **勝手に代入** していた。これは「無い値を発明する」欠陥で、
#   一度書かれると以降そのバージョン線は上位と無関係な値から分岐し、
#   ストアに上がった名前と version.json が静かに食い違う。
#
#   実測(A): music が `{"variants":{"play":{...}}}` のみ(music.versionName 無し)
#             → music.versionName に root(flow) の値ではなく '1.0.0' が生えた。
#   実測(B): `--app=music` の iOS 線が flow の `v.ios` を継承し、
#            **音楽ビルドが flow の iOS buildNumber を消費**していた。
# ══════════════════════════════════════════════════════════════════════
def _write(tmp_path: Path, payload: dict) -> Path:
    (tmp_path / "scripts").mkdir()
    for name in ("bump-version.js", "variants.js"):
        shutil.copy2(SCRIPTS / name, tmp_path / "scripts" / name)
    (tmp_path / "version.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return tmp_path


def test_music_ios_line_does_not_consume_flow_ios_build_number(tmp_path: Path):
    """`--app=music` の iOS 線は flow の iOS ビルド番号を消費しないこと。

    music と flow は**別パッケージ**なので iOS ビルド番号も独立。
    flow の `v.ios` を継承すると、音楽の再提出で flow 側の番号まで進み
    「ストアに出した番号」と「リポジトリの番号」が食い違う。
    """
    sandbox = _write(
        tmp_path,
        {
            "versionCode": 11,
            "versionName": "9.9.9",
            "ios": {"buildNumber": 11, "version": "9.9.9"},
            "variants": {},
            "music": {"versionCode": 1, "versionName": "1.0.0"},
            "tenants": {},
        },
    )
    r = run(sandbox, "minor", "--app=music")
    assert r.returncode == 0, r.stderr

    v = load(sandbox)
    assert v["ios"] == {"buildNumber": 11, "version": "9.9.9"}, "flow の iOS 線が書き換わった"
    # 音楽 iOS 線は **音楽自身** の versionCode(1) を起点に作られる(android 側 +1 の後に iOS +1)。
    # flow の 11 を引き継いでいたら 12 になる — そうなっていないことを見る。
    assert v["music"]["ios"]["buildNumber"] == 3, "音楽 iOS 線が自分の番号から始まっていない"
    assert v["music"]["ios"]["version"] == "1.2.0"


def test_music_ios_line_reuses_music_own_bucket(tmp_path: Path):
    """音楽 iOS 線は 2 回目の呼び出しで自分の直前値から連番になること(幂等性の裏側)。"""
    sandbox = _write(
        tmp_path,
        {
            "versionCode": 1,
            "versionName": "1.0.0",
            "ios": {"buildNumber": 1, "version": "1.0.0"},
            "variants": {},
            "music": {"versionCode": 1, "versionName": "1.0.0"},
            "tenants": {},
        },
    )
    assert run(sandbox, "build", "--app=music").returncode == 0
    first = load(sandbox)["music"]["ios"]
    assert run(sandbox, "build", "--app=music").returncode == 0
    second = load(sandbox)["music"]["ios"]

    # 初回は music.versionCode(1) を起点に android 側が +1、続けて iOS 側も +1 → 3。
    # 2 回目は前回値 3 の続き。
    assert first["buildNumber"] == 3, first
    assert second["buildNumber"] == 4, f"音楽 iOS 番号が連番でない: {first} → {second}"
    # build は版名を据え置く(root の 1.0.0 を引き継がず、音楽自身の値)
    assert second["version"] == "1.0.0"


def test_music_variant_block_missing_inherits_music_root_code(tmp_path: Path):
    """music.variants.play が無いとき、music 基本線の versionCode を継承すること。

    ここで `|| 1` に落ちると「音楽はまだ 1 のはず」という思い込みで
    実際の基本線を無視した値が書き込まれる(実測: music.versionCode が 4 でも 1 が書かれた)。
    """
    sandbox = _write(
        tmp_path,
        {
            "versionCode": 1,
            "versionName": "1.0.0",
            "variants": {},
            "music": {"versionCode": 4, "versionName": "1.4.0"},
            "tenants": {},
        },
    )
    r = run(sandbox, "patch", "--app=music", "--variant=play")
    assert r.returncode == 0, r.stderr
    assert load(sandbox)["music"]["variants"]["play"] == {
        "versionCode": 5,
        "versionName": "1.4.1",
    }
