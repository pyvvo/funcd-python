"""a free-text note on an order

Revision ID: 0002_order_note
Revises: 0001_init
"""

import sqlalchemy as sa

from alembic import op

revision = "0002_order_note"
down_revision = "0001_init"


def upgrade() -> None:
    op.add_column("orders", sa.Column("note", sa.String()))
