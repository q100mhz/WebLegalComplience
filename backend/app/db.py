import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Integer, String, Text, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from .config import settings

engine = create_async_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Scan(Base):
    __tablename__ = "scans"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    url: Mapped[str] = mapped_column(Text)
    host: Mapped[str] = mapped_column(String(255), index=True)
    # queued | running | done | failed
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    pages_scanned: Mapped[int] = mapped_column(Integer, default=0)
    current_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    result: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    client_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Rule(Base):
    """Реестр нарушений: статьи закона и штрафы. Редактируется в админке."""

    __tablename__ = "rules"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    category: Mapped[str] = mapped_column(String(128))
    title: Mapped[str] = mapped_column(Text)
    law: Mapped[str] = mapped_column(Text)
    koap: Mapped[str] = mapped_column(Text, default="")
    fine_citizen: Mapped[str] = mapped_column(String(255), default="")
    fine_official: Mapped[str] = mapped_column(String(255), default="")
    fine_ip: Mapped[str] = mapped_column(String(255), default="")
    fine_legal: Mapped[str] = mapped_column(String(255), default="")
    fine_repeat: Mapped[str] = mapped_column(String(255), default="")
    confidence: Mapped[str] = mapped_column(String(16), default="low")
    note: Mapped[str] = mapped_column(Text, default="")
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Feedback(Base):
    """Обратная связь по отчёту: ложные срабатывания, номер уведомления РКН и т.п."""

    __tablename__ = "feedback"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    scan_id: Mapped[str] = mapped_column(String(36), index=True)
    finding_id: Mapped[str] = mapped_column(String(64), index=True)
    # false_positive | rkn_claim | other
    kind: Mapped[str] = mapped_column(String(32))
    comment: Mapped[str] = mapped_column(Text, default="")
    contact: Mapped[str] = mapped_column(String(255), default="")
    # new | accepted | rejected | fixed
    status: Mapped[str] = mapped_column(String(16), default="new", index=True)
    admin_note: Mapped[str] = mapped_column(Text, default="")
    client_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class RknOperator(Base):
    """Реестр операторов ПДн из открытых данных Роскомнадзора (перезаливается целиком)."""

    __tablename__ = "rkn_operators"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    inn: Mapped[str] = mapped_column(String(12), index=True)
    name: Mapped[str] = mapped_column(Text, default="")
    reg_number: Mapped[str] = mapped_column(String(64), default="")
    reg_date: Mapped[str] = mapped_column(String(32), default="")


class KV(Base):
    """Служебные значения (статус импорта реестра РКН и т.п.)."""

    __tablename__ = "kv"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict] = mapped_column(JSONB, default=dict)


async def kv_get(db: AsyncSession, key: str) -> dict:
    row = await db.get(KV, key)
    return row.value if row else {}


async def kv_set(db: AsyncSession, key: str, value: dict) -> None:
    row = await db.get(KV, key)
    if row:
        row.value = value
    else:
        db.add(KV(key=key, value=value))


async def init_db() -> None:
    from .registry import RULES

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    # Добавляем новые правила из кода, не трогая отредактированные в админке
    async with SessionLocal() as db:
        existing = set((await db.execute(select(Rule.id))).scalars())
        for r in RULES:
            if r["id"] not in existing:
                db.add(Rule(**r))
        await db.commit()
