import sqlalchemy as sa
from sqlalchemy import Boolean, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.database import Base
from app.shared.models import BaseModelMixin


class PromptTemplate(BaseModelMixin, Base):
    """Versioned, DB-editable prompt text — one row per (feature, version).
    Append-only, like voice_profiles: a new version never overwrites an old
    one, it deactivates it. Exactly one row per feature has is_active=True
    at a time (enforced by the partial unique index below, not just
    application logic), so "the current prompt for feature X" is always an
    unambiguous single-row lookup. Runtime reads go through
    prompts.get_active_prompt (Redis-cached, falls back to a code-defined
    default if no row exists yet for a feature) — editing or rolling back a
    prompt is then just inserting/repointing a row, no deploy required.
    """

    __tablename__ = "prompt_templates"
    __table_args__ = (
        UniqueConstraint("feature", "version", name="uq_prompt_templates_feature_version"),
        Index(
            "uq_prompt_templates_feature_active",
            "feature",
            unique=True,
            postgresql_where=sa.text("is_active"),
        ),
    )

    feature: Mapped[str] = mapped_column(String(64), index=True)
    version: Mapped[int] = mapped_column(Integer)
    template: Mapped[str] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)


class PromptInvocation(BaseModelMixin, Base):
    """One row per LLM call — the fully assembled prompt actually sent
    (template + whatever dynamic content the caller interpolated), captured
    for audit/debugging independent of whether the call produced a
    persisted business row (e.g. streaming script generation never creates
    a `scripts` row, but its prompt is still logged here).
    """

    __tablename__ = "prompt_invocations"

    feature: Mapped[str] = mapped_column(String(64), index=True)
    template_version: Mapped[int | None] = mapped_column(Integer, default=None)
    rendered_prompt: Mapped[str] = mapped_column(Text)
    # Whatever business row this call was for (channel_id, script_id, ...) —
    # deliberately untyped/no FK, since the referent varies by feature.
    reference_id: Mapped[UUID | None] = mapped_column(UUID(as_uuid=True), default=None, index=True)
