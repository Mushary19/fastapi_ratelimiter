from sqlalchemy import Column, Integer, String, Boolean, ForeignKey, DateTime
from sqlalchemy import DateTime as TZDateTime
from sqlalchemy.orm import relationship, DeclarativeBase
from datetime import datetime, timezone


class Base(DeclarativeBase):
    pass


class Client(Base):
    __tablename__ = "clients"

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    email = Column(String, unique=True, nullable=False)
    created_at = Column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    api_key = relationship(
        "ApiKey", back_populates="client", uselist=False, cascade="all, delete-orphan"
    )


class ApiKey(Base):
    __tablename__ = "api_keys"

    id = Column(Integer, primary_key=True)
    key = Column(String, unique=True, nullable=False)
    tier = Column(String, default="free")  # free / pro
    is_active = Column(Boolean, default=True)
    client_id = Column(
        Integer, ForeignKey("clients.id", ondelete="CASCADE"), nullable=False
    )
    created_at = Column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    client = relationship("Client", back_populates="api_key")


class DroppedRequest(Base):
    __tablename__ = "dropped_requests"

    id = Column(Integer, primary_key=True)
    client_id = Column(Integer, nullable=True)  # nullable in case key is invalid
    api_key = Column(String, nullable=True)
    endpoint = Column(String, nullable=False)
    method = Column(String, nullable=False)
    reason = Column(String, nullable=False)  # rate_limited / invalid_key
    created_at = Column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
