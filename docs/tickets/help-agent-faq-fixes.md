# Help Agent — FAQ Answer Quality Fixes

| Field | Value |
|-------|-------|
| **Type** | Bug (4 defects) + hardening |
| **Component** | Help Agent · FAQ / RAG path (`service-request-chatbot/backend`) |
| **Branch** | `feature/faq-agent-audit` |
| **Commits** | `da28322`, `1b172d9`, `fee667b`, `26234a2` |
| **Related** | Ticket 3547 (RAG Knowledge Indexer) — separate deliverable, see `docs/specs/3547-rag-indexer-fable-build-spec.md` |
| **Status** | Implemented, tested, verified live on local |

---

## Summary

Four user-visible defects in the Help Agent's FAQ path were found and fixed:
the agent leaked internal data structures into chat, showed duplicated and
inconsistent source citations, exposed contacts outside the approved Key
Contact roles, and — most seriously — answered Key Contact questions with the
**wrong person** by substituting a different role.

All four are fixed and validated by a 15-case ground-truth suite run against
636 freshly indexed documents.

---

## Defect 1 — Raw Python dictionary shown to the user

**Severity:** High (user-visible data leak, looks broken)

### Problem
Asking *"give me all the details of mall of arabia"* returned a raw Python
`dict` repr in the chat bubble:

```
{'Mall Name': 'Mall Of Arabia', 'Region': 'Western', 'Total Gross Leasable
Area': '111,864 sqm', 'Floors': 4, 'Opening Hours': {'Weekdays': {'Hours':
'10:00 AM - 11:00 PM', ...
```

### Root cause
`faq_node` assumed `parsed["message"]` was always a string:

```python
answer = str(parsed["message"]).strip()
```

