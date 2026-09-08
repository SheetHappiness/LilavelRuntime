"""Persistence, provenance, restart, and failure regression tests."""

from __future__ import annotations

import sqlite3
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from queue import Queue
from typing import Any, cast

import pytest

from lilavel_core import (
    CanonicalMessage,
    ContextMessage,
    ConversationCore,
    EvidenceRecord,
    GenerationAccepted,
    GenerationCancelled,
    GenerationCompleted,
    GenerationEvent,
    GenerationFailedEvent,
    PersistenceConflict,
    PersistenceIntegrityError,
    PersistenceOpenError,
    PersistenceSchemaError,
    PersistenceValidationError,
    PersistenceWriteError,
    SQLiteConversationStore,
    TextDelta,
)
from lilavel_core.persistence import SCHEMA_VERSION, EvidenceProvenanceKind
from lilavel_core.sidecar_protocol import ProtocolError


class FakeGeneration:
    def __init__(self, generation_id: str, epoch: int) -> None:
        self.generation_id = generation_id
        self.epoch = epoch
        self._events: Queue[GenerationEvent] = Queue()

    def emit(self, event: GenerationEvent) -> None:
        self._events.put(event)

    def events(self) -> Iterator[GenerationEvent]:
        while True:
            event = self._events.get()
            yield event
            if isinstance(event, (GenerationCompleted, GenerationCancelled, GenerationFailedEvent)):
                return


class FakeRuntime:
    def __init__(self) -> None:
        self.generations: list[FakeGeneration] = []
        self.requests: list[Any] = []
        self.cancelled_ids: list[str] = []
        self._lock = threading.Lock()

    def generate(self, request: Any) -> FakeGeneration:
        with self._lock:
            self.requests.append(request)
            generation = FakeGeneration(
                f"fake-generation-{len(self.generations) + 1}",
                len(self.generations) + 1,
            )
            self.generations.append(generation)
            return generation

    def cancel(self, generation_id: str) -> bool:
        with self._lock:
            self.cancelled_ids.append(generation_id)
            generation = next(
                candidate
                for candidate in self.generations
                if candidate.generation_id == generation_id
            )
        generation.emit(GenerationCancelled(generation.generation_id, generation.epoch))
        return True

    def generation(self, index: int = 0) -> FakeGeneration:
        wait_until(lambda: len(self.generations) > index)
        return self.generations[index]


class FailingUserStore(SQLiteConversationStore):
    def append_user_message(self, **_: Any) -> CanonicalMessage:
        raise PersistenceWriteError("injected user write failure")


class FailingAssistantStore(SQLiteConversationStore):
    def append_assistant_message(self, **_: Any) -> CanonicalMessage:
        raise PersistenceWriteError("injected assistant write failure")


class FailingEvidenceStore(SQLiteConversationStore):
    def _ensure_evidence(
        self,
        connection: sqlite3.Connection,
        source: CanonicalMessage,
    ) -> EvidenceRecord:
        raise PersistenceWriteError("injected evidence write failure")


