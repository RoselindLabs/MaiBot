"""Conservative, private-only proposals for closed internal tool history.

These limits count internal Items, not real IM messages, tokens or RPC bytes.
192/96 give hysteresis at roughly 64/32 three-Item tool batches: above the small
successful sampled inputs, below an unbounded tool-heavy tail. They are not a
model-capacity guarantee and do not reinterpret max_private_context_size.
"""

from dataclasses import dataclass, fields
from typing import Sequence

from src.common.data_models.message_component_data_model import (
    AtComponent,
    EmojiComponent,
    FileComponent,
    ImageComponent,
    MessageSequence,
    ReplyComponent,
    TextComponent,
    VoiceComponent,
)
from src.llm_models.payload_content.context_item import (
    AssistantMessageItem,
    FunctionCallItem,
    ProviderActivityItem,
    ProviderOpaqueItem,
    ReasoningItem,
)
from src.llm_models.payload_content.context_protocol import ContextProtocolMode, validate_context_items

from .compaction import ContextSummaryMessage
from .history import collect_tool_turn_anchor_indices
from .messages import (
    ContentContextMessage,
    InternalContextMessage,
    LLMContextMessage,
    ModelOutputContextMessage,
    ReferenceMessage,
    SessionBackedMessage,
    ToolResultMediaMessage,
    ToolResultMessage,
)

INTERNAL_ITEM_TRIGGER = 192
INTERNAL_ITEM_TARGET = 96
MAX_STATE_GUARDS = 16384


@dataclass(frozen=True, slots=True)
class HistoryGuards:
    """Short-lived CAS read set; immutable strings/bytes are retained, not copied.

    Building the read set and all protocol analysis run in a worker. The final
    owner-loop check only compares field/list element identities, never encodes
    images, projects messages, hashes payloads or serializes metadata.
    """

    attributes: tuple[tuple[object, str, object], ...]
    lists: tuple[tuple[list, tuple], ...]

    def unchanged(self) -> bool:
        missing = object()
        return all(getattr(owner, name, missing) is value for owner, name, value in self.attributes) and all(
            len(current) == len(before) and all(a is b for a, b in zip(current, before, strict=True))
            for current, before in self.lists
        )


@dataclass(frozen=True, slots=True)
class InternalPressurePlan:
    status: str
    internal_count: int
    retained_internal_count: int
    removed_indices: frozenset[int] = frozenset()
    removed_batches: int = 0
    protected_batches: int = 0
    guards: HistoryGuards | None = None


def _is_internal(message: LLMContextMessage) -> bool:
    return isinstance(message, (ModelOutputContextMessage, ToolResultMessage, ToolResultMediaMessage))


def _guards(history: Sequence[LLMContextMessage]) -> HistoryGuards:
    attributes: list[tuple[object, str, object]] = []
    lists: list[tuple[list, tuple]] = []
    supported = {
        ModelOutputContextMessage, ToolResultMessage, ToolResultMediaMessage,
        SessionBackedMessage, ContentContextMessage, InternalContextMessage,
        ReferenceMessage, ContextSummaryMessage,
    }

    def attribute(owner: object, name: str) -> object:
        value = getattr(owner, name)
        attributes.append((owner, name, value))
        if len(attributes) > MAX_STATE_GUARDS:
            raise ValueError("history read set exceeds bounded CAS budget")
        return value

    def sequence(value: MessageSequence) -> None:
        components = attribute(value, "components")
        if not isinstance(components, list):
            raise ValueError("unsupported component sequence")
        lists.append((components, tuple(components)))
        for component in components:
            # Simple IM/media/quote components have only scalar fields. Nested
            # forward/opaque structures need a separate dependency/CAS contract.
            if type(component) not in {
                TextComponent, ImageComponent, EmojiComponent, VoiceComponent,
                FileComponent, AtComponent, ReplyComponent,
            }:
                raise ValueError("unknown or nested component type is protected")
            for name, field_value in vars(component).items():
                if not isinstance(field_value, (str, bytes, int, float, bool, type(None))):
                    raise ValueError("nested or opaque component state is protected")
                attribute(component, name)

    for message in history:
        if type(message) not in supported:
            raise ValueError("unknown context message ownership")
        for field in fields(message):
            attribute(message, field.name)
        if isinstance(message, ContentContextMessage):
            sequence(message.raw_message)
        if isinstance(message, SessionBackedMessage):
            original = message.original_message
            sequence(attribute(original, "raw_message"))
            attribute(original, "session_id")
            info = attribute(original, "message_info")
            user = attribute(info, "user_info")
            for name in ("user_id", "user_nickname", "user_cardname"):
                attribute(user, name)
    return HistoryGuards(tuple(attributes), tuple(lists))


