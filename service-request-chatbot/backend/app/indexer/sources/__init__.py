"""Source registry: SOURCE_MAP + enabled_sources()."""

from __future__ import annotations

from app.indexer.sources.download_center import DownloadCenterSource
from app.indexer.sources.event import EventSource
from app.indexer.sources.help_content import HelpContentSource
from app.indexer.sources.key_contact import KeyContactSource
from app.indexer.sources.mall_info import MallInfoSource
from app.indexer.sources.user_guide import UserGuideSource

# CLI alias → source instance.  Order = processing order.
SOURCE_MAP = {
    "help": HelpContentSource(),
    "mall": MallInfoSource(),
    "contacts": KeyContactSource(),
    "events": EventSource(),
    "guides": UserGuideSource(),
    "downloads": DownloadCenterSource(),
}


def enabled_sources(selected: list[str] | None = None):
    """Resolve CLI selection → list of enabled source instances.

    ``selected=None`` means every enabled source.  Explicitly selecting a
    disabled source is NOT an error — it logs ``indexer.source_disabled``
    at the call site and is skipped.
    """
    aliases = selected if selected else list(SOURCE_MAP.keys())
    return [SOURCE_MAP[a] for a in aliases if a in SOURCE_MAP]
