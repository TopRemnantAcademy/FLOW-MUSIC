"""D35 回归：身份桥接键必须是 UserLink.flow_subscriber_id 单一路径。
User.flow_subscriber_id（死列，无迁移）应已删除，避免与 UserLink 双重化。
"""

from app.models import User, UserLink


def test_d35_user_has_no_dead_flow_subscriber_id_column():
    col_names = [c.name for c in User.__table__.columns]
    assert "flow_subscriber_id" not in col_names
    assert not hasattr(User, "flow_subscriber_id")


def test_d35_userlink_remains_single_bridge():
    col_names = [c.name for c in UserLink.__table__.columns]
    assert "flow_subscriber_id" in col_names
    assert hasattr(UserLink, "flow_subscriber_id")
