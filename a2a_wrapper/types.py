# a2a_wrapper/types.py
from __future__ import annotations

import json
import logging

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, model_validator


logger = logging.getLogger(__name__)


class DataType(StrEnum):
    VERCEL_THINKING = 'vercel_thinking'
    SOURCE_REFERENCE = 'source_reference'


class VercelThinkingContent(BaseModel):
    text: str


class SourceReferenceContent(BaseModel):
    merged_reference: list[str]


DATA_TYPE_SCHEMAS: dict[DataType, type[BaseModel]] = {
    DataType.VERCEL_THINKING: VercelThinkingContent,
    DataType.SOURCE_REFERENCE: SourceReferenceContent,
}


class CustomizedData(BaseModel):
    data_type: DataType
    data_content: dict[str, Any]

    @model_validator(mode='after')
    def validate_content_against_schema(self) -> CustomizedData:
        schema_cls = DATA_TYPE_SCHEMAS.get(self.data_type)
        if schema_cls is None:
            raise ValueError(f'Unregistered data_type: {self.data_type}')
        schema_cls.model_validate(self.data_content)
        return self


class ExtendedPart(BaseModel):
    """Domain-layer Part, fully decoupled from protobuf."""

    text: str | None = None
    data: CustomizedData | None = None

    @model_validator(mode='after')
    def exactly_one_field(self) -> ExtendedPart:
        # Mirrors native Part's oneof "content": protobuf silently keeps
        # whichever of text/data was assigned last instead of raising.
        if self.text is None and self.data is None:
            raise ValueError('ExtendedPart must have at least text or data')
        if self.text is not None and self.data is not None:
            raise ValueError('ExtendedPart may not set both text and data')
        return self

    def to_protobuf(self):
        """Convert to a native `a2a.types.Part`.

        Returns:
            A native Part with `text` or `data` set.
        """
        from a2a.types import Part
        from google.protobuf.struct_pb2 import Struct, Value

        if self.text is not None:
            return Part(text=self.text)

        if self.data is not None:
            struct = Struct()
            struct.update(self.data.model_dump())
            return Part(data=Value(struct_value=struct))

        raise ValueError('Unreachable: validated in model_validator')

    @classmethod
    def from_protobuf(cls, part) -> ExtendedPart:
        """Parse a native `a2a.types.Part`. Never raises.

        Args:
            part: a native Part.

        Returns:
            The parsed ExtendedPart, or a text fallback (JSON-dumped, or
            empty) if `part` holds unrecognized or no content.
        """
        from google.protobuf.json_format import MessageToDict

        if part.HasField('text'):
            return cls(text=part.text)

        if part.HasField('data'):
            raw = MessageToDict(part.data)
            if isinstance(raw, dict) and 'data_type' in raw and 'data_content' in raw:
                try:
                    return cls(data=CustomizedData.model_validate(raw))
                except Exception as e:
                    logger.warning('CustomizedData validation failed: %s', e)

            # Fallback: dump unrecognized data as JSON text
            return cls(text=json.dumps(raw, ensure_ascii=False))

        logger.warning('Empty Part received, defaulting to empty text')
        return cls(text='')


@dataclass
class DomainContext:
    task_id: str
    context_id: str
    parts: list[ExtendedPart]
    message_id: str | None = None
    metadata: dict[Any, Any] | None = None
    configuration: dict[str, Any] = field(default_factory=dict)
    requested_extensions: list[str] = field(default_factory=list)
    is_resuming: bool = False
    """True when this is a fresh execute() call resuming a task previously
    paused via InputRequired/AuthRequired (same task_id, not the same
    coroutine). Read `prior_parts` to pick up where it left off."""
    prior_parts: list[ExtendedPart] = field(default_factory=list)
    """What the task's paused status said (the InputRequired/AuthRequired
    prompt). Only meaningful when is_resuming is True; empty otherwise."""

    def get_text(self) -> str:
        """Concatenate all text parts.

        Returns:
            The joined text of every part that has one (empty string parts excluded).
        """
        return ''.join(part.text for part in self.parts if part.text)

    def get_data_parts(self, data_type: DataType | None = None) -> list[CustomizedData]:
        """Collect data parts.

        Args:
            data_type: if given, only return parts of this DataType.

        Returns:
            The matching CustomizedData values, in part order.
        """
        results = [p.data for p in self.parts if p.data is not None]
        if data_type:
            results = [d for d in results if d.data_type == data_type]
        return results
