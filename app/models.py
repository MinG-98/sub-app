import secrets
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    event,
)
from sqlalchemy.orm import DeclarativeBase, relationship, sessionmaker


def utcnow():
    return datetime.now(timezone.utc)


def new_token(nbytes=16):
    return secrets.token_urlsafe(nbytes)


class Base(DeclarativeBase):
    pass


class Node(Base):
    __tablename__ = "nodes"

    id = Column(Integer, primary_key=True)
    name = Column(String(128), nullable=False)
    protocol = Column(String(32), nullable=False)
    uri = Column(Text, nullable=False)
    server = Column(String(255), default="")
    enabled = Column(Boolean, default=True, nullable=False)
    sort_order = Column(Integer, default=0, nullable=False)
    nezha_server_id = Column(Integer)
    created_at = Column(DateTime, default=utcnow, nullable=False)

    allocations = relationship(
        "Allocation", back_populates="node", cascade="all, delete-orphan"
    )


class Friend(Base):
    __tablename__ = "friends"

    id = Column(Integer, primary_key=True)
    uid = Column(String(64), unique=True, nullable=False)
    remark = Column(String(255), default="")
    token = Column(String(64), unique=True, nullable=False, default=new_token)
    enabled = Column(Boolean, default=True, nullable=False)
    flow_limit_gb = Column(Integer, default=0, nullable=False)
    device_limit = Column(Integer, default=0, nullable=False)
    created_at = Column(DateTime, default=utcnow, nullable=False)

    allocations = relationship(
        "Allocation", back_populates="friend", cascade="all, delete-orphan"
    )
    devices = relationship(
        "Device", back_populates="friend", cascade="all, delete-orphan"
    )
    # Without these, deleting a friend leaves orphaned rows that get
    # mis-attributed when SQLite reuses the freed row id.
    fetch_logs = relationship(
        "FetchLog", cascade="all, delete-orphan", passive_deletes=True
    )
    flow_records = relationship(
        "FlowRecord", cascade="all, delete-orphan", passive_deletes=True
    )


class Allocation(Base):
    __tablename__ = "allocations"
    __table_args__ = (UniqueConstraint("friend_id", "node_id", name="uq_friend_node"),)

    id = Column(Integer, primary_key=True)
    friend_id = Column(
        Integer, ForeignKey("friends.id", ondelete="CASCADE"), nullable=False
    )
    node_id = Column(
        Integer, ForeignKey("nodes.id", ondelete="CASCADE"), nullable=False
    )
    created_at = Column(DateTime, default=utcnow, nullable=False)

    friend = relationship("Friend", back_populates="allocations")
    node = relationship("Node", back_populates="allocations")


class Device(Base):
    __tablename__ = "devices"
    __table_args__ = (
        UniqueConstraint("friend_id", "fingerprint", name="uq_friend_fingerprint"),
    )

    id = Column(Integer, primary_key=True)
    friend_id = Column(
        Integer, ForeignKey("friends.id", ondelete="CASCADE"), nullable=False
    )
    fingerprint = Column(String(64), nullable=False)
    label = Column(String(128), default="")
    user_agent = Column(String(255), default="")
    last_ip = Column(String(64), default="")
    fetch_count = Column(Integer, default=0, nullable=False)
    blocked = Column(Boolean, default=False, nullable=False)
    first_seen = Column(DateTime, default=utcnow, nullable=False)
    last_seen = Column(DateTime, default=utcnow, nullable=False)

    friend = relationship("Friend", back_populates="devices")


class FetchLog(Base):
    __tablename__ = "fetch_logs"

    id = Column(Integer, primary_key=True)
    friend_id = Column(Integer, ForeignKey("friends.id", ondelete="CASCADE"))
    device_id = Column(Integer, ForeignKey("devices.id", ondelete="SET NULL"))
    target = Column(String(32), default="")
    ip = Column(String(64), default="")
    user_agent = Column(String(255), default="")
    created_at = Column(DateTime, default=utcnow, nullable=False, index=True)


class FlowRecord(Base):
    """Per-node traffic samples pulled from Nezha, plus per-friend attribution."""

    __tablename__ = "flow_records"

    id = Column(Integer, primary_key=True)
    node_id = Column(Integer, ForeignKey("nodes.id", ondelete="CASCADE"))
    friend_id = Column(Integer, ForeignKey("friends.id", ondelete="CASCADE"))
    device_id = Column(Integer, ForeignKey("devices.id", ondelete="SET NULL"))
    bytes_in = Column(Integer, default=0, nullable=False)
    bytes_out = Column(Integer, default=0, nullable=False)
    bucket = Column(DateTime, nullable=False, index=True)
    created_at = Column(DateTime, default=utcnow, nullable=False)


def make_session_factory(db_path):
    engine = create_engine(
        f"sqlite:///{db_path}", connect_args={"check_same_thread": False}
    )

    # SQLite ignores ON DELETE CASCADE unless foreign keys are enabled per
    # connection, so enforce it here.
    @event.listens_for(engine, "connect")
    def _enable_fk(dbapi_conn, _record):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
