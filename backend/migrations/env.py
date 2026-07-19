import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy.ext.asyncio import async_engine_from_config
from sqlalchemy.pool import NullPool

from app.config import get_settings

# Import every module's models so autogenerate can see them.
from app.modules.analytics import models as analytics_models  # noqa: F401
from app.modules.billing import models as billing_models  # noqa: F401
from app.modules.channels import models as channels_models  # noqa: F401
from app.modules.notifications import models as notifications_models  # noqa: F401
from app.modules.prompts import models as prompts_models  # noqa: F401
from app.modules.scripts import models as scripts_models  # noqa: F401
from app.modules.thumbnails import models as thumbnails_models  # noqa: F401
from app.modules.trending import models as trending_models  # noqa: F401
from app.modules.users import models as users_models  # noqa: F401
from app.modules.voice_profiles import models as voice_profiles_models  # noqa: F401
from app.shared import (
    event_log,  # noqa: F401
    feature_flags,  # noqa: F401
)
from app.shared.database import Base

config = context.config
# Escape % so configparser interpolation survives URL-encoded credentials (e.g. %40).
config.set_main_option("sqlalchemy.url", get_settings().database_url.replace("%", "%%"))

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def _do_run_migrations(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(_do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
