"""Score-to-Music: songs.score_url 원본 악보 보관 URL 컬럼 추가

Revision ID: m1a2b3c4d5e7
Revises: l1a2b3c4d5e6
Create Date: 2026-11-01 12:00:00.000000

/from-score 로 업로드된 악보(이미지/PDF/MusicXML) 원본을 객체 스토리지에 보관하고
그 공개 URL을 songs.score_url 에 기록한다. 용도:
  · 재생성(re-run): 동일 악보로 다른 스타일/엔진으로 다시 생성할 때 원본 재업로드 불필요.
  · 감사(audit): "어떤 악보에서 이 곡이 나왔는가" 추적 (OMR 정확도 디버깅 근거).
  · OMR 재시도: 인식 실패/저신뢰 거부 시 원본을 다시 OMR 에 태워 재시도 가능.

빈 문자열("") = 악보 경로가 아닌 일반 생성(generate/extend/cover/remix).
storage 미설정 노드에서는 from-score 가 빈 문자열로 두고 생성 흐름을 막지 않음.

[컬럼 타입/기본값 선택 근거]
  · Text + nullable=False + server_default="":
    URL 은 가변 길이라 String(N) 보다 Text 가 안전. NOT NULL 로 두되 기본값 빈 문자열
    → 기존 행(일반 생성곡)도 마이그레이션 후 빈 문자열로 채워져 NULL 위험 없음.
  · server_default="" 는 PG/SQLite 양쪽에서 렌더링 문제 없는 안전한 기본값.
    (대조: 불린은 sa.false() 로 써야 함 — l1a2b3c4d5e6 주석 참조. 여긴 불린 아님.)

[方言兼容]
  · op.get_bind() / exec_driver_sql / batch_alter_table 미사용 → offline `--sql` 렌더링 영향 없음.

[마이그레이션 체인]
down_revision 을 l1a2b3c4d5e6(GAP-021, 현재 head) 에 건다.
⚠ revision id `m1a2b3c4d5e7` 은 versions/ 전 디렉터리에서 본 파일만 사용(단독).
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "m1a2b3c4d5e7"
down_revision: Union[str, Sequence[str], None] = "l1a2b3c4d5e6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "songs",
        sa.Column(
            "score_url",
            sa.Text(),
            nullable=False,
            server_default="",
        ),
    )


def downgrade() -> None:
    op.drop_column("songs", "score_url")
