"""Original document snapshots, guides and per-section progress."""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "study_documents",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_table(
        "study_guides",
        sa.Column(
            "document_id",
            sa.String(),
            sa.ForeignKey("study_documents.id"),
            primary_key=True,
        ),
        sa.Column("section_id", sa.String(), primary_key=True),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("model", sa.String(), nullable=False),
        sa.Column("prompt_version", sa.String(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_table(
        "study_progress",
        sa.Column(
            "document_id",
            sa.String(),
            sa.ForeignKey("study_documents.id"),
            primary_key=True,
        ),
        sa.Column("section_id", sa.String(), primary_key=True),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )


def downgrade():
    op.drop_table("study_progress")
    op.drop_table("study_guides")
    op.drop_table("study_documents")
