"""motifs: 母题与渠道派生（M9 2026-11-22）——一个母题派生四线成品。

Revision ID: 0006_motifs
Revises: 0005_p5_drop_legacy
Create Date: 2026-11-22
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0006_motifs"
down_revision: Union[str, None] = "0005_p5_drop_legacy"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "motifs",
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("note", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_motifs_tenant_id"), "motifs", ["tenant_id"], unique=False)
    op.create_index(op.f("ix_motifs_status"), "motifs", ["status"], unique=False)
    op.add_column("ideas", sa.Column("motif_id", sa.Integer(), nullable=True))
    op.create_index(op.f("ix_ideas_motif_id"), "ideas", ["motif_id"], unique=False)
    op.create_foreign_key("fk_ideas_motif_id_motifs", "ideas", "motifs", ["motif_id"], ["id"])


def downgrade() -> None:
    op.drop_constraint("fk_ideas_motif_id_motifs", "ideas", type_="foreignkey")
    op.drop_index(op.f("ix_ideas_motif_id"), table_name="ideas")
    op.drop_column("ideas", "motif_id")
    op.drop_index(op.f("ix_motifs_status"), table_name="motifs")
    op.drop_index(op.f("ix_motifs_tenant_id"), table_name="motifs")
    op.drop_table("motifs")
