"""Structured logging setup."""

import logging
import sys


def configure_logging() -> None:
    """Configure structlog or stdlib logging; extend with JSON exporters in production."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        stream=sys.stdout,
    )

    # The Azure SDK's HTTP policy logs full request/response headers at INFO,
    # which buries the indexer's own events and inflates Container App Job log
    # volume (a --full run emits one dump per search/upload call). Warnings and
    # errors still surface.
    logging.getLogger("azure.core.pipeline.policies.http_logging_policy").setLevel(
        logging.WARNING
    )
    logging.getLogger("azure.identity").setLevel(logging.WARNING)
