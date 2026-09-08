"""Core-owned local persistence for canonical conversation and raw evidence."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from typing import Final, Literal, Protocol, TypeVar, cast
from uuid import uuid4

MAX_CANONICAL_TEXT_BYTES: Final = 64 * 1024
MAX_IDENTIFIER_BYTES: Final = 128
SCHEMA_VERSION: Final = 1

type CanonicalRole = Literal["user", "assistant"]
type EvidenceProvenanceKind = Literal["canonical_user", "canonical_assistant"]


class PersistenceError(RuntimeError):
    """Base class for explicit Core persistence failures."""


class PersistenceOpenError(PersistenceError):
    """The configured SQLite database could not be opened or inspected."""


class PersistenceSchemaError(PersistenceError):
    """The database does not have the supported Core schema."""


class PersistenceReadError(PersistenceError):
    """A configured database could not be read safely."""


class PersistenceWriteError(PersistenceError):
    """A canonical or evidence write could not be committed."""


class PersistenceValidationError(PersistenceError):
    """A caller supplied an invalid Core persistence value."""


class PersistenceConflict(PersistenceWriteError):
    """An idempotency key was reused with different canonical data."""


class PersistenceIntegrityError(PersistenceWriteError):
    """A canonical/evidence provenance invariant was violated."""


@dataclass(frozen=True, slots=True)
class CanonicalMessage:
    """One immutable canonical message with durable Core-owned identity."""

    message_id: str
    scope_id: str
    sequence_no: int
    role: CanonicalRole
    text: str
    created_at: str


@dataclass(frozen=True, slots=True)
class EvidenceRecord:
    """One immutable raw observation of a canonical source message.

    Evidence intentionally stores no interpretation or text copy.  The
    canonical source message is the authoritative content reached through
    ``source_message_id``.
    """

    evidence_id: str
    scope_id: str
    source_message_id: str
    source_role: CanonicalRole
    provenance_kind: EvidenceProvenanceKind
    observed_at: str
    created_at: str


class ConversationStore(Protocol):
    """The narrow durable boundary consumed by ``ConversationCore``."""

    def append_user_message(
        self,
        *,
        scope_id: str,
        message_id: str,
        text: str,
        created_at: str | None = None,
    ) -> CanonicalMessage:
        """Atomically append or idempotently replay a canonical user message."""
        ...

    def append_assistant_message(
        self,
        *,
        scope_id: str,
        message_id: str,
        text: str,
        created_at: str | None = None,
    ) -> CanonicalMessage:
        """Atomically append or idempotently replay a canonical assistant message."""
        ...

    def load_canonical_messages(self, scope_id: str) -> tuple[CanonicalMessage, ...]:
        """Load canonical messages in deterministic sequence order."""
        ...

    def append_evidence(
        self,
        *,
        evidence_id: str,
        scope_id: str,
        source_message_id: str,
        provenance_kind: EvidenceProvenanceKind,
        observed_at: str,
        created_at: str | None = None,
    ) -> EvidenceRecord:
        """Append one provenance record for an existing canonical source."""
        ...

    def load_evidence(self, scope_id: str) -> tuple[EvidenceRecord, ...]:
        """Load evidence for audit/testing in canonical source order."""
        ...


_ResultT = TypeVar("_ResultT")

_ROLE_FOR_PROVENANCE: Final = {
    "canonical_user": "user",
    "canonical_assistant": "assistant",
}
_REQUIRED_COLUMNS: Final = {
    "conversation_messages": {
        "message_id",
        "scope_id",
        "sequence_no",
        "role",
        "text",
        "created_at",
    },
    "evidence_records": {
        "evidence_id",
        "scope_id",
        "source_message_id",
        "source_role",
        "provenance_kind",
        "observed_at",
        "created_at",
    },
}
_CREATE_TABLES: Final = (
    """
    CREATE TABLE conversation_messages (
        message_id TEXT PRIMARY KEY,
        scope_id TEXT NOT NULL,
        sequence_no INTEGER NOT NULL CHECK (sequence_no > 0),
        role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
        text TEXT NOT NULL,
        created_at TEXT NOT NULL,
        UNIQUE (scope_id, sequence_no)
    )
    """,
    """
    CREATE TABLE evidence_records (
        evidence_id TEXT PRIMARY KEY,
        scope_id TEXT NOT NULL,
        source_message_id TEXT NOT NULL,
        source_role TEXT NOT NULL CHECK (source_role IN ('user', 'assistant')),
        provenance_kind TEXT NOT NULL CHECK (
            provenance_kind IN ('canonical_user', 'canonical_assistant')
            AND (
                (provenance_kind = 'canonical_user' AND source_role = 'user')
                OR (provenance_kind = 'canonical_assistant' AND source_role = 'assistant')
            )
        ),
        observed_at TEXT NOT NULL,
        created_at TEXT NOT NULL,
        FOREIGN KEY (source_message_id) REFERENCES conversation_messages(message_id),
        UNIQUE (source_message_id, provenance_kind)
    )
    """,
    (
        "CREATE INDEX conversation_messages_scope_sequence "
        "ON conversation_messages(scope_id, sequence_no)"
    ),
    "CREATE INDEX evidence_records_scope_created ON evidence_records(scope_id, created_at)",
)


def validate_scope_id(scope_id: str) -> str:
    """Validate a provider-neutral Core scope identifier."""

    return _validate_identifier(scope_id, "scope_id")


class SQLiteConversationStore:
    """Small single-connection SQLite store for one or more Core scopes.

    ``:memory:`` is useful for deterministic ephemeral tests.  A filesystem
    path is durable across Core/store reconstruction.  The connection is
    guarded because ``ConversationCore`` accepts on its caller thread and
    commits assistant output on its generation worker thread.
    """

    def __init__(self, path: str | Path = ":memory:") -> None:
        self._path = str(path)
        self._lock = RLock()
        self._closed = False
        self._connection: sqlite3.Connection
        self._journal_mode: str
        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(
                self._path,
                timeout=5.0,
                isolation_level=None,
                check_same_thread=False,
            )
            self._connection = connection
            self._connection.row_factory = sqlite3.Row
            self._connection.execute("PRAGMA foreign_keys = ON")
            self._connection.execute("PRAGMA busy_timeout = 5000")
            journal_row = self._connection.execute("PRAGMA journal_mode = WAL").fetchone()
            if journal_row is None or not isinstance(journal_row[0], str):
                raise PersistenceOpenError("SQLite did not report its journal mode")
            self._journal_mode = journal_row[0]
            self._initialize_schema()
        except PersistenceError:
            if connection is not None:
                with suppress(sqlite3.Error):
                    connection.close()
            raise
        except sqlite3.Error as error:
            if connection is not None:
                with suppress(sqlite3.Error):
                    connection.close()
            raise PersistenceOpenError("could not open or inspect the SQLite database") from error

    @property
    def path(self) -> str:
        """Return the configured SQLite path for diagnostics."""

        return self._path

    @property
    def journal_mode(self) -> str:
        """Return the mode accepted by SQLite (``memory`` for ``:memory:``)."""

        return self._journal_mode

    @property
    def schema_version(self) -> int:
        """Return the inspectable SQLite schema version."""

        with self._lock:
            row = self._db.execute("PRAGMA user_version").fetchone()
            if row is None or type(row[0]) is not int:
                raise PersistenceSchemaError("SQLite user_version is unreadable")
            return cast(int, row[0])

    def close(self) -> None:
        """Close the store; subsequent operations fail explicitly."""

        with self._lock:
            if self._closed:
                return
            try:
                self._connection.close()
            except sqlite3.Error as error:
                self._closed = True
                raise PersistenceError("could not close the SQLite database") from error
            self._closed = True

    def __enter__(self) -> SQLiteConversationStore:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def append_user_message(
        self,
        *,
        scope_id: str,
        message_id: str,
        text: str,
        created_at: str | None = None,
    ) -> CanonicalMessage:
        return self._append_message(
            scope_id=scope_id,
            message_id=message_id,
            role="user",
            text=text,
            created_at=created_at,
        )

    def append_assistant_message(
        self,
        *,
        scope_id: str,
        message_id: str,
        text: str,
        created_at: str | None = None,
    ) -> CanonicalMessage:
        return self._append_message(
            scope_id=scope_id,
            message_id=message_id,
            role="assistant",
            text=text,
            created_at=created_at,
        )

    def load_canonical_messages(self, scope_id: str) -> tuple[CanonicalMessage, ...]:
        validate_scope_id(scope_id)

        def read(connection: sqlite3.Connection) -> tuple[CanonicalMessage, ...]:
            rows = connection.execute(
                """
                SELECT message_id, scope_id, sequence_no, role, text, created_at
                FROM conversation_messages
                WHERE scope_id = ?
                ORDER BY sequence_no ASC
                """,
                (scope_id,),
            ).fetchall()
            messages = tuple(self._message_from_row(row) for row in rows)
            for expected_sequence, message in enumerate(messages, start=1):
                if message.sequence_no != expected_sequence:
                    raise PersistenceIntegrityError(
                        f"canonical sequence for scope {scope_id!r} is not contiguous"
                    )
            return messages

        return self._read(read)

    def append_evidence(
        self,
        *,
        evidence_id: str,
        scope_id: str,
        source_message_id: str,
        provenance_kind: EvidenceProvenanceKind,
        observed_at: str,
        created_at: str | None = None,
    ) -> EvidenceRecord:
        validate_scope_id(scope_id)
        _validate_identifier(evidence_id, "evidence_id")
        _validate_identifier(source_message_id, "source_message_id")
        expected_role = _validate_provenance(provenance_kind)
        observed_at = _validate_timestamp(observed_at, "observed_at")
        record_created_at = _timestamp_or_now(created_at, "created_at")

        def write(connection: sqlite3.Connection) -> EvidenceRecord:
            source = self._message_by_id(connection, source_message_id)
            if source is None:
                raise PersistenceIntegrityError(
                    f"evidence source {source_message_id!r} is not canonical"
                )
            if source.scope_id != scope_id:
                raise PersistenceIntegrityError("evidence scope does not match its source message")
            if source.role != expected_role:
                raise PersistenceIntegrityError(
                    "evidence provenance role does not match its source"
                )

            existing_by_id = self._evidence_by_id(connection, evidence_id)
            if existing_by_id is not None:
                if self._same_evidence(
                    existing_by_id,
                    scope_id=scope_id,
                    source_message_id=source_message_id,
                    provenance_kind=provenance_kind,
                    observed_at=observed_at,
                ):
                    return existing_by_id
                raise PersistenceConflict("evidence_id replay conflicts with existing evidence")

            existing_for_source = self._evidence_for_source(
                connection, source_message_id, provenance_kind
            )
            if existing_for_source is not None:
                raise PersistenceConflict(
                    "a source message already has evidence for this provenance kind"
                )
            return self._insert_evidence(
                connection,
                evidence_id=evidence_id,
                source=source,
                provenance_kind=provenance_kind,
                observed_at=observed_at,
                created_at=record_created_at,
            )

        return self._write(write)

    def load_evidence(self, scope_id: str) -> tuple[EvidenceRecord, ...]:
        validate_scope_id(scope_id)

        def read(connection: sqlite3.Connection) -> tuple[EvidenceRecord, ...]:
            rows = connection.execute(
                """
                SELECT
                    e.evidence_id,
                    e.scope_id,
                    e.source_message_id,
                    e.source_role,
                    e.provenance_kind,
                    e.observed_at,
                    e.created_at
                FROM evidence_records AS e
                JOIN conversation_messages AS m
                  ON m.message_id = e.source_message_id
                WHERE e.scope_id = ?
                ORDER BY m.sequence_no ASC, e.created_at ASC, e.evidence_id ASC
                """,
                (scope_id,),
            ).fetchall()
            records = tuple(self._evidence_from_row(row) for row in rows)
            for record in records:
                source = self._message_by_id(connection, record.source_message_id)
                if source is None or source.scope_id != record.scope_id:
                    raise PersistenceIntegrityError(
                        f"evidence {record.evidence_id!r} has an invalid canonical source"
                    )
                if source.role != record.source_role:
                    raise PersistenceIntegrityError(
                        f"evidence {record.evidence_id!r} has an invalid source role"
                    )
                if _ROLE_FOR_PROVENANCE[record.provenance_kind] != record.source_role:
                    raise PersistenceIntegrityError(
                        f"evidence {record.evidence_id!r} has invalid provenance"
                    )
            return records

        return self._read(read)

    def _append_message(
        self,
        *,
        scope_id: str,
        message_id: str,
        role: CanonicalRole,
        text: str,
        created_at: str | None,
    ) -> CanonicalMessage:
        validate_scope_id(scope_id)
        _validate_identifier(message_id, "message_id")
        _validate_role(role)
        _validate_text(text)
        message_created_at = _timestamp_or_now(created_at, "created_at")

        def write(connection: sqlite3.Connection) -> CanonicalMessage:
            existing = self._message_by_id(connection, message_id)
            if existing is not None:
                if existing.scope_id != scope_id or existing.role != role or existing.text != text:
                    raise PersistenceConflict(
                        "message_id replay conflicts with existing canonical message"
                    )
                self._ensure_evidence(connection, existing)
                return existing

            sequence_row = connection.execute(
                """
                SELECT COALESCE(MAX(sequence_no), 0) + 1
                FROM conversation_messages
                WHERE scope_id = ?
                """,
                (scope_id,),
            ).fetchone()
            if sequence_row is None or type(sequence_row[0]) is not int:
                raise PersistenceIntegrityError("SQLite did not return the next canonical sequence")
            sequence_no = cast(int, sequence_row[0])
            connection.execute(
                """
                INSERT INTO conversation_messages
                    (message_id, scope_id, sequence_no, role, text, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (message_id, scope_id, sequence_no, role, text, message_created_at),
            )
            message = CanonicalMessage(
                message_id,
                scope_id,
                sequence_no,
                role,
                text,
                message_created_at,
            )
            self._ensure_evidence(connection, message)
            return message

        return self._write(write)

    def _ensure_evidence(
        self,
        connection: sqlite3.Connection,
        source: CanonicalMessage,
    ) -> EvidenceRecord:
        provenance_kind: EvidenceProvenanceKind = (
            "canonical_user" if source.role == "user" else "canonical_assistant"
        )
        existing = self._evidence_for_source(connection, source.message_id, provenance_kind)
        if existing is not None:
            if existing.scope_id != source.scope_id or existing.source_role != source.role:
                raise PersistenceIntegrityError(
                    f"evidence for {source.message_id!r} does not match its canonical source"
                )
            return existing
        return self._insert_evidence(
            connection,
            evidence_id=str(uuid4()),
            source=source,
            provenance_kind=provenance_kind,
            observed_at=source.created_at,
            created_at=source.created_at,
        )

    def _insert_evidence(
        self,
        connection: sqlite3.Connection,
        *,
        evidence_id: str,
        source: CanonicalMessage,
        provenance_kind: EvidenceProvenanceKind,
        observed_at: str,
        created_at: str,
    ) -> EvidenceRecord:
        record = EvidenceRecord(
            evidence_id,
            source.scope_id,
            source.message_id,
            source.role,
            provenance_kind,
            observed_at,
            created_at,
        )
        connection.execute(
            """
            INSERT INTO evidence_records
                (evidence_id, scope_id, source_message_id, source_role,
                 provenance_kind, observed_at, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.evidence_id,
                record.scope_id,
                record.source_message_id,
                record.source_role,
                record.provenance_kind,
                record.observed_at,
                record.created_at,
            ),
        )
        return record

    def _message_by_id(
        self,
        connection: sqlite3.Connection,
        message_id: str,
    ) -> CanonicalMessage | None:
        row = connection.execute(
            """
            SELECT message_id, scope_id, sequence_no, role, text, created_at
            FROM conversation_messages
            WHERE message_id = ?
            """,
            (message_id,),
        ).fetchone()
        return None if row is None else self._message_from_row(row)

    def _evidence_by_id(
        self,
        connection: sqlite3.Connection,
        evidence_id: str,
    ) -> EvidenceRecord | None:
        row = connection.execute(
            """
            SELECT evidence_id, scope_id, source_message_id, source_role,
                   provenance_kind, observed_at, created_at
            FROM evidence_records
            WHERE evidence_id = ?
            """,
            (evidence_id,),
        ).fetchone()
        return None if row is None else self._evidence_from_row(row)

    def _evidence_for_source(
        self,
        connection: sqlite3.Connection,
        source_message_id: str,
        provenance_kind: EvidenceProvenanceKind,
    ) -> EvidenceRecord | None:
        row = connection.execute(
            """
            SELECT evidence_id, scope_id, source_message_id, source_role,
                   provenance_kind, observed_at, created_at
            FROM evidence_records
            WHERE source_message_id = ? AND provenance_kind = ?
            """,
            (source_message_id, provenance_kind),
        ).fetchone()
        return None if row is None else self._evidence_from_row(row)

    def _message_from_row(self, row: sqlite3.Row) -> CanonicalMessage:
        message_id = row["message_id"]
        scope_id = row["scope_id"]
        sequence_no = row["sequence_no"]
        role = row["role"]
        text = row["text"]
        created_at = row["created_at"]
        try:
            _validate_identifier(message_id, "message_id")
            validate_scope_id(scope_id)
            if type(sequence_no) is not int or sequence_no <= 0:
                raise PersistenceValidationError("sequence_no must be a positive integer")
            _validate_role(role)
            _validate_text(text)
            created_at = _validate_timestamp(created_at, "created_at")
        except PersistenceValidationError as error:
            raise PersistenceIntegrityError(
                "conversation_messages contains invalid data"
            ) from error
        return CanonicalMessage(
            cast(str, message_id),
            cast(str, scope_id),
            sequence_no,
            cast(CanonicalRole, role),
            cast(str, text),
            created_at,
        )

    def _evidence_from_row(self, row: sqlite3.Row) -> EvidenceRecord:
        evidence_id = row["evidence_id"]
        scope_id = row["scope_id"]
        source_message_id = row["source_message_id"]
        source_role = row["source_role"]
        provenance_kind = row["provenance_kind"]
        observed_at = row["observed_at"]
        created_at = row["created_at"]
        try:
            _validate_identifier(evidence_id, "evidence_id")
            validate_scope_id(scope_id)
            _validate_identifier(source_message_id, "source_message_id")
            _validate_role(source_role)
            _validate_provenance(provenance_kind)
            observed_at = _validate_timestamp(observed_at, "observed_at")
            created_at = _validate_timestamp(created_at, "created_at")
        except PersistenceValidationError as error:
            raise PersistenceIntegrityError("evidence_records contains invalid data") from error
        return EvidenceRecord(
            cast(str, evidence_id),
            cast(str, scope_id),
            cast(str, source_message_id),
            cast(CanonicalRole, source_role),
            cast(EvidenceProvenanceKind, provenance_kind),
            observed_at,
            created_at,
        )

    def _initialize_schema(self) -> None:
        version = self.schema_version
        if version == 0:
            tables = self._user_tables()
            if tables:
                raise PersistenceSchemaError(
                    "SQLite database has tables but no supported Core schema version"
                )
            try:
                self._connection.execute("BEGIN IMMEDIATE")
                for statement in _CREATE_TABLES:
                    self._connection.execute(statement)
                self._connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
                self._connection.commit()
            except sqlite3.Error as error:
                self._rollback()
                raise PersistenceSchemaError("could not create the Core SQLite schema") from error
        elif version != SCHEMA_VERSION:
            raise PersistenceSchemaError(
                f"unsupported SQLite schema version {version}; expected {SCHEMA_VERSION}"
            )
        self._verify_schema()

    def _verify_schema(self) -> None:
        for table, required_columns in _REQUIRED_COLUMNS.items():
            rows = self._connection.execute(f"PRAGMA table_info({table})").fetchall()
            columns = {cast(str, row[1]) for row in rows}
            if not required_columns.issubset(columns):
                missing = ", ".join(sorted(required_columns - columns))
                raise PersistenceSchemaError(f"Core SQLite table {table!r} is missing: {missing}")

    def _user_tables(self) -> set[str]:
        rows = self._connection.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
            """
        ).fetchall()
        return {cast(str, row[0]) for row in rows}

    def _read(self, operation: Callable[[sqlite3.Connection], _ResultT]) -> _ResultT:
        with self._lock:
            self._ensure_open()
            try:
                return operation(self._connection)
            except PersistenceError:
                raise
            except sqlite3.Error as error:
                raise PersistenceReadError("could not read the SQLite database") from error

    def _write(self, operation: Callable[[sqlite3.Connection], _ResultT]) -> _ResultT:
        with self._lock:
            self._ensure_open()
            try:
                self._connection.execute("BEGIN IMMEDIATE")
                result = operation(self._connection)
                self._connection.commit()
                return result
            except PersistenceError:
                self._rollback()
                raise
            except sqlite3.Error as error:
                self._rollback()
                raise PersistenceWriteError("could not commit the SQLite write") from error
            except Exception:
                self._rollback()
                raise

    def _ensure_open(self) -> None:
        if self._closed:
            raise PersistenceError("SQLite conversation store is closed")

    @property
    def _db(self) -> sqlite3.Connection:
        self._ensure_open()
        return self._connection

    def _rollback(self) -> None:
        with suppress(sqlite3.Error):
            self._connection.rollback()

    @staticmethod
    def _same_evidence(
        existing: EvidenceRecord,
        *,
        scope_id: str,
        source_message_id: str,
        provenance_kind: EvidenceProvenanceKind,
        observed_at: str,
    ) -> bool:
        return (
            existing.scope_id == scope_id
            and existing.source_message_id == source_message_id
            and existing.provenance_kind == provenance_kind
            and existing.observed_at == observed_at
        )


