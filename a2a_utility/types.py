"""Typed data contract for a2a_utility — Pydantic models that mirror the native
a2a protobuf messages and convert both ways.

Design (see the a2a Part proto: text/raw/url/data/metadata/filename/media_type,
where `data` is a google.protobuf.Value and `metadata` is a Struct):

  - Plain text  -> native Part.text
  - Files/blobs -> native Part.url OR Part.raw (+ filename + media_type)
  - Custom app-level structured payloads (thinking, source references, …) ->
    native Part.data, carrying a `CustomizedData` discriminated union.

`ExtendedPart.from_protobuf` / `to_protobuf` are the single conversion boundary
between a2a_utility's typed world and the a2a-sdk protobuf world.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, Optional, Union

from a2a.types import Artifact, Message, Part
from google.protobuf.json_format import MessageToDict, ParseDict
from google.protobuf.struct_pb2 import Struct, Value
from pydantic import BaseModel, Field, TypeAdapter


# --------------------------------------------------------------------------- #
# CustomizedData — the discriminated union carried inside Part.data            #
# Add new structured response types here; each needs a unique `data_type`.     #
# --------------------------------------------------------------------------- #
class ThinkingResponse(BaseModel):
    data_type: Literal["thinking_response"] = "thinking_response"
    text: str


class SourceReferenceResponse(BaseModel):
    data_type: Literal["source_reference_response"] = "source_reference_response"
    merged_reference: list = Field(default_factory=list)


CustomizedData = Annotated[
    Union[ThinkingResponse, SourceReferenceResponse],
    Field(discriminator="data_type"),
]

_CUSTOMIZED_DATA_ADAPTER: TypeAdapter[CustomizedData] = TypeAdapter(CustomizedData)


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

    # ---- ergonomic constructors ----
    @classmethod
    def from_text(cls, text: str, **metadata: Any) -> "ExtendedPart":
        return cls(text=text, metadata=metadata or None)

    @classmethod
    def thinking(cls, text_or_model: "str | ThinkingResponse", **metadata: Any) -> "ExtendedPart":
        payload = (
            text_or_model
            if isinstance(text_or_model, ThinkingResponse)
            else ThinkingResponse(text=text_or_model)
        )
        return cls(data=payload, metadata=metadata or None)

    @classmethod
    def source_reference(
        cls, refs_or_model: "list | SourceReferenceResponse", **metadata: Any
    ) -> "ExtendedPart":
        payload = (
            refs_or_model
            if isinstance(refs_or_model, SourceReferenceResponse)
            else SourceReferenceResponse(merged_reference=refs_or_model)
        )
        return cls(data=payload, metadata=metadata or None)

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
            data = _CUSTOMIZED_DATA_ADAPTER.validate_python(MessageToDict(proto_part.data))

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
        kwargs: dict[str, Any] = {}
        if self.text is not None:
            kwargs["text"] = self.text
        if self.raw is not None:
            kwargs["raw"] = self.raw
        if self.url is not None:
            kwargs["url"] = self.url
        if self.filename is not None:
            kwargs["filename"] = self.filename
        if self.media_type is not None:
            kwargs["media_type"] = self.media_type
        if self.data is not None:
            kwargs["data"] = ParseDict(self.data.model_dump(mode="json"), Value())
        if self.metadata:
            struct = Struct()
            struct.update(self.metadata)
            kwargs["metadata"] = struct
        return Part(**kwargs)


# --------------------------------------------------------------------------- #
# Artifact / Message / Task result wrappers                                    #
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


class A2ATaskResult(BaseModel):
    task_id: str
    status: str
    artifacts: list[ExtendedArtifact] = Field(default_factory=list)

    def parts(self) -> list[ExtendedPart]:
        return [p for a in self.artifacts for p in a.parts]

    def text(self) -> str:
        return "".join(p.text for p in self.parts() if p.text is not None)
