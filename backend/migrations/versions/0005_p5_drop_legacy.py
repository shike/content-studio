"""P5 收口：DROP 全部 13 张旧表（素材面 5 + 内容面 4 + 数据面 4）。

数据已全部迁入新域（素材 P2 / 内容 P3 / 数据 P4，迁移脚本见 git 历史与
docs/TDD.md §15 记录），旧表自 2026-11-16 起零读写。不可逆（downgrade 不还原——
旧表数据只存在于迁移前 pg_dump 快照，drop 前由 sync-server 自动生成）。

Revision ID: 0005_p5_drop_legacy
Revises: 9a3302c66a18
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0005_p5_drop_legacy"
down_revision: Union[str, None] = "9a3302c66a18"
branch_labels = None
depends_on = None

_LEGACY = (
    # 素材面（P2 迁 sources/materials/material_analyses）
    "benchmark_videos", "watch_candidates", "radar_topics",
    "content_items", "falsify_items",
    # 内容面（P3 迁 channels/ideas/contents/publications/channel_styles）
    "topic_cards", "scripts", "articles", "style_profiles",
    # 数据面（P4 迁 accounts/metrics_snapshots/analytics_reports/pipeline_ledger）
    "watch_accounts", "account_metrics_daily", "video_weekly_reports", "daily_ledger",
)


def upgrade() -> None:
    # CASCADE：旧表互引（articles→scripts→topic_cards）+ 索引随表清
    for t in _LEGACY:
        op.execute(f'DROP TABLE IF EXISTS "{t}" CASCADE')


def downgrade() -> None:
    # 不可逆：旧表数据只在迁移前 pg_dump 快照里（sync-server 每次发版自动生成）。
    pass
