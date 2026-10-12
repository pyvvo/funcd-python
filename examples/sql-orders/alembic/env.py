"""Alembic's environment, offline only: the migrations render to SQLite SQL, which the migrate function
applies through context.sql."""

from alembic import context

context.configure(
    url="sqlite://", literal_binds=True, render_as_batch=True, dialect_opts={"paramstyle": "named"}
)

with context.begin_transaction():
    context.run_migrations()