def _validate_identifier(value: object, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise PersistenceValidationError(f"{field} must be a non-empty string")
    if len(value.encode("utf-8")) > MAX_IDENTIFIER_BYTES:
        raise PersistenceValidationError(f"{field} exceeds {MAX_IDENTIFIER_BYTES} UTF-8 bytes")
    if any(ord(character) < 0x20 or ord(character) == 0x7F for character in value):
        raise PersistenceValidationError(f"{field} contains a control character")
    return value


def _validate_role(value: object) -> CanonicalRole:
    if not isinstance(value, str) or value not in {"user", "assistant"}:
        raise PersistenceValidationError("role must be user or assistant")
    return cast(CanonicalRole, value)


def _validate_provenance(value: object) -> CanonicalRole:
    if not isinstance(value, str) or value not in _ROLE_FOR_PROVENANCE:
        raise PersistenceValidationError(
            "provenance_kind must be canonical_user or canonical_assistant"
        )
    return cast(CanonicalRole, _ROLE_FOR_PROVENANCE[value])


def _validate_text(value: object) -> str:
    if not isinstance(value, str):
        raise PersistenceValidationError("text must be a string")
    if len(value.encode("utf-8")) > MAX_CANONICAL_TEXT_BYTES:
        raise PersistenceValidationError(f"text exceeds {MAX_CANONICAL_TEXT_BYTES} UTF-8 bytes")
    return value


def _validate_timestamp(value: object, field: str) -> str:
    if not isinstance(value, str) or not value or len(value.encode("utf-8")) > 128:
        raise PersistenceValidationError(f"{field} must be a non-empty bounded string")
    return value


def _timestamp_or_now(value: str | None, field: str) -> str:
    if value is not None:
        return _validate_timestamp(value, field)
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