def _protocol_valid(history: Sequence[LLMContextMessage], pending_call_ids: frozenset[str]) -> bool:
    items = [
        message.output_item if isinstance(message, ModelOutputContextMessage) else message.to_context_item()
        for message in history if isinstance(message, (ModelOutputContextMessage, ToolResultMessage))
    ]
    try:
        validate_context_items(items, ContextProtocolMode.HISTORY, pending_call_ids=pending_call_ids)
    except ValueError:
        return False
    return True


def plan_internal_history_pressure(
    history: Sequence[LLMContextMessage], *, pending_call_ids: frozenset[str],
    active_turn_id: str | None, trigger: int = INTERNAL_ITEM_TRIGGER, target: int = INTERNAL_ITEM_TARGET,
) -> InternalPressurePlan:
    """Pure worker-side selection; no normalization, deletion, projection or LLM.

    These three internal message classes each project to at most one Context Item;
    counting the wrapper is a conservative pressure count for empty media too.
    All actual IM messages, references and old summaries are retained by selection.
    """
    if not 0 <= target < trigger:
        raise ValueError("internal pressure target must be below trigger")
    count = sum(_is_internal(message) for message in history)
    if count < trigger:
        return InternalPressurePlan("no_pressure", count, count)
    if not _protocol_valid(history, pending_call_ids):
        return InternalPressurePlan("invalid_history", count, count)
    try:
        guards = _guards(history)
    except RuntimeError:
        # A component's dictionary may change while the worker enumerates its
        # scalar fields. Treat this as a concurrent read, never a deletion plan.
        return InternalPressurePlan("changed", count, count)
    except (ValueError, TypeError, AttributeError):
        return InternalPressurePlan("unsupported_state", count, count)
    if not guards.unchanged():
        return InternalPressurePlan("changed", count, count)

    groups: dict[str, list[int]] = {}
    calls: dict[str, set[str]] = {}
    owners: dict[str, str] = {}
    outputs: dict[str, str] = {}
    for index, message in enumerate(history):
        turn = None
        if isinstance(message, ModelOutputContextMessage):
            turn = message.output_item.meta.logical_turn_id
            if not turn:
                return InternalPressurePlan("unknown_turn", count, count)
            for call in message.tool_calls:
                calls.setdefault(turn, set()).add(call.call_id)
                owners[call.call_id] = turn
        elif isinstance(message, ToolResultMessage):
            turn = message.logical_turn_id
            outputs[message.tool_call_id] = turn
        if turn:
            groups.setdefault(turn, []).append(index)
    for index, message in enumerate(history):
        if isinstance(message, ToolResultMediaMessage):
            turn = owners.get(message.tool_call_id)
            if turn is None:
                return InternalPressurePlan("unowned_media", count, count)
            groups[turn].append(index)

    anchors = collect_tool_turn_anchor_indices(list(history), set(calls))
    anchor_indices = set(anchors.values())
    latest = max(calls, key=lambda turn: max(groups[turn]), default=None)
    candidates: list[tuple[str, list[int]]] = []
    protected = 0
    for turn in sorted(calls, key=lambda key: min(groups[key])):
        indices = sorted(groups[turn])
        blocked = (
            turn in {active_turn_id, latest} or turn not in anchors
            or any(call in pending_call_ids or outputs.get(call) != turn for call in calls[turn])
            or bool(set(indices) & anchor_indices)
            or indices != list(range(indices[0], indices[-1] + 1))
        )
        for index in indices:
            message = history[index]
            if isinstance(message, ModelOutputContextMessage):
                item = message.output_item
                if isinstance(item, (ProviderActivityItem, ProviderOpaqueItem)):
                    blocked = True
                if isinstance(item, (ReasoningItem, AssistantMessageItem, FunctionCallItem)) and item.replay is not None:
                    blocked = True
        if blocked:
            protected += 1
        else:
            candidates.append((turn, indices))

    available = sum(len(indices) for _, indices in candidates)
    if count - available > target:
        return InternalPressurePlan("unreclaimable", count, count, protected_batches=protected)
    selected: set[int] = set()
    selected_turns: set[str] = set()
    batches = 0
    for turn, indices in candidates:
        selected.update(indices)
        selected_turns.add(turn)
        batches += 1
        if count - len(selected) <= target:
            break
    retained = [message for index, message in enumerate(history) if index not in selected]
    # Replacing all old summaries with one new head summary cannot change the
    # user anchor of any retained tool turn. In particular, do not silently turn
    # an old summary, a media item or a missing anchor into a different anchor.
    retained_turns = set(calls) - selected_turns
    if any(turn not in anchors or isinstance(history[anchors[turn]], ContextSummaryMessage) for turn in retained_turns):
        return InternalPressurePlan("protected_anchor", count, count, protected_batches=protected)
    if not _protocol_valid(retained, pending_call_ids):
        return InternalPressurePlan("invalid_proposal", count, count)
    return InternalPressurePlan("ready", count, count - len(selected), frozenset(selected), batches, protected, guards)