`LLMGateway.complete_json` runs the model in JSON mode. When the retrieved
knowledge is itself structured (a mall's details), the model sometimes answers
with a nested object instead of a string. `str()` on a dict produces its
Python repr — single quotes and all — which was shipped straight to the user.
The prompt asked for a key named `"message"` but never required its value to
be a **string**.

### Fix
- `FAQ_SYSTEM_PROMPT` now requires `"message"` to be a single STRING and
  instructs the model to render structured knowledge as readable markdown.
- `faq_node._render_structured()` added as a defensive fallback: any non-string
  value is converted to markdown (bold labels, bullet lists, scalar lists
  inlined) and logs `faq_node.structured_message_coerced`.

Both layers are intentional — the prompt reduces frequency, the code guarantees
a Python repr can never reach a user even when the model ignores instructions.

---

## Defect 2 — Duplicated, inconsistent source citations

**Severity:** Medium (UI noise, inconsistent counts)

### Problem
Every FAQ answer showed its sources **twice**: an inline
`[Source: …], [Source: …]` line appended to the message text, plus a row of
source chips below it. The two never agreed — the inline list was capped at 3,
the chips were uncapped.

### Root cause
`faq_node` both appended inline markers **and** returned `faq_sources`, which
fans out to the MSP `sources` array, the SSE `source` events, and
`/api/chat/service-request`'s `faq_sources` field.

### Fix
`faq_node` is the single producer, so returning `faq_sources: []` suppresses
every surface at once, and the inline marker block was removed.

**Retrieval is unaffected** — the RAG context block still grounds the answer;
only the citation display is gone. Retrieved titles are still logged
(`retrieved_count`) for RAG-quality debugging. Response fields remain present
but empty, so the MSP contract is unchanged and nothing 422s.

---

## Defect 3 — Contacts outside the approved Key Contact roles

**Severity:** High (policy / data exposure)

### Problem
The indexer indexed **14** contact roles, including `LEASING_MANAGER`. Only
seven roles are valid Key Contacts, and leasing/legal/collections contacts must
never be surfaced.

### Fix — allowlist enforced at index time
Non-allowlisted contacts are **never indexed**, so they cannot be retrieved,
summarised, or cited as a fallback. The seven valid roles:

| `role_name` | Display label |
|---|---|
| `MALL_MANAGER` | Mall Manager |
| `OPERATIONS` | Operations |
| `SALES_MANAGER` | Sales Manager |
| `DD_ENGINEER` | RDD Project Manager |
| `DD_ARCH` | RDD Architectural Engineer |
| `DD_MEP` | RDD MEP Engineer |
| `DD_ELEC_ENGINEER` | RDD Electrical Engineer |

The last four are collectively "the RDD team".

Implementation notes:
- Matching is on the `role_name` enum, **exact and case-sensitive**, by set
  membership — so `REGIONAL_DD_ENGINEER` is excluded despite the `DD_` prefix,
  with no special case needed.
- `LEASING_MANAGER` / `LEGAL` / `COLLECTOR` are additionally named in an
  explicit `HIDDEN_ROLES` set. Redundant against an allowlist, kept because
  silently leaking them is the expensive failure.
- **Display labels now come from code, not the database.** `roles.role_name_en`
  is localized and dirty — one value, `"RDD Electrical Engineer "`, carries a
  trailing space. The old code both *rendered* and *grouped* on that column, so
  the same role could have split into two separate contact cards. Grouping now
  keys on `role_name`; the label is looked up for presentation only.

### Side effect handled
`MALL_MANAGER` joining the allowlist created a document-id collision: the
existing JSON-blob path (`overview.mall_manager` emails) and `tenant_contacts`
`MALL_MANAGER` rows both produce `key_contact_{prop}_MALL_MANAGER`, so one would
have silently overwritten the other. Both populations are now **merged** into
one card and deduplicated by lowercased email.

---

## Defect 4 — Wrong Key Contact role returned

**Severity:** Critical (confidently wrong answer; user contacts the wrong person)

### Problem
> **Q:** Who should I contact for operations at Mall of Arabia?
> **A:** "you can contact the Mall Manager, **Badr Alharthy**"

The Operations card exists and lists four people. The agent answered with a
completely different role, with no indication anything was wrong.

### Root cause — two independent causes
**1. Retrieval: only ONE contact card could ever reach the model.**
`AzureSearchRepository._deduplicate` interleaved its first pass but then drained
groups one at a time. Because `help_content` and `mall_info` come first in
`_QUERY_CONFIG`, they consumed every remaining slot under `top_k=5`. The
Operations card ranked **#2** in its own sub-query and was still discarded — the
model only ever saw the Mall Manager card and answered from it.

**2. Prompt: nothing forbade substitution.** No instruction told the model that
a different role is not an acceptable answer.

### Fix
- **Retrieval:** the merge now interleaves all the way down (Nth-best of every
  source type before the (N+1)th of any), and `top_k` raised `5 → 8` so at
  least two results per source type fit.
- **Prompt:** `FAQ_SYSTEM_PROMPT` gained explicit Key Contact rules — answer
  only the role asked for; say no contact is listed rather than offering a
  different role or mall; never infer a role from a job title or email address;
  never mention non-allowlisted roles; enumerate all seven roles when asked for
  the RDD team or a mall's contacts generally.

Both were required. The prompt alone would have turned a wrong answer into
*"not listed"* — compliant, but still worse than naming the four real contacts.

---

## Supporting change — log noise

`azure-core`'s `http_logging_policy` logged full request/response headers at
INFO, once per search and upload call. A full indexer run buried its own events
under thousands of lines and would inflate Container App Job log volume and
cost. Capped at WARNING; failures still surface.

---

## Validation

A 15-case ground-truth suite was run against the **636 documents** built from a
full local index run, asserting answers against the actual indexed records.

**Result: 14/15 → 15/15** (Defect 4 was the failing case).

| Category | Cases | Result |
|---|---|---|
| Mall facts — GLA, opening hours, hotline, location, floors | 5 | Pass |
| Key contacts — Mall Manager, RDD Project Manager, Operations | 3 | Pass |
| No-substitution — absent RDD sub-roles | 2 | Pass |
| Hidden roles — leasing / legal / collections | 2 | Pass |
| Grounding — out-of-index mall, out-of-scope question | 2 | Pass |
| Platform knowledge — FM review documents | 1 | Pass |

Verified live over HTTP after restart:

> **Q:** Who should I contact for operations at Mall of Arabia?
> **A:** Operations Contacts for Mall Of Arabia — Muath AlGhamdi, Anas
> Alabdulkareem, Saad Aljadaani, Hani Ashour, …

Automated tests:
- `tests/unit/test_search_merge.py` — **7 new tests** pinning the interleaving
  and asserting `top_k` stays ≥ 2× the source-type count, so future tuning
  cannot silently reintroduce the crowding.
- `tests/unit/indexer/` — **107 tests**, including allowlist coverage for all
  seven roles, hidden-role exclusion, `REGIONAL_DD_ENGINEER`, case sensitivity,
  the trailing-space label, and the `MALL_MANAGER` merge.
- `tests/eval/indexer_eval.py` — 28/28 offline contract checks.
- Wider suites unchanged at their pre-existing baselines.

---

## Files changed

| File | Change |
|---|---|
| `app/agents/graph/nodes/faq/faq_node.py` | Citation suppression; `_render_structured()` coercion |
| `app/agents/prompts/faq_prompt.py` | String-only `message` contract; Key Contact rules |
| `app/integrations/search/azure_search.py` | Full interleaving in `_deduplicate`; `top_k` 5 → 8 |
| `app/indexer/config.py` | `KEY_CONTACT_ROLES`, `HIDDEN_ROLES`, `is_key_contact_role()` |
| `app/indexer/sources/key_contact.py` | Allowlist enforcement; label from code; MALL_MANAGER merge |
| `app/core/logging.py` | Azure SDK HTTP logging capped at WARNING |
| `tests/unit/test_search_merge.py` | New — 7 regression tests |
| `tests/unit/indexer/test_source_builders.py` | Allowlist test coverage |
| `tests/eval/indexer_eval.py` | Allowlist contract checks |

---

## Deployment notes

1. **A full re-index is required** for Defect 3. The allowlist only governs
   future indexing — contacts already indexed under the old 14 roles, including
   `LEASING_MANAGER`, remain retrievable until a rebuild. Trigger with
   `POST /api/v1/indexer/reindex {"source": "key_contact"}` (admin), or run the
   full-rebuild Container App Job.
2. **`azure_search.py` is the shared read path.** The `top_k` 5 → 8 change
   increases retrieved context for every FAQ query — slightly larger prompts and
   marginally higher per-turn token cost. Judged worth it to prevent wrong-role
   answers.
3. **No API contract change.** `sources` / `faq_sources` fields and the SSE
   `source` event type remain; they are simply always empty.
4. **No migration** required by these fixes. (Migration `006` belongs to
   ticket 3547.)

---

## Known limitations / follow-ups

- **RDD sub-roles are not answerable per-mall.** `DD_ARCH` (7 contacts),
  `DD_MEP` (7) and `DD_ELEC_ENGINEER` (4) exist and are active, but **every one
  has `property_id = NULL`**, so they attach to no mall and are correctly
  dropped by the `properties` join. Questions like *"who is the RDD
  Architectural Engineer at Mall of Arabia?"* therefore answer *"no key contact
  is listed"* — correct given the data. **Product decision needed:** should
  mall-unscoped RDD staff be surfaced globally?
- **`REGIONAL_DD_ENGINEER` has 0 contacts** in the current data, so its
  exclusion is covered by unit tests rather than live data.
- Citation suppression is currently global. If the internal
  `/api/chat/service-request` consumer should keep citations while the MSP
  platform does not, the suppression would move from `faq_node` to the MSP
  route.
