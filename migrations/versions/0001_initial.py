"""initial schema: the fourteen tables

Revision ID: 0001
Revises: None
"""
from alembic import op

from newsintel.store.models import Base

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    Base.metadata.create_all(op.get_bind())


def downgrade() -> None:
    Base.metadata.drop_all(op.get_bind())