def wait_until(check: Callable[[], bool], timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while not check():
        if time.monotonic() >= deadline:
            raise AssertionError("condition did not become true before the deadline")
        time.sleep(0.001)


def complete_generation(generation: FakeGeneration, text: str) -> None:
    generation.emit(GenerationAccepted(generation.generation_id, generation.epoch))
    generation.emit(TextDelta(generation.generation_id, generation.epoch, text))
    generation.emit(GenerationCompleted(generation.generation_id, generation.epoch))


def test_user_acceptance_is_durable_immediately_and_has_raw_evidence() -> None:
    runtime = FakeRuntime()
    store = SQLiteConversationStore(":memory:")
    core = ConversationCore(runtime, scope_id="scope-a", store=store)

    run = core.start_turn("accepted now")

    stored = store.load_canonical_messages("scope-a")
    assert len(stored) == 1
    assert stored[0].role == "user"
    assert stored[0].text == "accepted now"
    assert stored[0].message_id == run.user_message_id
    assert stored[0].sequence_no == 1
    assert store.load_evidence("scope-a") == (
        EvidenceRecord(
            store.load_evidence("scope-a")[0].evidence_id,
            "scope-a",
            run.user_message_id,
            "user",
            "canonical_user",
            stored[0].created_at,
            stored[0].created_at,
        ),
    )
    run.cancel()
    assert run.wait(2.0).status == "cancelled"
    store.close()


def test_successful_assistant_is_persisted_once_and_partial_output_is_not() -> None:
    runtime = FakeRuntime()
    store = SQLiteConversationStore(":memory:")
    core = ConversationCore(runtime, scope_id="scope-success", store=store)

    run = core.start_turn("complete me")
    generation = runtime.generation()
    generation.emit(GenerationAccepted(generation.generation_id, generation.epoch))
    generation.emit(TextDelta(generation.generation_id, generation.epoch, "partial"))
    wait_until(lambda: run.text == "partial")
    generation.emit(TextDelta(generation.generation_id, generation.epoch, " final"))
    generation.emit(GenerationCompleted(generation.generation_id, generation.epoch))
    generation.emit(GenerationCompleted(generation.generation_id, generation.epoch))

    assert run.wait(2.0).status == "completed"
    messages = store.load_canonical_messages("scope-success")
    assert [(message.role, message.text) for message in messages] == [
        ("user", "complete me"),
        ("assistant", "partial final"),
    ]
    assert len(store.load_evidence("scope-success")) == 2
    assert messages[1].message_id == run.assistant_message_id

    cancelled = core.start_turn("cancel me")
    cancelled_generation = runtime.generation(1)
    cancelled_generation.emit(
        GenerationAccepted(cancelled_generation.generation_id, cancelled_generation.epoch)
    )
    cancelled_generation.emit(
        TextDelta(cancelled_generation.generation_id, cancelled_generation.epoch, "never")
    )
    wait_until(lambda: cancelled.text == "never")
    assert cancelled.cancel()
    assert cancelled.wait(2.0).status == "cancelled"
    assert [message.text for message in store.load_canonical_messages("scope-success")] == [
        "complete me",
        "partial final",
        "cancel me",
    ]


def test_failure_and_supersede_keep_users_but_never_persist_stale_assistant() -> None:
    runtime = FakeRuntime()
    store = SQLiteConversationStore(":memory:")
    core = ConversationCore(runtime, scope_id="scope-lifecycle", store=store)

    failed = core.start_turn("provider fails")
    failed_generation = runtime.generation()
    failed_generation.emit(
        GenerationFailedEvent(
            failed_generation.generation_id, failed_generation.epoch, "provider_error"
        )
    )
    assert failed.wait(2.0).status == "failed"

    old = core.start_turn("old turn")
    old_generation = runtime.generation(1)
    old_generation.emit(GenerationAccepted(old_generation.generation_id, old_generation.epoch))
    old_generation.emit(TextDelta(old_generation.generation_id, old_generation.epoch, "stale"))
    wait_until(lambda: old.text == "stale")
    current = core.start_turn("new turn")
    assert old.wait(2.0).status == "superseded"
    assert core.commit_assistant(old) == "superseded"
    current_generation = runtime.generation(2)
    complete_generation(current_generation, "fresh")
    assert current.wait(2.0).status == "completed"

    messages = store.load_canonical_messages("scope-lifecycle")
    assert [(message.role, message.text) for message in messages] == [
        ("user", "provider fails"),
        ("user", "old turn"),
        ("user", "new turn"),
        ("assistant", "fresh"),
    ]
    assert "stale" not in [message.text for message in messages]
    assert len(store.load_evidence("scope-lifecycle")) == len(messages)


def test_restart_reopens_exact_order_and_keeps_cancelled_output_transient(tmp_path: Path) -> None:
    database = tmp_path / "conversation.sqlite3"
    scope_id = "restart-scope"

    first_runtime = FakeRuntime()
    first_store = SQLiteConversationStore(database)
    first_core = ConversationCore(first_runtime, scope_id=scope_id, store=first_store)
    first_run = first_core.start_turn("hello durable")
    complete_generation(first_runtime.generation(), "hello back")
    assert first_run.wait(2.0).status == "completed"
    expected_history = first_core.history
    expected_canonical = first_core.canonical_history
    expected_evidence = first_core.evidence_records
    first_store.close()

    second_runtime = FakeRuntime()
    second_store = SQLiteConversationStore(database)
    second_core = ConversationCore(second_runtime, scope_id=scope_id, store=second_store)
    assert second_core.history == expected_history
    assert second_core.canonical_history == expected_canonical
    assert second_core.evidence_records == expected_evidence

    cancelled = second_core.start_turn("partial then cancel")
    cancelled_generation = second_runtime.generation()
    cancelled_generation.emit(
        GenerationAccepted(cancelled_generation.generation_id, cancelled_generation.epoch)
    )
    cancelled_generation.emit(
        TextDelta(cancelled_generation.generation_id, cancelled_generation.epoch, "partial")
    )
    wait_until(lambda: cancelled.text == "partial")
    cancelled.cancel()
    assert cancelled.wait(2.0).status == "cancelled"
    second_store.close()

    third_store = SQLiteConversationStore(database)
    third_core = ConversationCore(FakeRuntime(), scope_id=scope_id, store=third_store)
    assert third_core.history == expected_history + (ContextMessage("user", "partial then cancel"),)
    assert all(
        message.role != "assistant" or message.text != "partial" for message in third_core.history
    )
    assert len(third_core.evidence_records) == len(third_core.canonical_history)
    third_store.close()


def test_two_scopes_are_isolated_in_one_database(tmp_path: Path) -> None:
    store = SQLiteConversationStore(tmp_path / "scopes.sqlite3")
    first_runtime = FakeRuntime()
    second_runtime = FakeRuntime()
    first = ConversationCore(first_runtime, scope_id="first", store=store)
    second = ConversationCore(second_runtime, scope_id="second", store=store)

    first_run = first.start_turn("first user")
    complete_generation(first_runtime.generation(), "first assistant")
    second_run = second.start_turn("second user")
    complete_generation(second_runtime.generation(), "second assistant")
    assert first_run.wait(2.0).status == "completed"
    assert second_run.wait(2.0).status == "completed"

    assert first.history == (
        ContextMessage("user", "first user"),
        ContextMessage("assistant", "first assistant"),
    )
    assert second.history == (
        ContextMessage("user", "second user"),
        ContextMessage("assistant", "second assistant"),
    )
    assert [message.scope_id for message in store.load_canonical_messages("first")] == ["first"] * 2
    assert [message.scope_id for message in store.load_canonical_messages("second")] == [
        "second"
    ] * 2
    store.close()


def test_evidence_requires_canonical_source_and_correct_provenance() -> None:
    store = SQLiteConversationStore(":memory:")
    user = store.append_user_message(
        scope_id="evidence-scope",
        message_id="user-message",
        text="raw source",
        created_at="2026-09-06T00:00:00Z",
    )
    evidence = store.load_evidence("evidence-scope")[0]
    assert evidence.source_message_id == user.message_id
    assert evidence.source_role == user.role
    assert evidence.provenance_kind == "canonical_user"
    assert not hasattr(evidence, "text")

    with pytest.raises(PersistenceIntegrityError):
        store.append_evidence(
            evidence_id="missing-source-evidence",
            scope_id="evidence-scope",
            source_message_id="missing-source",
            provenance_kind="canonical_user",
            observed_at="2026-09-06T00:00:00Z",
        )
    with pytest.raises(PersistenceIntegrityError):
        store.append_evidence(
            evidence_id="wrong-role-evidence",
            scope_id="evidence-scope",
            source_message_id=user.message_id,
            provenance_kind="canonical_assistant",
            observed_at=user.created_at,
        )
    store.close()


def test_message_and_evidence_replay_is_idempotent_without_duplicates() -> None:
    store = SQLiteConversationStore(":memory:")
    user = store.append_user_message(
        scope_id="replay-scope",
        message_id="stable-user",
        text="same user",
        created_at="2026-09-06T00:00:00Z",
    )
    assert (
        store.append_user_message(
            scope_id="replay-scope",
            message_id="stable-user",
            text="same user",
            created_at="2026-09-06T01:00:00Z",
        )
        == user
    )
    assistant = store.append_assistant_message(
        scope_id="replay-scope",
        message_id="stable-assistant",
        text="same assistant",
        created_at="2026-09-06T00:00:01Z",
    )
    assert (
        store.append_assistant_message(
            scope_id="replay-scope",
            message_id="stable-assistant",
            text="same assistant",
            created_at="2026-09-06T02:00:00Z",
        )
        == assistant
    )
    assert len(store.load_canonical_messages("replay-scope")) == 2
    assert len(store.load_evidence("replay-scope")) == 2

    evidence = store.load_evidence("replay-scope")[0]
    assert (
        store.append_evidence(
            evidence_id=evidence.evidence_id,
            scope_id=evidence.scope_id,
            source_message_id=evidence.source_message_id,
            provenance_kind=evidence.provenance_kind,
            observed_at=evidence.observed_at,
            created_at="2026-09-06T03:00:00Z",
        )
        == evidence
    )
    with pytest.raises(PersistenceConflict):
        store.append_user_message(
            scope_id="replay-scope",
            message_id="stable-user",
            text="different text",
        )
    with pytest.raises(PersistenceConflict):
        store.append_evidence(
            evidence_id="another-evidence-id",
            scope_id=evidence.scope_id,
            source_message_id=evidence.source_message_id,
            provenance_kind=evidence.provenance_kind,
            observed_at=evidence.observed_at,
        )
    store.close()


def test_invalid_roles_references_and_scope_identifiers_are_rejected() -> None:
    with pytest.raises(ProtocolError):
        ContextMessage(cast(Any, "system"), "invalid role")

    store = SQLiteConversationStore(":memory:")
    with pytest.raises(PersistenceValidationError):
        store.append_evidence(
            evidence_id="invalid-provenance",
            scope_id="scope",
            source_message_id="missing",
            provenance_kind=cast(EvidenceProvenanceKind, "canonical_system"),
            observed_at="2026-09-06T00:00:00Z",
        )
    with pytest.raises(PersistenceValidationError):
        store.load_canonical_messages("")
    store.close()


def test_schema_version_corruption_and_open_failures_are_explicit(tmp_path: Path) -> None:
    database = tmp_path / "versioned.sqlite3"
    store = SQLiteConversationStore(database)
    assert store.schema_version == SCHEMA_VERSION
    assert store.journal_mode == "wal"
    store.close()

    connection = sqlite3.connect(database)
    connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
    connection.commit()
    connection.close()
    with pytest.raises(PersistenceSchemaError, match="unsupported SQLite schema version"):
        SQLiteConversationStore(database)

    corrupt = tmp_path / "corrupt.sqlite3"
    corrupt.write_bytes(b"not a SQLite database")
    with pytest.raises(PersistenceOpenError):
        SQLiteConversationStore(corrupt)


def test_user_write_failure_is_atomic_and_does_not_accept_a_false_turn() -> None:
    store = FailingEvidenceStore(":memory:")
    core = ConversationCore(FakeRuntime(), scope_id="atomic-scope", store=store)

    with pytest.raises(PersistenceWriteError, match="injected evidence"):
        core.start_turn("must not be half-written")
    assert core.history == ()
    assert store.load_canonical_messages("atomic-scope") == ()
    assert store.load_evidence("atomic-scope") == ()
    store.close()


def test_user_acceptance_and_assistant_completion_failures_are_typed() -> None:
    user_store = FailingUserStore(":memory:")
    user_core = ConversationCore(FakeRuntime(), scope_id="user-failure", store=user_store)
    with pytest.raises(PersistenceWriteError, match="injected user"):
        user_core.start_turn("not accepted")
    assert user_core.history == ()
    user_store.close()

    runtime = FakeRuntime()
    assistant_store = FailingAssistantStore(":memory:")
    assistant_core = ConversationCore(
        runtime,
        scope_id="assistant-failure",
        store=assistant_store,
    )
    run = assistant_core.start_turn("accepted despite later failure")
    complete_generation(runtime.generation(), "not canonical")
    outcome = run.wait(2.0)
    assert outcome.status == "failed"
    assert isinstance(outcome.error, PersistenceWriteError)
    assert assistant_core.history == (ContextMessage("user", "accepted despite later failure"),)
    assert assistant_store.load_evidence("assistant-failure")[0].provenance_kind == "canonical_user"
    assistant_evidence = assistant_core.runtime_evidence()
    assert [record.kind for record in assistant_evidence] == [
        "turn_accepted",
        "generation_bound",
        "generation_terminal",
        "run_terminal",
    ]
    assert assistant_evidence[-1].result == "failed"
    assert assistant_evidence[-1].reason == "persistence"
    assert not any(record.kind == "assistant_commit" for record in assistant_evidence)
    assistant_store.close()
