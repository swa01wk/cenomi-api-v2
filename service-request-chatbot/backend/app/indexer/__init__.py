"""RAG Knowledge Indexer job (ticket 3547).

Populates the Azure AI Search index (``cenomi-help-index``) that the chatbot's
``faq_node`` read path queries.  Runs as a separate Container App Job image
(``Dockerfile.indexer``) — the API process never imports this package at
runtime and the API image does not install the ``indexer`` dependency extra.

Entry point: ``python -m app.indexer.cli``.
"""
