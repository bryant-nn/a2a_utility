# a2a_wrapper/types.py
from __future__ import annotations

import json
import logging

from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, model_validator

if TYPE_CHECKING:
    from a2a.types import Part


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
    """Domain-layer Part, fully decoupled from protobuf.

    Content fields (mutually exclusive — mirrors protobuf oneof 'content'):
      - text, raw, url, data

    Common fields (may coexist with any content field):
      - metadata, filename, media_type
    """

    # oneof content
    text: str | None = None
    raw: bytes | None = None
    url: str | None = None
    data: CustomizedData | None = None

    # common fields
    metadata: dict[str, Any] | None = None
    filename: str | None = None
    media_type: str | None = None

    @model_validator(mode='after')
    def exactly_one_content(self) -> ExtendedPart:
        # Mirrors native Part's oneof "content": protobuf silently keeps
        # whichever field was assigned last instead of raising.
        count = sum(v is not None for v in (self.text, self.raw, self.url, self.data))
        if count != 1:
            raise ValueError(
                f'Exactly one content field required (text/raw/url/data), got {count}'
            )
        return self

    def to_protobuf(self) -> Part:
        """Convert to a native `a2a.types.Part`.

        Built via the SDK's own `a2a.helpers` constructors, not hand-rolled
        protobuf plumbing — `data`/`metadata`'s underlying protobuf types
        (`Value` vs `Struct`, respectively) are the helpers' problem, not
        this method's.

        Returns:
            A native Part with exactly one content field set, plus any
            common fields given.
        """
        from a2a.helpers import new_data_part, new_raw_part, new_text_part, new_url_part

        if self.text is not None:
            part = new_text_part(self.text, media_type=self.media_type)
        elif self.raw is not None:
            part = new_raw_part(self.raw, media_type=self.media_type, filename=self.filename)
        elif self.url is not None:
            part = new_url_part(self.url, media_type=self.media_type, filename=self.filename)
        elif self.data is not None:
            part = new_data_part(self.data.model_dump(mode='json'), media_type=self.media_type)
        else:
            raise ValueError('Unreachable: validated in model_validator')

        # new_raw_part/new_url_part already set filename; new_text_part/
        # new_data_part don't take one, so backfill without overwriting.
        if self.filename is not None and not part.filename:
            part.filename = self.filename
        if self.metadata:
            part.metadata.update(self.metadata)
        return part

    @classmethod
    def from_protobuf(cls, part: Part) -> ExtendedPart:
        """Parse a native `a2a.types.Part`. Never raises.

        Args:
            part: a native Part.

        Returns:
            The parsed ExtendedPart. Unrecognized `data` falls back to a
            JSON-dumped `text`; a Part with no oneof member set at all
            falls back to empty `text`.
        """
        from google.protobuf.json_format import MessageToDict

        # common fields
        kwargs: dict[str, Any] = {}
        if part.HasField('metadata'):
            kwargs['metadata'] = MessageToDict(part.metadata)
        if part.filename:
            kwargs['filename'] = part.filename
        if part.media_type:
            kwargs['media_type'] = part.media_type

        # oneof content
        match part.WhichOneof('content'):
            case 'text':
                kwargs['text'] = part.text
            case 'raw':
                kwargs['raw'] = part.raw
            case 'url':
                kwargs['url'] = part.url
            case 'data':
                raw = MessageToDict(part.data)
                if isinstance(raw, dict) and 'data_type' in raw and 'data_content' in raw:
                    try:
                        kwargs['data'] = CustomizedData.model_validate(raw)
                        return cls(**kwargs)
                    except Exception as e:
                        logger.warning('CustomizedData validation failed: %s', e)
                # Fallback: dump unrecognized data as JSON text
                kwargs['text'] = json.dumps(raw, ensure_ascii=False)
            case _:
                logger.warning('Empty Part received, defaulting to empty text')
                kwargs['text'] = ''

        return cls(**kwargs)


@dataclass
class DomainContext:
    """What a DomainAgentExecutorPort.execute() call receives.

    Attributes:
        is_resuming: True when this is a fresh execute() call resuming a
            task previously paused via InputRequired/AuthRequired (same
            task_id, not the same coroutine). Read `prior_parts` to pick
            up where it left off.
        prior_parts: what the task's paused status said (the
            InputRequired/AuthRequired prompt). Only meaningful when
            is_resuming is True; empty otherwise.
    """

    task_id: str
    context_id: str
    parts: list[ExtendedPart]
    message_id: str | None = None
    metadata: dict[Any, Any] | None = None
    configuration: dict[str, Any] = field(default_factory=dict)
    requested_extensions: list[str] = field(default_factory=list)
    is_resuming: bool = False
    prior_parts: list[ExtendedPart] = field(default_factory=list)

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
