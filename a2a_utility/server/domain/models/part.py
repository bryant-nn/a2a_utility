"""Typed Part — the unit an executor callback returns.

adjust.md §1 "Data Contract": every Part carries `data: Any` plus a `metadata`
dict whose `dataType` is one of four kinds. The A2A server packs a `list[Part]`
into a single Artifact on the final Task response.

Kept free of any a2a-sdk types: the domain/application layers speak only in
these objects; adapters/inbound/dtos.py maps them onto a2a-sdk wire parts.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class DataType(str, Enum):
    TEXT = "text"                        # natural-language reply / analysis
    SOURCE_REFERENCE = "source_reference"  # DB / table / document / page etc.
    THINKING_PROCESS = "thinking_process"  # agent reasoning (Vercel AI SDK shape)
    FILE = "file"                        # produced file (CSV, image, ...)


@dataclass
class Part:
    data: Any
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # dataType is mandatory; default to text if a caller built Part(...) raw.
        self.metadata.setdefault("dataType", DataType.TEXT.value)

    @property
    def data_type(self) -> str:
        return self.metadata.get("dataType", DataType.TEXT.value)

    # --- factories, one per dataType -------------------------------------
    @classmethod
    def text(cls, data: Any, **metadata: Any) -> "Part":
        return cls(data=data, metadata={"dataType": DataType.TEXT.value, **metadata})

    @classmethod
    def source_reference(cls, data: Any, **metadata: Any) -> "Part":
        return cls(data=data, metadata={"dataType": DataType.SOURCE_REFERENCE.value, **metadata})

    @classmethod
    def thinking_process(cls, data: Any, **metadata: Any) -> "Part":
        return cls(data=data, metadata={"dataType": DataType.THINKING_PROCESS.value, **metadata})

    @classmethod
    def file(cls, data: Any, **metadata: Any) -> "Part":
        return cls(data=data, metadata={"dataType": DataType.FILE.value, **metadata})
