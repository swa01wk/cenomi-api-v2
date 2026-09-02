"""Human-readable names for the identifiers the tracing tables record.

Nodes, tools, prompts and workflow stages are all stored as codes because
they are routing keys.  Anything a person reads should say what the step
actually does — "Answering from the knowledge base", not ``faq``.

Each map is exhaustive over the values seen in production, and every lookup
falls back to a title-cased form of the identifier, so a step added later
degrades to "Some New Node" rather than leaking ``some_new_node``.
"""

from __future__ import annotations

# Graph steps, named for what the user is waiting on rather than the module
# that implements it.
_NODE_LABELS: dict[str, str] = {
    "supervisor": "Understanding the request",
    "supervisor_llm_call": "Understanding the request (model)",
    "faq": "Answering from the knowledge base",
    "faq_llm_call": "Answering from the knowledge base (model)",
    "field_extraction": "Reading details from the message",
    "field_extraction_llm_call": "Reading details from the message (model)",
    "response_generation": "Writing the reply",
    "response_generation_llm_call": "Writing the reply (model)",
    "notification": "Opening the notification",
    "list_notifications": "Loading notifications",
    "open_notification": "Opening the notification",
    "lease_lookup": "Finding the lease",
    "lease_api_call": "Finding the lease (platform)",
    "validation": "Checking the details",
    "missing_field": "Asking for the next detail",
    "confirmation": "Showing the confirmation",
    "preview": "Building the preview",
    "merge_state": "Saving the answer",
    "load_session": "Loading the conversation",
    "save_state": "Saving the conversation",
    "registry": "Selecting the workflow",
    "sr_status_sync": "Checking the request status",
    "handover_entry": "Starting the handover",
    "fm_review_entry": "Starting the FM review",
    "rdd_review_entry": "Starting the RDD review",
    "fitout_confirm_entry": "Starting the fit-out confirmation",
    "payload_builder": "Preparing the submission",
    "fm_payload_builder": "Preparing the FM submission",
    "rdd_payload_builder": "Preparing the RDD submission",
    "fitout_confirm_payload_builder": "Preparing the fit-out submission",
    "api_submission": "Submitting the request",
    "fm_api_submission": "Submitting the FM review",
    "rdd_api_submission": "Submitting the RDD report",
    "fitout_confirm_api_submission": "Submitting the fit-out confirmation",
}

# Platform endpoints the agent calls.
_TOOL_LABELS: dict[str, str] = {
    "lease_tenant_api": "Lease lookup",
    "service_request_api.create_service_request": "Create service request",
    "service_request_api.rdd.submit_report": "Submit RDD report",
    "service_request_api.fm.patch_approve": "Approve FM review",
    "service_request_api.fm.patch_save_progress": "Save FM progress",
}

# Prompts, named for the job they do rather than the file they live in.
_PROMPT_LABELS: dict[str, str] = {
    "supervisor_prompt": "Understanding the request",
    "handover_extraction_prompt": "Reading details from the message",
    "faq_prompt": "Answering from the knowledge base",
    "response_generation_prompt": "Writing the reply",
}

# Workflow stages, as a person would describe where a request has got to.
_STAGE_LABELS: dict[str, str] = {
    "CREATE_SR": "Collecting details",
    "CREATE_HANDED_OVER_SR": "Collecting details (handed over)",
    "SR_CREATED": "Request created",
    "FM_REVIEW": "FM review",
    "RDD_REVIEW": "RDD review",
    "APPROVE_SR": "Awaiting approval",
    "SR_APPROVED": "Request approved",
    "SR_COMPLETED": "Request completed",
    "HANDED_OVER_COMPLETED": "Handover completed",
}


def _titleise(identifier: str) -> str:
    """Fall back for an unmapped identifier: ``some_new_node`` → "Some New Node".

    Dotted API names keep only their last segment, which is the part that
    carries meaning (``service_request_api.foo.bar`` → "Bar").
    """
    tail = identifier.rsplit(".", 1)[-1]
    return " ".join(part.capitalize() for part in tail.split("_") if part) or identifier


def node_label(name: str | None) -> str:
    """Human name for a graph step."""
    if not name:
        return "Unknown step"
    # Some runs are recorded under their tool name; prefer the tool's label.
    return _NODE_LABELS.get(name) or _TOOL_LABELS.get(name) or _titleise(name)


def tool_label(name: str | None) -> str:
    """Human name for a platform endpoint."""
    if not name:
        return "Unknown service"
    return _TOOL_LABELS.get(name) or _titleise(name)


def prompt_label(name: str | None) -> str:
    """Human name for a prompt."""
    if not name:
        return "Unnamed prompt"
    return _PROMPT_LABELS.get(name) or _titleise(name)


def stage_label(name: str | None) -> str:
    """Human name for a workflow stage."""
    if not name:
        return "Not started"
    return _STAGE_LABELS.get(name) or _titleise(name)
