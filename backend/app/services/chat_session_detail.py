"""Application-layer projection of a session state into HTTP DTOs.

The API layer owns transport, authentication, run status and error mapping.
Business display rules stay in the arcade domain, reached through the narrow
``ArcadeDisplaySource`` protocol declared here rather than the kernel's
``TurnPresenter`` port, so the kernel never learns the arcade display shape.
The immediate response is still built by the domain presenter from that same
projection; this service performs the HTTP DTO mapping for the session detail
and the archived ``map_artifacts`` payload.
"""

from __future__ import annotations

from typing import Protocol

from app.agent.runtime.session_state import AgentSessionState, AgentTurn
from app.protocol.messages import (
    ChatHistoryTurnDto,
    ChatRunDto,
    ChatSessionDetailDto,
    ChatSessionStatusType,
    ChatTextStepDto,
    ChatToolStepDto,
    ChatTurnMapArtifactsDto,
    ChatTurnStepDto,
    IntentType,
)
from app.domain.arcade.display import ArcadeDisplay
from app.services.arcade_payload_mapper import ArcadePayloadMapper
from app.services.chat_session_summary import visible_turns as _visible_turns


def normalize_intent(raw: str | None) -> IntentType:
    if raw == "navigate":
        return "navigate"
    if raw == "search_nearby":
        return "search_nearby"
    return "search"


class ArcadeDisplaySource(Protocol):
    def session_display(self, state: AgentSessionState) -> ArcadeDisplay:
        """Current display projection of ``state``."""


class ChatSessionDetailService:
    """Projects state and stored turns into ``ChatSessionDetailDto``."""

    def __init__(self, *, display_source: ArcadeDisplaySource, payload_mapper: ArcadePayloadMapper) -> None:
        self._display_source = display_source
        self._payload_mapper = payload_mapper

    def project(
        self,
        state: AgentSessionState,
        *,
        status: ChatSessionStatusType,
        current_run: ChatRunDto | None,
    ) -> ChatSessionDetailDto:
        visible = _visible_turns(state.turns)
        artifacts = self._map_display(self._display_source.session_display(state))
        intent = normalize_intent(state.intent)
        if artifacts.route is not None:
            intent = "navigate"
        return ChatSessionDetailDto(
            session_id=state.session_id,
            intent=intent,
            active_subagent=state.active_subagent,
            status=status,
            current_run=current_run,
            last_error=state.last_error,
            reply=state.working_memory.get("reply") if isinstance(state.working_memory.get("reply"), str) else None,
            shops=artifacts.shops,
            route=artifacts.route,
            client_location=artifacts.client_location,
            destination=artifacts.destination,
            view_payload=artifacts.view_payload,
            turn_count=len(visible),
            created_at=state.created_at,
            updated_at=state.updated_at,
            turns=self._history_turns(state, visible),
        )

    def _map_display(self, display: ArcadeDisplay) -> ChatTurnMapArtifactsDto:
        mapper = self._payload_mapper
        raw_shops = list(display.shops)[:20]
        return ChatTurnMapArtifactsDto(
            shops=mapper.summaries_from_rows(raw_shops),
            route=mapper.route_from_payload(display.route),
            client_location=mapper.client_location_from_payload(display.client_location),
            destination=(
                mapper.summary_from_row(display.destination)
                if isinstance(display.destination, dict)
                else None
            ),
            view_payload=display.view_payload if isinstance(display.view_payload, dict) else None,
        )

    def _turn_map_artifacts(self, turn: AgentTurn) -> ChatTurnMapArtifactsDto | None:
        raw = turn.payload.get("map_artifacts") if isinstance(turn.payload, dict) else None
        if not isinstance(raw, dict):
            return None
        shops = raw.get("shops")
        display = ArcadeDisplay(
            shops=tuple(item for item in shops if isinstance(item, dict)) if isinstance(shops, list) else (),
            route=raw.get("route") if isinstance(raw.get("route"), dict) else None,
            client_location=raw.get("client_location") if isinstance(raw.get("client_location"), dict) else None,
            destination=raw.get("destination") if isinstance(raw.get("destination"), dict) else None,
            view_payload=raw.get("view_payload") if isinstance(raw.get("view_payload"), dict) else None,
        )
        artifacts = self._map_display(display)
        if not (artifacts.shops or artifacts.destination or artifacts.route or artifacts.view_payload):
            return None
        return artifacts

    def _to_turn(self, turn: AgentTurn, *, steps: list[ChatTurnStepDto] | None = None) -> ChatHistoryTurnDto:
        return ChatHistoryTurnDto(
            role=turn.role,
            content=turn.content,
            name=turn.name,
            call_id=turn.call_id,
            map_artifacts=self._turn_map_artifacts(turn),
            steps=steps or [],
            created_at=turn.created_at,
        )

    def _history_turns(
        self,
        state: AgentSessionState,
        visible_turns: list[AgentTurn],
    ) -> list[ChatHistoryTurnDto]:
        rounds = iter(_round_steps(state.turns))
        return [
            self._to_turn(turn, steps=next(rounds, []) if turn.role == "user" else None)
            for turn in visible_turns
        ]


def _to_step(turn: AgentTurn) -> ChatTurnStepDto | None:
    """Project one stored turn into a UI step, or None when it is not part of the story.

    Only names, status and the main agent's own text leave the server; model
    evidence, tool arguments and tool results stay private.
    """
    if turn.role == "tool":
        if not turn.name:
            return None
        status = "completed" if turn.payload.get("status") == "completed" else "failed"
        return ChatToolStepDto(
            call_id=turn.call_id, name=turn.name, agent=turn.agent, status=status, created_at=turn.created_at
        )
    model = turn.payload.get("model")
    if turn.role == "assistant" and turn.scope == "conversation" and isinstance(model, dict):
        text = turn.content.strip()
        # A call without tool calls is the final reply, shown as the assistant turn itself.
        if text and model.get("tool_calls"):
            return ChatTextStepDto(agent=turn.agent, content=text, created_at=turn.created_at)
    return None


def _round_steps(turns: list[AgentTurn]) -> list[list[ChatTurnStepDto]]:
    """Steps of every round, one list per user turn in order."""
    rounds: list[list[ChatTurnStepDto]] = []
    for turn in turns:
        if turn.role == "user":
            rounds.append([])
            continue
        step = _to_step(turn)
        if step is not None and rounds:
            rounds[-1].append(step)
    return rounds
