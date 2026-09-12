"""Minimal asynchronous local CLI for persistent Lilavel presence."""

from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import dataclass, field

from lilavel_core import ConversationCore, ModelRuntimeV3
from lilavel_core.production_cognition import build_turn_guidance
from prompt_toolkit import PromptSession, print_formatted_text
from prompt_toolkit.patch_stdout import patch_stdout

from .cognition_model import LocalCognitionEngine
from .contracts import ActionProposalKind
from .kernel import LilavelRuntime
from .mind import MindState
from .presence import (
    DEFAULT_IDLE_TIMEOUT_S,
    PRESENCE_SAY,
    PRESENCE_STAY_SILENT,
    FixedPresenceWakePolicy,
    PersistentPresenceRuntime,
    PresenceOutput,
    PresenceToolSessionFactory,
    WakeAfterIdleOpportunitiesPolicy,
)
from .proposal_application import ProposalApplicationCoordinator


@dataclass(slots=True)
class PromptToolkitOutputSink:
    """Bridge thread-based Core/tool output into one asyncio terminal writer."""

    loop: asyncio.AbstractEventLoop
    queue: asyncio.Queue[PresenceOutput | None] = field(
        default_factory=lambda: asyncio.Queue[PresenceOutput | None](maxsize=128)
    )

    def publish(self, output: PresenceOutput) -> None:
        asyncio.run_coroutine_threadsafe(self.queue.put(output), self.loop).result(5.0)

    async def render(self) -> None:
        while True:
            output = await self.queue.get()
            try:
                if output is None:
                    return
                if output.kind == "autonomous":
                    print_formatted_text(f"\nLilavel: {output.text}")
                elif output.kind == "conversation_delta":
                    print_formatted_text(output.text, end="")
                elif output.kind == "conversation_complete":
                    print_formatted_text("")
                elif output.kind == "conversation_interrupted":
                    print_formatted_text("\n[interrupted]")
                elif output.kind == "conversation_failed":
                    print_formatted_text("\n[generation failed]")
            finally:
                self.queue.task_done()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run Lilavel as a persistent local process.")
    parser.add_argument("--idle-seconds", type=float, default=DEFAULT_IDLE_TIMEOUT_S)
    parser.add_argument(
        "--wake-on-idle",
        action="store_true",
        help="deterministically admit one autonomous cognition after real idle",
    )
    parser.add_argument("--debug-presence", action="store_true")
    parser.add_argument(
        "--wake-after-opportunities",
        type=int,
        metavar="N",
        help="reject idle opportunities before deterministically waking on N",
    )
    return parser


async def run_cli(
    *,
    idle_seconds: float,
    wake_on_idle: bool,
    wake_after_opportunities: int | None,
    debug_presence: bool,
) -> int:
    loop = asyncio.get_running_loop()
    sink = PromptToolkitOutputSink(loop)
    tools = PresenceToolSessionFactory(sink)
    # ConversationCore and the actor-owned cognition engine share the physical
    # model lifecycle. Presence tools are application-only and are never
    # exposed to this model host.
    model = ModelRuntimeV3()
    mind_state = MindState()
    core = ConversationCore(
        model,
        trusted_guidance=lambda: build_turn_guidance(mind_state.projection().guidance_blocks()),
        scope_id="local-cli",
    )
    wake_policy = (
        WakeAfterIdleOpportunitiesPolicy(wake_after_opportunities)
        if wake_after_opportunities is not None
        else FixedPresenceWakePolicy(wake=wake_on_idle)
    )
    presence = PersistentPresenceRuntime(
        model,
        core,
        sink,
        mind_state=mind_state,
        wake_policy=wake_policy,
        idle_timeout_s=idle_seconds,
    )
    engine = LocalCognitionEngine(model, presence.history_for_cognition)
    application = ProposalApplicationCoordinator(
        mind_state,
        scope_id="runtime",
        state_provenance=presence.state_provenance_for,
        tool_registry=tools.registry,
        tool_session_factory=tools,
        action_tool_names={
            ActionProposalKind.SPEAK: PRESENCE_SAY,
            ActionProposalKind.STAY_SILENT: PRESENCE_STAY_SILENT,
        },
    )
    runtime = LilavelRuntime(
        presence=presence,
        cognition_engine=engine,
        mind_state=mind_state,
        proposal_application_coordinator=application,
    )
    renderer = asyncio.create_task(sink.render(), name="lilavel-cli-output")
    pending: set[asyncio.Task[str]] = set()
    session: PromptSession[str] = PromptSession()
    started = False
    try:
        await runtime.start()
        started = True
        print_formatted_text("Lilavel is present. Type /quit to exit.")
        with patch_stdout(raw=True):
            while True:
                try:
                    text = await session.prompt_async("you> ")
                except EOFError:
                    break
                if text.strip() in {"/quit", "/exit"}:
                    break
                if not text.strip():
                    continue
                task = asyncio.create_task(runtime.submit_user(text))
                pending.add(task)
                task.add_done_callback(pending.discard)
    except KeyboardInterrupt:
        pass
    finally:
        if started:
            await runtime.stop()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        if debug_presence:
            for record in presence.evidence():
                detail = "" if record.result is None else f" result={record.result}"
                print_formatted_text(f"presence[{record.sequence}] {record.kind}{detail}")
        sink.queue.put_nowait(None)
        await renderer
    return 0


def main() -> int:
    args = _parser().parse_args()
    if args.idle_seconds <= 0:
        print("--idle-seconds must be positive", file=sys.stderr)
        return 2
    if args.wake_after_opportunities is not None:
        if args.wake_after_opportunities <= 0:
            print("--wake-after-opportunities must be positive", file=sys.stderr)
            return 2
        if args.wake_on_idle:
            print("choose one deterministic wake option", file=sys.stderr)
            return 2
    try:
        return asyncio.run(
            run_cli(
                idle_seconds=args.idle_seconds,
                wake_on_idle=args.wake_on_idle,
                wake_after_opportunities=args.wake_after_opportunities,
                debug_presence=args.debug_presence,
            )
        )
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
