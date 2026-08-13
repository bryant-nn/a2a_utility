"""Typed data contract for a2a_utility — Pydantic models that mirror the native
a2a protobuf messages and convert both ways.

Design (see the a2a Part proto: text/raw/url/data/metadata/filename/media_type,
where text/raw/url/data are members of ONE `oneof "content"` — protobuf allows
only one at a time — and `metadata` is a `google.protobuf.Struct` living
OUTSIDE that oneof, always addressable regardless of which content variant is
set):

  - Plain text  -> native Part.text
  - Files/blobs -> native Part.url OR Part.raw (+ filename + media_type)
  - Custom app-level structured payloads (thinking, source references, …) ->
    native Part.data, carrying a `CustomizedData` envelope

`ExtendedPart.from_protobuf` / `to_protobuf` are the single conversion boundary
between a2a_utility's typed world and the a2a-sdk protobuf world, built on top
of the SDK's own `a2a.helpers` constructors (`new_text_part`/`new_raw_part`/
`new_url_part`/`new_data_part`) rather than hand-rolled protobuf plumbing.

This module lives in `a2a_utility.schema` — deliberately a level above both
`a2a_utility.client` and `a2a_utility.server` — because both subpackages need
it and importing one shouldn't drag in the other's deps (see the client/server
`__init__.py` docstrings).

`PartEmitter` is the single streaming-callback type shared by both sides:
server-side handlers receive one to stream `ExtendedPart`s live (thinking,
source references, files — anything, not just text); the client's
`call_agent_result(..., emit=...)` receives the same shape on the consuming
end. Streaming a part uses the exact same vocabulary as the final return
value (`list[ExtendedPart]`) — one type describes both "here's a part now"
and "here are the final parts."
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Literal, Optional, Union

from a2a.helpers import new_data_part, new_raw_part, new_text_part, new_url_part
from a2a.types import Artifact, Message, Part
from google.protobuf.json_format import MessageToDict
from pydantic import BaseModel, Field, model_validator


# --------------------------------------------------------------------------- #
# CustomizedData — the envelope carried inside Part.data.                     #
# Shape follows the Vercel AI SDK data-part protocol: an outer `data_type`     #
# discriminator plus a nested `data_content` payload. Add new structured       #
# response types here; each needs a unique `data_type`.                       #
# --------------------------------------------------------------------------- #
class VercelThinkingResponse(BaseModel):
    data_type: Literal["thinking_response"] = "thinking_response"
    text: str


class SourceReferenceResponse(BaseModel):
    data_type: Literal["source_reference_response"] = "source_reference_response"
    merged_reference: list = Field(default_factory=list)


class CustomizedData(BaseModel):
    data_type: Literal["thinking_response", "source_reference_response"]
    data_content: Union[VercelThinkingResponse, SourceReferenceResponse] = Field(
        discriminator="data_type"
    )


# --------------------------------------------------------------------------- #
# ExtendedPart — typed mirror of the a2a Part proto                           #
# --------------------------------------------------------------------------- #
class ExtendedPart(BaseModel):
    text: Optional[str] = None
    raw: Optional[bytes] = None
    url: Optional[str] = None
    data: Optional[CustomizedData] = None
    metadata: Optional[dict] = None
    filename: Optional[str] = None
    media_type: Optional[str] = None

    @model_validator(mode="after")
    def _enforce_content_oneof(self) -> "ExtendedPart":
        # Mirrors the native Part proto's `oneof "content"` (text/raw/url/data):
        # protobuf itself won't error if you set more than one, it silently
        # keeps whichever was assigned last — so we enforce it here instead of
        # letting that footgun reach to_protobuf().
        set_fields = [f for f in (self.text, self.raw, self.url, self.data) if f is not None]
        if len(set_fields) > 1:
            raise ValueError(
                "ExtendedPart may set at most one of text/raw/url/data "
                "(they share the native Part's oneof 'content')"
            )
        return self

    # ---- ergonomic constructors ----
    @classmethod
    def from_text(cls, text: str, **metadata: Any) -> "ExtendedPart":
        return cls(text=text, metadata=metadata or None)

    @classmethod
    def thinking(cls, text_or_model: "str | VercelThinkingResponse", **metadata: Any) -> "ExtendedPart":
        inner = (
            text_or_model
            if isinstance(text_or_model, VercelThinkingResponse)
            else VercelThinkingResponse(text=text_or_model)
        )
        return cls(
            data=CustomizedData(data_type="thinking_response", data_content=inner),
            metadata=metadata or None,
        )

    @classmethod
    def source_reference(
        cls, refs_or_model: "list | SourceReferenceResponse", **metadata: Any
    ) -> "ExtendedPart":
        inner = (
            refs_or_model
            if isinstance(refs_or_model, SourceReferenceResponse)
            else SourceReferenceResponse(merged_reference=refs_or_model)
        )
        return cls(
            data=CustomizedData(data_type="source_reference_response", data_content=inner),
            metadata=metadata or None,
        )

    @classmethod
    def file(
        cls,
        *,
        url: Optional[str] = None,
        raw: Optional[bytes] = None,
        filename: Optional[str] = None,
        media_type: Optional[str] = None,
        **metadata: Any,
    ) -> "ExtendedPart":
        return cls(
            url=url, raw=raw, filename=filename, media_type=media_type,
            metadata=metadata or None,
        )

    # ---- conversion boundary ----
    @classmethod
    def from_protobuf(cls, proto_part: Part) -> "ExtendedPart":
        data = None
        if proto_part.HasField("data"):  # `data` is a message field inside oneof
            data = CustomizedData(**MessageToDict(proto_part.data))

        metadata = None
        if proto_part.HasField("metadata"):  # `metadata` is a Struct message field
            metadata = MessageToDict(proto_part.metadata)

        return cls(
            # text/raw/url are in the `content` oneof -> HasField is valid
            text=proto_part.text if proto_part.HasField("text") else None,
            raw=proto_part.raw if proto_part.HasField("raw") else None,
            url=proto_part.url if proto_part.HasField("url") else None,
            data=data,
            metadata=metadata,
            # filename/media_type are plain proto3 scalars (no presence) -> read directly.
            # HasField() on them RAISES; treat "" as absent.
            filename=proto_part.filename or None,
            media_type=proto_part.media_type or None,
        )

    def to_protobuf(self) -> Part:
        # Built via the SDK's own a2a.helpers constructors (not hand-rolled
        # ParseDict/Struct plumbing), then metadata is layered on top since
        # none of those constructors accept it.
        if self.text is not None:
            part = new_text_part(self.text, media_type=self.media_type)
        elif self.raw is not None:
            part = new_raw_part(self.raw, media_type=self.media_type, filename=self.filename)
        elif self.url is not None:
            part = new_url_part(self.url, media_type=self.media_type, filename=self.filename)
        elif self.data is not None:
            part = new_data_part(self.data.model_dump(mode="json"), media_type=self.media_type)
        else:
            part = Part()
            if self.media_type is not None:
                part.media_type = self.media_type

        if self.filename is not None and not part.filename:
            part.filename = self.filename
        if self.metadata:
            part.metadata.update(self.metadata)
        return part


# --------------------------------------------------------------------------- #
# Streaming callback contract                                                 #
# --------------------------------------------------------------------------- #
PartEmitter = Callable[["ExtendedPart"], Awaitable[None]]
"""Streams one ExtendedPart live. Shared by server-side handlers (AgentHandlerPort's
second argument) and the client's call_agent_result(..., emit=...)."""


