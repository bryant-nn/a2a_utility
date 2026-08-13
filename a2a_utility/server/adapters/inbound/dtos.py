"""Inbound wire DTOs: map domain Typed Parts onto a2a-sdk Artifact parts.

adjust.md §step-1 places PartDTO/ArtifactDTO here (the transport edge). The a2a
`Part` proto has a first-class `metadata` Struct that serializes on the wire, so
`dataType` rides along on every part:

  - dataType == "text"  -> a2a text part (so google-adk RemoteA2aAgent, which
    extracts the answer from *text* parts, still sees the real answer).
  - everything else      -> a2a data part (kept out of the text channel so it
    never pollutes the answer a text-only client reconstructs).
"""

from dataclasses import dataclass
from typing import Any

from a2a.helpers import new_data_part, new_text_part

from ...domain.models.part import DataType, Part


def _as_struct(data: Any) -> dict:
    # a2a data part's `data` field is a protobuf Struct (an object), so a bare
    # scalar/str must be wrapped before it can be set.
    return data if isinstance(data, dict) else {"value": data}


@dataclass
class PartDTO:
    data: Any
    metadata: dict[str, Any]

    @classmethod
    def from_domain(cls, part: Part) -> "PartDTO":
        return cls(data=part.data, metadata=dict(part.metadata))

    def to_a2a_part(self):
        data_type = self.metadata.get("dataType", DataType.TEXT.value)
        if data_type == DataType.TEXT.value:
            a2a_part = new_text_part(str(self.data))
        else:
            a2a_part = new_data_part(_as_struct(self.data))
        if self.metadata:
            a2a_part.metadata.update(self.metadata)
        return a2a_part


@dataclass
class ArtifactDTO:
    parts: list[PartDTO]

    @classmethod
    def from_domain(cls, parts: list[Part]) -> "ArtifactDTO":
        return cls(parts=[PartDTO.from_domain(p) for p in parts])

    def to_a2a_parts(self) -> list:
        return [dto.to_a2a_part() for dto in self.parts]
