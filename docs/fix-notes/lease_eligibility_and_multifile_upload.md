# Fix Note — Lease Eligibility Reasons + Multi-File Uploads

**Date:** 2026-07-22  
**Branch:** `doc_upload_fix`  
**Commit:** `b9bd469`  
**Tracked in:** [`docs/CHANGELOG.md`](../CHANGELOG.md) § v1.4.1

---

## Problem

1. **Lease eligibility collapsed** — When the platform returned an empty `leases[]` array with `reason=handover_exists` or `reason=activation_pending`, the agent treated every empty result as “lease not found”. Users with an existing handover could not see the SR link; users with a pre-activation lease got a misleading not-found message.
2. **Multi-file uploads raced** — Selecting multiple documents in the upload panel triggered one chat-refresh turn per file, causing races and inconsistent session state.

## Fix (code)

| Layer | Change |
|---|---|
| `lease_lookup_service.py` | Parse `reason` + `existing_handover_sr_id` from empty-match responses; mock fixtures for eval |
| `lease_lookup_node.py` | Map reasons → `existing_handover` / activation message / not_found; never create/reopen on `handover_exists` |
| `response_generation_node.py` | Skip LLM rewrite for `ui.type=existing_handover` (preserve SR # + deep link) |
| `DocumentUploadPanel.tsx` / `ServiceRequestChat.tsx` | Multi-file input + coalesced chat refresh |
| `ExistingHandoverCard.tsx` | Render existing handover deep-link card |
| `config.py` | `TENANT_PORTAL_BASE_URL` for absolute details URLs |

## Eval coverage (e2e tracking)

| Scenario | File | Tier | Tags | Pass criteria |
|---|---|---|---|---|
| **S63** | `tests/eval/scenarios.py` | 2 | `lease-eligibility`, `handover-exists` | `ui.type=existing_handover`, `srId=10473`, no “not found”, can continue with `t0105712` |
| **S64** | `tests/eval/scenarios.py` | 2 | `lease-eligibility`, `activation-pending` | Activation/start-date messaging, no “not found”, can continue with `t0105712` |
| **L173** | `tests/eval/lifecycle_scenarios.py` | 6 | `multi-file`, `document-upload` | FM uploads 3 doc types then approve → APPROVED |

### Mock lease fixtures (local eval)

Requires mock lease mode (`LEASE_LOOKUP_USE_MOCK=true` or unset `LEASE_TENANT_API_BASE_URL`):

| Lease code | `reason` | Extra |
|---|---|---|
| `t0028568` | `handover_exists` | `existing_handover_sr_id=10473` |
| `t0999999` | `activation_pending` | — |

### Run commands

```bash
cd service-request-chatbot

# Tier 2 eligibility scenarios
./run_agent_eval.sh --no-start --tier 2 --tags lease-eligibility

# Tier 6 multi-file FM upload
./run_agent_eval.sh --no-start --tier 6 --tags multi-file

# Or target by scenario id
cd backend && python -m tests.eval.run_eval --scenarios 63,64
cd backend && python -m tests.eval.run_lifecycle_eval --scenarios 173
```

## Unit tests (already on branch)

- `tests/unit/test_lease_lookup_node.py` — `TestZeroMatchEligibilityReasons`
- `tests/unit/test_lease_lookup_service.py` — HTTP parse + `TestMockLookupEligibilityReasons`

## Verification checklist

- [ ] S63 passes against local backend with mock leases
- [ ] S64 passes against local backend with mock leases
- [ ] L173 passes (FM multi-file → approve)
- [ ] Manual UI: blocked lease shows ExistingHandoverCard with working details link
- [ ] Manual UI: multi-file select uploads all files and chat stays coherent