def as_thinking_emitter(emit: PartEmitter) -> Callable[[str], Awaitable[None]]:
    """Adapts a PartEmitter into a plain str->None callback for business logic
    that only ever streams thinking text (nanobot's on_progress=, deepagents'
    emit_thought closures, etc.) and doesn't need the richer ExtendedPart shape."""

    async def emit_text(text: str) -> None:
        await emit(ExtendedPart.thinking(text))

    return emit_text


# --------------------------------------------------------------------------- #
# Artifact / Message wrappers (kept here since ExtendedArtifact/ExtendedMessage #
# are built directly out of ExtendedPart)                                     #
# --------------------------------------------------------------------------- #
class ExtendedArtifact(BaseModel):
    artifact_id: Optional[str] = None
    name: Optional[str] = None
    parts: list[ExtendedPart] = Field(default_factory=list)

    @classmethod
    def from_protobuf(cls, proto: Artifact) -> "ExtendedArtifact":
        return cls(
            artifact_id=proto.artifact_id or None,
            name=proto.name or None,
            parts=[ExtendedPart.from_protobuf(p) for p in proto.parts],
        )

    def to_a2a_parts(self) -> list[Part]:
        return [p.to_protobuf() for p in self.parts]


class ExtendedMessage(BaseModel):
    role: str
    parts: list[ExtendedPart] = Field(default_factory=list)

    @classmethod
    def from_protobuf(cls, proto: Message) -> "ExtendedMessage":
        from a2a.types import Role

        return cls(
            role=Role.Name(proto.role),
            parts=[ExtendedPart.from_protobuf(p) for p in proto.parts],
        )
