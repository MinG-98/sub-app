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
    per_user_enabled = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=utcnow, nullable=False)

    allocations = relationship(
        "Allocation", back_populates="node", cascade="all, delete-orphan"
    )
    metric_samples = relationship(
        "NodeMetricSample",
        back_populates="node",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    credentials = relationship(
        "UserNodeCredential",
        back_populates="node",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class NodeAgent(Base):
    """Root-only node agent registration and last-seen state."""

    __tablename__ = "node_agents"
    __table_args__ = (UniqueConstraint("node_id", name="uq_node_agent_node"),)

    id = Column(Integer, primary_key=True)
    node_id = Column(
        Integer, ForeignKey("nodes.id", ondelete="CASCADE"), nullable=False
    )
    token_hash = Column(String(128), nullable=False)
    status = Column(String(16), default="pending", nullable=False)
    agent_version = Column(String(64), default="", nullable=False)
    capabilities = Column(Text, default="{}", nullable=False)
    desired_generation = Column(String(128), default="", nullable=False)
    applied_generation = Column(String(128), default="", nullable=False)
    last_seen = Column(DateTime)
    last_error = Column(Text, default="", nullable=False)
    created_at = Column(DateTime, default=utcnow, nullable=False)
    updated_at = Column(DateTime, default=utcnow, nullable=False)


class Friend(Base):
    __tablename__ = "friends"

    id = Column(Integer, primary_key=True)
    uid = Column(String(64), unique=True, nullable=False)
    remark = Column(String(255), default="")
    token = Column(String(64), unique=True, nullable=False, default=new_token)
    enabled = Column(Boolean, default=True, nullable=False)
    flow_limit_gb = Column(Integer, default=0, nullable=False)
    device_limit = Column(Integer, default=0, nullable=False)
    per_user_credentials = Column(Boolean, default=False, nullable=False)
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
    credentials = relationship(
        "UserNodeCredential",
        back_populates="friend",
        cascade="all, delete-orphan",
        passive_deletes=True,
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
    device_token_hash = Column(String(128), unique=True)
    identity_source = Column(String(32), default="legacy_ua_ip", nullable=False)
    device_token_created_at = Column(DateTime)
    device_token_revoked_at = Column(DateTime)
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
    source = Column(String(32), default="unknown", nullable=False)
    sample_key = Column(String(128), index=True)
    created_at = Column(DateTime, default=utcnow, nullable=False)


class ProxyTrafficCounter(Base):
    """Last cumulative per-user counter seen from a proxy core."""

    __tablename__ = "proxy_traffic_counters"
    __table_args__ = (
        UniqueConstraint(
            "source", "node_id", "credential_key", name="uq_proxy_traffic_counter"
        ),
    )

    id = Column(Integer, primary_key=True)
    source = Column(String(32), nullable=False)
    node_id = Column(
        Integer, ForeignKey("nodes.id", ondelete="CASCADE"), nullable=False
    )
    friend_id = Column(
        Integer, ForeignKey("friends.id", ondelete="CASCADE"), nullable=False
    )
    credential_key = Column(String(128), nullable=False)
    last_bytes_in = Column(Integer, default=0, nullable=False)
    last_bytes_out = Column(Integer, default=0, nullable=False)
    updated_at = Column(DateTime, default=utcnow, nullable=False)


class NodeMetricSample(Base):
    """Five-minute node snapshots collected from the Nezha inventory API."""

    __tablename__ = "node_metric_samples"
    __table_args__ = (
        UniqueConstraint("node_id", "bucket", name="uq_node_metric_bucket"),
    )

    id = Column(Integer, primary_key=True)
    node_id = Column(
        Integer, ForeignKey("nodes.id", ondelete="CASCADE"), nullable=False
    )
    nezha_server_id = Column(Integer, nullable=False)
    bucket = Column(DateTime, nullable=False, index=True)
    collected_at = Column(DateTime, default=utcnow, nullable=False, index=True)
    online = Column(Boolean, default=False, nullable=False)
    last_active = Column(DateTime)
    net_in_transfer = Column(Integer, default=0, nullable=False)
    net_out_transfer = Column(Integer, default=0, nullable=False)
    net_in_speed = Column(Integer, default=0, nullable=False)
    net_out_speed = Column(Integer, default=0, nullable=False)
    delta_in = Column(Integer, default=0, nullable=False)
    delta_out = Column(Integer, default=0, nullable=False)
    created_at = Column(DateTime, default=utcnow, nullable=False)

    node = relationship("Node", back_populates="metric_samples")


class CollectorRun(Base):
    """One execution record for the external metrics collector."""

    __tablename__ = "collector_runs"

    id = Column(Integer, primary_key=True)
    started_at = Column(DateTime, default=utcnow, nullable=False, index=True)
    source = Column(String(32), default="nezha", nullable=False)
    finished_at = Column(DateTime)
    status = Column(String(16), default="running", nullable=False)
    nodes_total = Column(Integer, default=0, nullable=False)
    samples_written = Column(Integer, default=0, nullable=False)
    error = Column(Text, default="")


class UserNodeCredential(Base):
    """Versioned per-user credential metadata.

    The actual credential is derived from SUB_APP_SECRET and the version.
    Raw credentials are deliberately not stored in this table.
    """

    __tablename__ = "user_node_credentials"
    __table_args__ = (
        UniqueConstraint(
            "friend_id",
            "node_id",
            "protocol",
            "version",
            name="uq_user_node_credential_version",
        ),
    )

    id = Column(Integer, primary_key=True)
    friend_id = Column(
        Integer, ForeignKey("friends.id", ondelete="CASCADE"), nullable=False
    )
    node_id = Column(
        Integer, ForeignKey("nodes.id", ondelete="CASCADE"), nullable=False
    )
    protocol = Column(String(32), nullable=False)
    credential_name = Column(String(128), nullable=False)
    version = Column(Integer, default=1, nullable=False)
    status = Column(String(16), default="pending", nullable=False)
    created_at = Column(DateTime, default=utcnow, nullable=False)
    rotated_at = Column(DateTime)
    revoked_at = Column(DateTime)
    grace_until = Column(DateTime)
    last_synced_at = Column(DateTime)
    last_error = Column(Text, default="")

    friend = relationship("Friend", back_populates="credentials")
    node = relationship("Node", back_populates="credentials")


def _table_exists(conn, table_name):
    row = conn.exec_driver_sql(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    ).first()
    return row is not None


def _column_exists(conn, table_name, column_name):
    return any(
        row[1] == column_name
        for row in conn.exec_driver_sql(f"PRAGMA table_info({table_name})").all()
    )


def _mark_migration(conn, version):
    conn.exec_driver_sql(
        "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
        (version, utcnow().isoformat()),
    )


def migrate_database(engine):
    """Apply explicit, idempotent SQLite migrations before opening sessions."""

    with engine.begin() as conn:
        conn.exec_driver_sql(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            "version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        current = (
            conn.exec_driver_sql(
                "SELECT COALESCE(MAX(version), 0) FROM schema_migrations"
            ).scalar()
            or 0
        )

        # Bootstrap an empty database once. Existing installations are
        # recorded as baseline v1 and are never recreated on every startup.
        if current < 1:
            if not _table_exists(conn, "nodes"):
                Base.metadata.create_all(bind=conn)
            _mark_migration(conn, 1)
            current = 1

        if current < 2:
            if not _column_exists(conn, "nodes", "per_user_enabled"):
                conn.exec_driver_sql(
                    "ALTER TABLE nodes ADD COLUMN per_user_enabled "
                    "BOOLEAN NOT NULL DEFAULT 0"
                )
            if not _column_exists(conn, "flow_records", "source"):
                conn.exec_driver_sql(
                    "ALTER TABLE flow_records ADD COLUMN source "
                    "VARCHAR(32) NOT NULL DEFAULT 'unknown'"
                )
            if not _column_exists(conn, "flow_records", "sample_key"):
                conn.exec_driver_sql(
                    "ALTER TABLE flow_records ADD COLUMN sample_key " "VARCHAR(128)"
                )
            conn.exec_driver_sql(
                "CREATE TABLE IF NOT EXISTS node_metric_samples ("
                "id INTEGER PRIMARY KEY, "
                "node_id INTEGER NOT NULL REFERENCES nodes(id) ON DELETE CASCADE, "
                "nezha_server_id INTEGER NOT NULL, "
                "bucket DATETIME NOT NULL, "
                "collected_at DATETIME NOT NULL, "
                "online BOOLEAN NOT NULL DEFAULT 0, "
                "last_active DATETIME, "
                "net_in_transfer INTEGER NOT NULL DEFAULT 0, "
                "net_out_transfer INTEGER NOT NULL DEFAULT 0, "
                "net_in_speed INTEGER NOT NULL DEFAULT 0, "
                "net_out_speed INTEGER NOT NULL DEFAULT 0, "
                "delta_in INTEGER NOT NULL DEFAULT 0, "
                "delta_out INTEGER NOT NULL DEFAULT 0, "
                "created_at DATETIME NOT NULL, "
                "CONSTRAINT uq_node_metric_bucket UNIQUE(node_id, bucket))"
            )
            conn.exec_driver_sql(
                "CREATE TABLE IF NOT EXISTS collector_runs ("
                "id INTEGER PRIMARY KEY, "
                "started_at DATETIME NOT NULL, "
                "finished_at DATETIME, "
                "status VARCHAR(16) NOT NULL DEFAULT 'running', "
                "nodes_total INTEGER NOT NULL DEFAULT 0, "
                "samples_written INTEGER NOT NULL DEFAULT 0, "
                "error TEXT DEFAULT '')"
            )
            conn.exec_driver_sql(
                "CREATE TABLE IF NOT EXISTS user_node_credentials ("
                "id INTEGER PRIMARY KEY, "
                "friend_id INTEGER NOT NULL REFERENCES friends(id) ON DELETE CASCADE, "
                "node_id INTEGER NOT NULL REFERENCES nodes(id) ON DELETE CASCADE, "
                "protocol VARCHAR(32) NOT NULL, "
                "credential_name VARCHAR(128) NOT NULL, "
                "version INTEGER NOT NULL DEFAULT 1, "
                "status VARCHAR(16) NOT NULL DEFAULT 'pending', "
                "created_at DATETIME NOT NULL, "
                "rotated_at DATETIME, "
                "revoked_at DATETIME, "
                "grace_until DATETIME, "
                "last_synced_at DATETIME, "
                "last_error TEXT DEFAULT '', "
                "CONSTRAINT uq_user_node_credential_version "
                "UNIQUE(friend_id, node_id, protocol, version))"
            )
            conn.exec_driver_sql(
                "CREATE UNIQUE INDEX IF NOT EXISTS "
                "uq_flow_source_sample ON flow_records(source, sample_key) "
                "WHERE sample_key IS NOT NULL"
            )
            conn.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS ix_node_metric_node_bucket "
                "ON node_metric_samples(node_id, bucket)"
            )
            conn.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS ix_collector_runs_started "
                "ON collector_runs(started_at)"
            )
            _mark_migration(conn, 2)

        if current < 3:
            if not _column_exists(conn, "friends", "per_user_credentials"):
                conn.exec_driver_sql(
                    "ALTER TABLE friends ADD COLUMN per_user_credentials "
                    "BOOLEAN NOT NULL DEFAULT 0"
                )
            if not _column_exists(conn, "devices", "device_token_hash"):
                conn.exec_driver_sql(
                    "ALTER TABLE devices ADD COLUMN device_token_hash VARCHAR(128)"
                )
            if not _column_exists(conn, "devices", "identity_source"):
                conn.exec_driver_sql(
                    "ALTER TABLE devices ADD COLUMN identity_source "
                    "VARCHAR(32) NOT NULL DEFAULT 'legacy_ua_ip'"
                )
            if not _column_exists(conn, "devices", "device_token_created_at"):
                conn.exec_driver_sql(
                    "ALTER TABLE devices ADD COLUMN device_token_created_at DATETIME"
                )
            if not _column_exists(conn, "devices", "device_token_revoked_at"):
                conn.exec_driver_sql(
                    "ALTER TABLE devices ADD COLUMN device_token_revoked_at DATETIME"
                )
            if not _column_exists(conn, "collector_runs", "source"):
                conn.exec_driver_sql(
                    "ALTER TABLE collector_runs ADD COLUMN source "
                    "VARCHAR(32) NOT NULL DEFAULT 'nezha'"
                )
            conn.exec_driver_sql(
                "CREATE TABLE IF NOT EXISTS proxy_traffic_counters ("
                "id INTEGER PRIMARY KEY, "
                "source VARCHAR(32) NOT NULL, "
                "node_id INTEGER NOT NULL REFERENCES nodes(id) ON DELETE CASCADE, "
                "friend_id INTEGER NOT NULL REFERENCES friends(id) ON DELETE CASCADE, "
                "credential_key VARCHAR(128) NOT NULL, "
                "last_bytes_in INTEGER NOT NULL DEFAULT 0, "
                "last_bytes_out INTEGER NOT NULL DEFAULT 0, "
                "updated_at DATETIME NOT NULL, "
                "CONSTRAINT uq_proxy_traffic_counter "
                "UNIQUE(source, node_id, credential_key))"
            )
            conn.exec_driver_sql(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_device_token_hash "
                "ON devices(device_token_hash) WHERE device_token_hash IS NOT NULL"
            )
            conn.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS ix_flow_friend_source_bucket "
                "ON flow_records(friend_id, source, bucket)"
            )
            conn.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS ix_proxy_counter_friend "
                "ON proxy_traffic_counters(friend_id, source)"
            )
            _mark_migration(conn, 3)

        if current < 4:
            conn.exec_driver_sql(
                "CREATE TABLE IF NOT EXISTS node_agents ("
                "id INTEGER PRIMARY KEY, "
                "node_id INTEGER NOT NULL REFERENCES nodes(id) ON DELETE CASCADE, "
                "token_hash VARCHAR(128) NOT NULL, "
                "status VARCHAR(16) NOT NULL DEFAULT 'pending', "
                "agent_version VARCHAR(64) NOT NULL DEFAULT '', "
                "capabilities TEXT NOT NULL DEFAULT '{}', "
                "desired_generation VARCHAR(128) NOT NULL DEFAULT '', "
                "applied_generation VARCHAR(128) NOT NULL DEFAULT '', "
                "last_seen DATETIME, "
                "last_error TEXT NOT NULL DEFAULT '', "
                "created_at DATETIME NOT NULL, "
                "updated_at DATETIME NOT NULL, "
                "CONSTRAINT uq_node_agent_node UNIQUE(node_id))"
            )
            conn.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS ix_node_agents_last_seen "
                "ON node_agents(last_seen)"
            )
            _mark_migration(conn, 4)


def make_session_factory(db_path):
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False, "timeout": 30},
    )

    # SQLite ignores ON DELETE CASCADE unless foreign keys are enabled per
    # connection, so enforce it here.
    @event.listens_for(engine, "connect")
    def _enable_fk(dbapi_conn, _record):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA busy_timeout=30000")
        cur.close()

    migrate_database(engine)
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
