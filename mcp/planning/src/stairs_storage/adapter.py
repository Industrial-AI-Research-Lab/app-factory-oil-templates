"""Bounded, read-only SQLAlchemy model adapter."""

from collections import OrderedDict
from threading import RLock
from time import monotonic

from sqlalchemy import LargeBinary, String, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker


class _Base(DeclarativeBase):
    """Declarative base for the read-only model tables."""


class _ResModel(_Base):
    """ORM mapping of the res_model table (resource estimators)."""
    __tablename__ = "res_model"
    id: Mapped[int] = mapped_column(primary_key=True)
    model_type: Mapped[str] = mapped_column(String)
    name: Mapped[str] = mapped_column(String)
    data: Mapped[bytes] = mapped_column(LargeBinary)
    measurement_type: Mapped[str | None] = mapped_column(String)
    category: Mapped[str | None] = mapped_column(String)


class _TimeModel(_Base):
    """ORM mapping of the time_model table (duration estimators)."""
    __tablename__ = "time_model"
    id: Mapped[int] = mapped_column(primary_key=True)
    model_type: Mapped[str] = mapped_column(String)
    name: Mapped[str] = mapped_column(String)
    data: Mapped[bytes] = mapped_column(LargeBinary)
    measurement_type: Mapped[str | None] = mapped_column(String)
    category: Mapped[str | None] = mapped_column(String)


class MschmAdapter:
    """Read-only adapter with a small refreshable per-process cache."""

    def __init__(self, url: str, *, cache_size: int = 256, cache_ttl: float = 300) -> None:
        """Open a pooled engine (5 connections, pre-ping) with a TTL cache."""
        self.engine = create_engine(url, pool_size=5, max_overflow=0, pool_pre_ping=True)
        self._sessions = sessionmaker(bind=self.engine)
        self._cache_size = cache_size
        self._cache_ttl = cache_ttl
        self._cache: OrderedDict[tuple, tuple[float, list[dict[str, object]]]] = OrderedDict()
        self._lock = RLock()

    def _get(self, table: type[_ResModel] | type[_TimeModel], **values: object) -> list[dict[str, object]]:
        """Select rows by exact column matches through the TTL cache."""
        key = (table.__tablename__, *values.items())
        now = monotonic()
        with self._lock:
            cached = self._cache.get(key)
            if cached and now - cached[0] <= self._cache_ttl:
                self._cache.move_to_end(key)
                return cached[1]
            self._cache.pop(key, None)
        query = select(table)
        for field, value in values.items():
            if value is not None:
                query = query.where(getattr(table, field) == value)
        with self._sessions() as session:
            rows = [{column.name: getattr(row, column.name) for column in table.__table__.columns} for row in session.scalars(query).all()]
        with self._lock:
            self._cache[key] = (now, rows)
            self._cache.move_to_end(key)
            while len(self._cache) > self._cache_size:
                self._cache.popitem(last=False)
        return rows

    def get_res_model(self, *, name: str, category: str, model_type: str | None = None, measurement_type: str | None = None) -> list[dict[str, object]]:
        """Return resource-model rows; None filters are skipped, not matched."""
        return self._get(_ResModel, name=name, category=category, model_type=model_type, measurement_type=measurement_type)

    def get_perf_model(self, *, name: str, category: str, model_type: str | None = None, measurement_type: str | None = None) -> list[dict[str, object]]:
        """Return performance-model rows; None filters are skipped, not matched."""
        return self._get(_TimeModel, name=name, category=category, model_type=model_type, measurement_type=measurement_type)

    def close(self) -> None:
        """Clear the cache and dispose the engine; safe to call twice."""
        with self._lock:
            self._cache.clear()
        self.engine.dispose()
