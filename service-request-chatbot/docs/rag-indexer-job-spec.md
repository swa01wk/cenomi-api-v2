# RAG Knowledge Indexer Job — Implementation Spec

**Status:** Proposed — for team implementation
**Target repo:** `cenomi-api-v2` (`service-request-chatbot/backend`)
**Source of truth:** `cenomi-ai-backend/indexer/` (existing production job)
**Consumes:** Azure AI Search index `cenomi-help-index`, read by
[`AzureSearchRepository`](../backend/app/integrations/search/azure_search.py)

---

## 1. Purpose

The chatbot's FAQ / help / general-inquiry answers are grounded in an Azure AI Search
index (`cenomi-help-index`) that stores chunked text **plus** a 1536-dim embedding
vector per chunk. This repo already contains the **read** side of that index
(`app/integrations/search/azure_search.py` + `app/integrations/embeddings/*`). It does
**not** contain the **write** side — the job that reads the Cenomi PostgreSQL
database, cleans/chunks the content, embeds it, and upserts it into the index.

That job currently lives in the legacy `cenomi-ai-backend` repo as a standalone
sync script (`indexer/`) run as an Azure Container App **Job**. This document
specifies:

1. **Part A** — a faithful port of the existing job into this repo's conventions
   (async SQLAlchemy, `structlog`, `app.core.config.settings`, uv/pyproject deps).
2. **Part B** — a proposed **incremental / delta indexing** mechanism using
   `updated_at` watermarks, so we stop doing a full delete-and-rebuild on every run.

Part A must be delivered first; Part B builds on it and is additive (a new CLI flag
plus one new table). Both are described in enough detail to be implemented without
reading the legacy repo.

---

## 2. What the existing job does (reference behaviour)

### 2.1 Shape

| Aspect | Current implementation |
| --- | --- |
| Entry point | `python main.py --full \| --source <name>` |
| Runtime | Python 3.11-slim container, `ENTRYPOINT ["python","main.py"]`, `CMD ["--full"]` |
| Deploy | Azure DevOps pipeline `pipelines/jobs-prod.yml`, tag trigger `release-prod-ai-jobs-*`, `az containerapp job update --image ...` |
| DB access | `psycopg2` **sync**, read-only credentials, `sslmode=require` |
| Embeddings | Azure OpenAI `text-embedding-3-small`, batches of 50, api-version `2024-02-01` |
| Chunking | `tiktoken` `cl100k_base`, 500 tokens max, 50 token overlap |
| HTML cleanup | BeautifulSoup — drop `<script>/<style>`, collapse whitespace |
| Index writes | `azure-search-documents` SDK, `upload_documents` (upsert) in batches of 1000 |
| Update strategy | **Full rebuild per source**: `delete_by_source(<source_type>)` then upsert everything |
| Config | `pydantic-settings`, flat env vars (`POSTGRES_*`, `AZURE_OPENAI_*`, `AZURE_SEARCH_*`) |

### 2.2 Index schema (`cenomi-help-index`)

Created idempotently by `ensure_index_exists()` — skipped if the index already exists.

| Field | Type | Attributes |
| --- | --- | --- |
| `id` | String | **key** |
| `source_type` | String | filterable |
| `source_id` | Int64 | filterable |
| `language` | String | filterable |
| `category_name_en` | String | searchable |
| `category_name_ar` | String | searchable, analyzer `ar.microsoft` |
| `url_pattern` | String | filterable, searchable |
| `title` | String | searchable |
| `content` | String | searchable |
| `content_vector` | Collection(Single) | searchable, **1536 dims**, profile `cenomi-vector-profile` |
| `mall_name` | String | filterable, searchable |
| `unique_property_id` | Int64 | filterable |
| `document_id` | String | filterable |
| `guide_id` | Int32 | filterable |
| `help_content_id` | Int32 | filterable |
| `announcement_event_id` | Int32 | filterable |
| `section` | String | filterable |
| `chunk_index` | Int32 | — |
| `page_number` | Int32 | — |

Vector search: `HnswAlgorithmConfiguration(name="hnsw-algo")` exposed through
`VectorSearchProfile(name="cenomi-vector-profile")`.

> **No semantic configuration is defined.** The read side deliberately omits
> `queryType=semantic` because adding it returns HTTP 400. If we ever add a semantic
> config, `azure_search.py` must be updated in the same change.

Every document is built from `_make_null_doc()` — a dict with **all** optional fields
set to `None` — so that an upsert of a changed document reliably clears fields that no
longer apply. Keep this pattern.

### 2.3 Data sources

Four sources are live. Two (`user_guide`, `download_center`) are commented out in the
legacy code pending PDF uploads to blob storage — port them as **disabled but present**
(see §3.6).

---

#### Source 1 — `help_content`

**Tables:** `cenomi.help_node_contents c` JOIN `cenomi.help_nodes n`, self-joined twice
(`p1` = parent, `p2` = grandparent) to resolve the PAGE ancestor of a
CATEGORY/SUBCATEGORY node.

```sql
SELECT
    c.help_content_id, c.help_node_id, n.node_type,
    c.title_en, c.title_ar, c.description_en, c.description_ar,
    CASE WHEN n.node_type = 'PAGE'        THEN n.name_en
         WHEN n.node_type = 'CATEGORY'    THEN p1.name_en
         WHEN n.node_type = 'SUBCATEGORY' THEN p2.name_en END AS page_name_en,
    CASE WHEN n.node_type = 'PAGE'        THEN n.name_ar
         WHEN n.node_type = 'CATEGORY'    THEN p1.name_ar
         WHEN n.node_type = 'SUBCATEGORY' THEN p2.name_ar END AS page_name_ar,
    CASE WHEN n.node_type = 'PAGE'        THEN n.url_pattern
         WHEN n.node_type = 'CATEGORY'    THEN p1.url_pattern
         WHEN n.node_type = 'SUBCATEGORY' THEN p2.url_pattern END AS url_pattern,
    CASE WHEN n.node_type = 'CATEGORY'    THEN n.name_en
         WHEN n.node_type = 'SUBCATEGORY' THEN p1.name_en END AS category_name_en,
    CASE WHEN n.node_type = 'CATEGORY'    THEN n.name_ar
         WHEN n.node_type = 'SUBCATEGORY' THEN p1.name_ar END AS category_name_ar,
    CASE WHEN n.node_type = 'SUBCATEGORY' THEN n.name_en END AS subcategory_name_en,
    CASE WHEN n.node_type = 'SUBCATEGORY' THEN n.name_ar END AS subcategory_name_ar
FROM cenomi.help_node_contents c
JOIN      cenomi.help_nodes n  ON n.help_node_id  = c.help_node_id
LEFT JOIN cenomi.help_nodes p1 ON p1.help_node_id = n.parent_id
LEFT JOIN cenomi.help_nodes p2 ON p2.help_node_id = p1.parent_id
WHERE n.is_visible = TRUE
ORDER BY c.help_node_id, c.sort_order NULLS LAST, c.help_content_id
```

**Per row, for each language in `("en", "ar")`:**

1. `text = clean_html(description_<lang>)`; skip if empty.
2. Build breadcrumb `"Page > Category > Subcategory"` from the non-empty
   `page_name_<lang>` / `category_name_<lang>` / `subcategory_name_<lang>` parts.
3. Indexed body = `f"{breadcrumb}\n{title}\n{text}"` (or `f"{title}\n{text}"` when there
   is no breadcrumb). **The breadcrumb prefix is deliberate** — it makes BM25 and vector
   search both match hierarchy-level queries like *"New Service Request > Workpermit"*.
4. `chunk_text(...)` → embed each chunk.

**Document fields:**

| Field | Value |
| --- | --- |
| `id` | `help_content_{help_content_id}_{lang}_{i}` |
| `source_type` | `"help_content"` |
| `source_id` | `help_content_id` |
| `language` | `lang` |
| `title` | `f"{breadcrumb} — {title}"` (or just `title`) |
| `content` | chunk text |
| `content_vector` | embedding |
| `category_name_en/ar` | `page_name_en` / `page_name_ar` (note: **page**, not category) |
| `url_pattern` | `url_pattern` |
| `help_content_id` | `help_content_id` |
| `chunk_index` | `i` |

---

#### Source 2 — `mall_info`

**Table:** `cenomi.unique_properties`, primary rows only.

```sql
SELECT
    up.unique_property_id, up.marketing_name, up.city, up.country,
    up.mall_information, up.mall_services, up.total_gross_leasable_area,
    TRIM((up.mall_information::jsonb) -> 'overview' ->> 'mall_manager') AS mall_manager
FROM cenomi.unique_properties up
WHERE up.is_primary = true
```

`mall_information` is a JSON blob flattened by `parse_mall_information()` into
`{"section": ..., "content": ...}` dicts. Port that parser **verbatim** — it encodes a
lot of hard-won data quirks:

- **Overview section** — `MallDescriptionEn` (fallback `MallDescriptionAr`), top-level
  `region`, then labelled fields pulled from `overview`:
  `total_area` → *Total Gross Leasable Area*, `floors`, `parking`, `facilities`,
  `mall_emergency_hotline` / `emergency_hotline` → *Emergency Hotline*,
  `fire_department`, `ambulance`, `police`, `mall_security`,
  `delivery_hours`, `delivery_location`, `location_description` → *Location*,
  `emergency_assembly_points` / legacy `assembly_points`.
  Values may be a scalar, a list (join with `", "`), or a dict (`{"default": v}` renders
  as the bare value; other keys render as `"k: v"`, joined with `"; "`).
- **`total_gross_leasable_area` from the column overrides `overview.total_area`** — the
  JSON value is stale. Formatted `f"{gla:,} sqm"`. If the whole JSON blob is missing but
  the column has a value, emit a minimal overview section with just the GLA line.
- **Timing is merged into the overview section, not its own section.** Rationale in the
  original code: a separate timing doc loses to other sections in top-K. Header is a
  keyword-stuffed line —
  `"Opening hours / operating hours / timings / working hours / schedule for {mall}:"` —
  followed by, in priority order: `operations.working_hours`, else the `MallTiming` array
  (`day_en`/`dayen`/`day_ar`/`dayar` + `start_time` + `end_time`), else
  `working_time`/`WorkingTime`.
- **Services section** — from the `mall_services` column merged into the JSON under key
  `mall_services` (only if the JSON does not already carry `mall_services`/`MallServices`).
  Dict form → `"{service_name}: {loc1, loc2}"` where each entry's location comes from
  `location` / `name` / `title` / `description` / `str(entry)`; empty entry list →
  `"{service_name}: available"`. List form → one `"Services: a, b, c"` line.
- **Social section** — `Social` or `social`, rendered as
  `"Social media links for {mall}:"` + `"{platform}: {url}"` lines.

**Per section:** `indexed_content = f"{mall_name}\n{section_text}"`. The mall-name prefix
is deliberate — it lifts BM25 scores for exact-name queries so *Nakheel Mall* is not
outranked by *Nakheel Mall Dammam* / *Nakheel Plaza*. **One embedding per section, no
chunking** (`embed_texts([indexed_content])`).

**Document fields:** `id = mall_info_{prop_id}_{section}`, `source_type="mall_info"`,
`source_id=prop_id`, `language="en"`, `title = f"{mall_name} — {section.title()}"`,
`mall_name`, `unique_property_id`, `section`, `chunk_index=0`.

---

#### Source 3 — `key_contact`

**One document per role group per mall** — searching *"mall manager nakheel"* must return
a focused card, not a 149-person blob.

For each `unique_property_id` where `is_primary = true`:

**(a) Mall Manager** — sourced from `mall_information -> overview -> mall_manager`, **not**
from `tenant_contacts` (the `up.mall_manager` column is empty for every mall). The JSON
value is one or more **comma-separated email addresses**. Split on `,`, trim, then resolve
each email individually via:

```sql
SELECT LOWER(email) AS email_lower,
       COALESCE(NULLIF(TRIM(CONCAT_WS(' ', first_name, last_name)), ''), '') AS full_name,
       COALESCE(phone, '') AS phone
FROM cenomi.users
WHERE LOWER(email) IN (...)
```

Resolve **per email** — do not dump the comma-joined string into one email slot; that
garbled multi-manager malls. Render `"- {name} | {email} | {phone}"` per manager under a
`"Mall Manager contacts for {mall_name}:"` header. Document id
`key_contact_{prop_id}_MALL_MANAGER`.

**(b) All other roles** — from `tenant_contacts`, matching the canonical
`FetchKeyContactsByPropertyID` logic:

```sql
SELECT DISTINCT ON (r.role_id, tc.tenant_contact_user_id)
    r.role_name,
    COALESCE(r.role_name_en, r.role_name, '') AS role_name_en,
    COALESCE(r.role_name_ar, '')              AS role_name_ar,
    COALESCE(u.first_name, '') AS first_name,
    COALESCE(u.last_name,  '') AS last_name,
    COALESCE(u.email,      '') AS email,
    COALESCE(u.phone,      '') AS phone
FROM cenomi.tenant_contacts tc
INNER JOIN cenomi.users u ON tc.tenant_contact_user_id = u.user_id
INNER JOIN cenomi.roles r ON tc.role_id = r.role_id
WHERE tc.property_id IN (
        SELECT p.property_id FROM cenomi.properties p
        WHERE p.unique_property_id = %s)
  AND tc.user_type = 'ADMIN'
  AND (tc.role_status = 'ACTIVE' OR tc.role_status IS NULL)
  AND r.role_name IN (:KEY_ROLES)
ORDER BY r.role_id, tc.tenant_contact_user_id, r.role_name, u.first_name, u.last_name
```

`KEY_ROLES` (excludes internal process roles like `AMENDMENT_ACTIVATION_EMAIL`, `WO_*`,
`LEASING_BOT`; **`MALL_MANAGER` is intentionally absent** — handled by (a)):

```
MALL_ADMIN, FM_MANAGER, MAINTENANCE_MANAGER, OPERATIONS_MANAGER, LEASING_MANAGER,
LEASING_ADMIN, KEY_ACCOUNT_MANAGER, TENANT_RELATION_OFFICER, MARKETING, FINANCE_MANAGER,
CUSTOMER_SERVICE_REPRESENTATIVE, CUSTOMER_RELATIONS_TEAM, AREA_MANAGER, REGIONAL_HEAD
```

Group by `role_name_en` (fallback `role_name`, fallback `"Staff"`). Content =
`"{role} contacts for {mall_name}:"` + `"- {name} | {email} | {phone}"` lines (email/phone
omitted when blank). One embedding per group, `chunk_index=0`,
`id = key_contact_{prop_id}_{ROLE_KEY}` where `ROLE_KEY = role_name.replace(" ","_").upper()`.

> **Known defect to fix during the port:** in the legacy loop, `role_key` is derived from
> the loop variable `c` leaking out of the inner `for c in members` loop. Derive it from
> the group key instead (`members[0]["role_name"]`) — same output in practice, but the
> current form is fragile and fails on an empty group.

`mall_name` falls back to `f"Property {prop_id}"` when `marketing_name` is absent.

---

#### Source 4 — `event`

```sql
SELECT ae.announcement_event_id, ae.title_en, ae.title_ar,
       ae.description_en, ae.description_ar,
       ae.start_date, ae.end_date, ae.is_event, ae.is_announcement,
       ae.applies_to_all_malls, up.marketing_name AS mall_name
FROM cenomi.announcements_events ae
LEFT JOIN cenomi.announcement_event_malls aem
       ON ae.announcement_event_id = aem.announcement_event_id
LEFT JOIN cenomi.unique_properties up
       ON aem.unique_property_id = up.unique_property_id
WHERE ae.deleted_at IS NULL
```

- `mall_name` falls back to `"All Malls"` when `applies_to_all_malls` is true, else `""`.
- `type_line` = `"Event"` / `"Announcement"` / `"Event, Announcement"` from the two
  booleans; defaults to `"Event"`.
- `date_line` = `"Date: {start} to {end}"`, or `"Date: {start}"`, or omitted.
- Per language: content = `"{type_line}: {title}"`, then `date_line`, then
  `"Mall: {mall_name}"`, then `clean_html(description_<lang>)`, newline-joined.
  **Skip the row if `title` is empty.** Then chunk + embed.
- `id = event_{event_id}_{lang}_{i}`, `announcement_event_id`, `mall_name`, `chunk_index=i`.

> Note the `LEFT JOIN` on `announcement_event_malls` **fans out** an event mapped to N
> malls into N rows, and each produces the same document `id` — last write wins, so only
> one mall name survives. This is a pre-existing bug. Recommendation: aggregate mall
> names per event (`string_agg`) and index one document per event. Flag before changing —
> it alters retrieved content.

---

### 2.4 Orchestration

```python
SOURCE_MAP = {"help": ..., "mall": ..., "contacts": ..., "events": ...}
```

`--full` (and today `--incremental`, an alias) runs every source in order. Each source
function ends with `delete_by_source(<type>)` then `upsert_documents(docs)`.

`delete_by_source` pages through `search(search_text="*", filter="source_type eq '<t>'",
select=["id"], top=1000)` and calls `delete_documents` until a page returns fewer than
1000 rows.

> **Availability gap in the current design:** the delete happens *after* all embeddings
> are computed but *before* the upload, so there is a window (seconds to a minute) where a
> source's documents are missing from the index and live chatbot queries return nothing
> for that source. Part B removes this window.

---

## 3. Part A — Port into `cenomi-api-v2`

### 3.1 Layout

```
service-request-chatbot/backend/
  app/
    indexer/
      __init__.py
      cli.py               # argparse entry point  (python -m app.indexer.cli --full)
      config.py            # IndexerSettings (extends/composes app.core.config)
      readers/
        __init__.py
        pg_reader.py       # async SQLAlchemy reads against the Cenomi DB
      transform/
        __init__.py
        html_cleaner.py
        chunker.py
        mall_information.py   # port of json_parser.parse_mall_information
      sources/
        __init__.py
        base.py            # IndexSource protocol
        help_content.py
        mall_info.py
        key_contact.py
        event.py
      index/
        __init__.py
        schema.py          # ensure_index_exists — field definitions
        writer.py          # upsert_documents / delete_documents / delete_by_source
      state.py             # Part B: watermark read/write
  Dockerfile.indexer
  tests/unit/indexer/
```

Reuse, do **not** duplicate:

- `app/integrations/embeddings/*` — `EmbeddingProvider` protocol already exists.
  Add a `embed_batch(texts: list[str]) -> list[list[float]] | None` method to the
  protocol and to both providers (the OpenAI SDK accepts a list for `input`; batch at 50).
  The single-text `embed()` becomes a one-element call to it.
- `app/core/config.py` — add the indexer settings there (see §3.3) rather than a second
  `Settings` class, so one `.env` drives API and job.
- `app/core/logging.py` / `structlog` — replace `logging.basicConfig` and all `logger.info`
  calls with structured events (`log.info("indexer.source_start", source="help")`).
  Keep the event-name-with-dots convention used across `app/`.

### 3.2 Async vs sync

The API is fully async (`asyncpg` + async SQLAlchemy). The job should match: use
`create_async_engine` against the Cenomi DB and drive `cli.py` with `asyncio.run(...)`.
This lets embedding calls (`AsyncOpenAI`) and Azure Search HTTP writes overlap with DB
reads, and lets us bound concurrency with a semaphore instead of serialising.

Use **raw SQL via `sqlalchemy.text()`** for every query above. Do not model the Cenomi
schema with ORM classes — this repo owns a different database and must not take a
migration dependency on `cenomi.*`.

### 3.3 Configuration

Add to `app/core/config.py::Settings` (all optional so the API starts without them):

| Env var | Default | Purpose |
| --- | --- | --- |
| `CENOMI_DATABASE_URL` | `None` | **Read-only** async DSN for the Cenomi DB, e.g. `postgresql+asyncpg://ro_user:...@host:5432/cenomi?ssl=require`. Distinct from `DATABASE_URL`, which is the chatbot's own DB. |
| `INDEXER_CHUNK_MAX_TOKENS` | `500` | |
| `INDEXER_CHUNK_OVERLAP` | `50` | |
| `INDEXER_EMBED_BATCH_SIZE` | `50` | |
| `INDEXER_UPLOAD_BATCH_SIZE` | `1000` | |
| `INDEXER_MAX_CONCURRENCY` | `4` | Concurrent embed requests |
| `INDEXER_DRY_RUN` | `false` | Build documents, log counts, write nothing |

Reuse the existing `AZURE_SEARCH_ENDPOINT` / `AZURE_SEARCH_API_KEY` /
`AZURE_SEARCH_INDEX_NAME` and `EMBEDDING_PROVIDER` / `AZURE_AI_*` / `OPENAI_API_KEY`
fields — **the job must embed with exactly the model the read path uses**
(`text-embedding-3-small`, 1536 dims). A mismatch silently degrades retrieval rather than
erroring.

The Azure Search **admin key** is required for writes; the API only needs a query key.
Give the job its own secret binding.

### 3.4 CLI

```
python -m app.indexer.cli --full                 # rebuild everything (current behaviour)
python -m app.indexer.cli --source help|mall|contacts|events
python -m app.indexer.cli --incremental          # Part B — delta only
python -m app.indexer.cli --incremental --source events
python -m app.indexer.cli --since 2026-08-01T00:00:00Z   # manual watermark override
python -m app.indexer.cli --full --dry-run
```

`--full` / `--incremental` / `--source` combine as: mode flag selects the strategy,
`--source` narrows which sources run (default: all). `--full` and `--incremental` remain
mutually exclusive. **Drop the current `--incremental`-is-an-alias-for-`--full`
behaviour** once Part B lands; until then keep it and log a warning.

Exit codes: `0` success, `1` unhandled error, `2` partial (some sources failed — job
should attempt every source and aggregate failures rather than aborting on the first).

### 3.5 Docker + pipeline

New `service-request-chatbot/backend/Dockerfile.indexer` — same two-stage build as the
API image, but `ENTRYPOINT ["python","-m","app.indexer.cli"]` / `CMD ["--incremental"]`,
no `EXPOSE`, no healthcheck, non-root `appuser`.

New `pipelines/sr-chatbot-indexer-prod.yml`, modelled on `pipelines/sr-chatbot-api-prod.yml`
and the legacy `jobs-prod.yml`:

- Tag trigger `release-prod-ai-v2-indexer-*`
- Variable group `v2-ai-prod-vars`, plus `indexer_repository` and `indexer_job_name`
- Build context `service-request-chatbot/backend`, `Dockerfile.indexer`, `--platform linux/amd64`
- Deploy via `az containerapp job update --name $(indexer_job_name) --image "$IMAGE"`
- Verify step asserting the deployed image tag matches, same as the API pipeline

Container App Job settings (infra, not code):

- Trigger type **Schedule**, cron `*/15 * * * *` for the incremental job.
- A second **Manual** job (same image) with args `["--full"]` for rebuilds.
- `replicaTimeout` 3600s, `parallelism` 1, `replicaRetryLimit` 1.
- **Never run full and incremental concurrently** — see §4.6.

### 3.6 Disabled sources

Port `blob_reader.py` (Azure Blob download by `document_id`) and `pdf_extractor.py`
(`pdfplumber`, 1-indexed page tuples, skip pages with no extractable text) plus the
`user_guide` and `download_center` source modules as **real code guarded by feature
flags** (`INDEXER_ENABLE_USER_GUIDES`, `INDEXER_ENABLE_DOWNLOAD_CENTER`, both default
`false`) rather than as commented-out blocks. Their document shapes are defined in the
legacy file and use `page_number`, `guide_id`, `document_id`, and `section`.

Queries:

```sql
-- user guides
SELECT ug.guide_id, ug.title_en, ug.title_ar, d.document_id, d.file_name
FROM cenomi.user_guides ug JOIN cenomi.documents d ON ug.document_id = d.document_id;

-- download centre
SELECT d.document_id, d.file_name, dt.document_type_id, dt.type_name,
       dt.type_name_arabic, dt.section AS section_code, dcs.section_name
FROM cenomi.documents d
JOIN cenomi.document_types dt ON d.document_type_id = dt.document_type_id
LEFT JOIN cenomi.download_center_sections dcs ON dt.section = dcs.section_code
WHERE dt.category = 'Download Center';
```

### 3.7 Dependencies to add

To `[project.optional-dependencies]` as a new `indexer` extra (keeps the API image lean):

```
tiktoken>=0.7.0
beautifulsoup4>=4.12.3
azure-search-documents>=11.6.0     # or keep raw httpx — see note
pdfplumber>=0.11.0                 # only if PDF sources are enabled
azure-storage-blob>=12.20.0        # only if PDF sources are enabled
```

> **Note on the Search SDK:** the read path in this repo deliberately uses raw `httpx`
> against the REST API (`api-version=2023-11-01`) instead of the SDK. Two options:
> (a) use `azure-search-documents` in the job only — fastest port, one more dependency; or
> (b) implement `upsert` / `delete` / `ensure_index_exists` over `httpx` for consistency
> with `azure_search.py`. **Recommendation: (a).** The SDK's batch result objects
> (`r.succeeded`) give per-document error reporting that we would otherwise reimplement,
> and index creation over raw REST is fiddly. Revisit if the extra dependency is a problem.

### 3.8 Tests

Unit tests (no network, no DB) in `tests/unit/indexer/`:

- `chunker` — under-limit passthrough, overlap correctness, empty/whitespace → `[]`.
- `html_cleaner` — script/style removal, entity handling, whitespace collapse, `None` → `""`.
- `mall_information` parser — table-driven over fixture JSON blobs covering: missing blob
  + GLA only, GLA column override, all three timing shapes, dict/list/scalar overview
  values, `mall_services` from column vs from JSON, social block.
- Document builders — feed fixture rows through each source with a stub embedder and
  assert exact `id`s, `source_type`, and the breadcrumb/mall-name prefixes.
- Watermark logic (Part B) — see §4.7.

Integration test behind a marker: `--dry-run --source mall` against a seeded DB, asserting
document counts and that nothing is written to Azure.

---

## 4. Part B — Incremental indexing

### 4.1 Problem

Every run deletes and rebuilds all ~thousands of documents for each source. That means:

- Full re-embedding of unchanged content on every run — the dominant cost and the reason
  the job can't run more often than a few times a day.
- A visible availability gap between `delete_by_source` and the upload (§2.4).
- Retrieval quality can't improve between runs, so newly published events/help content
  take up to a day to appear.

### 4.2 Approach

Drive each run from an `updated_at` **watermark** per source: read only rows changed since
the last successful run, re-index those, and remove documents whose source rows are gone.

Three parts: **watermark state**, **delta queries**, **deletion reconciliation**.

### 4.3 Watermark state

Store watermarks in the **chatbot's own** database (this repo already owns Alembic
migrations) — not in the read-only Cenomi DB.

New Alembic migration adding:

```sql
CREATE TABLE indexer_watermarks (
    source        TEXT PRIMARY KEY,          -- 'help_content' | 'mall_info' | 'key_contact' | 'event'
    last_run_at   TIMESTAMPTZ NOT NULL,      -- when the run started (see §4.6)
    watermark_at  TIMESTAMPTZ NOT NULL,      -- max(updated_at) successfully indexed
    last_status   TEXT NOT NULL,             -- 'success' | 'partial' | 'failed'
    docs_upserted INTEGER NOT NULL DEFAULT 0,
    docs_deleted  INTEGER NOT NULL DEFAULT 0,
    error         TEXT NULL,
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

Rules:

- The watermark is advanced **only after** the source's upserts all succeed. On failure,
  record `last_status='failed'` and leave `watermark_at` unchanged — the next run retries
  the same window. This makes the job **at-least-once**, which is safe because upserts are
  idempotent (same deterministic `id`).
- Use **the run's start time minus a safety lag** as the new watermark, not
  `max(updated_at)` of the rows read. A 5-minute `INDEXER_WATERMARK_LAG` absorbs clock
  skew between app servers and rows committed inside a transaction that started before,
  but committed after, the query's snapshot. Overlap re-indexes a few documents; a gap
  loses them permanently.
- No watermark row for a source → treat as a full build for that source and insert one.
- `--since <iso8601>` overrides the stored value for one run without persisting it unless
  the run succeeds.

### 4.4 Delta queries per source

Column availability (**verify against prod before implementing** — this is from
migration DDL in `cenomi-api-2`, not a live introspection):

| Table | `updated_at` | Notes |
| --- | --- | --- |
| `cenomi.help_nodes` | ✅ `TIMESTAMPTZ NOT NULL DEFAULT now()` | |
| `cenomi.help_node_contents` | ✅ `TIMESTAMPTZ NOT NULL DEFAULT now()` | |
| `cenomi.announcements_events` | ✅ `TIMESTAMP` (nullable) + `deleted_at` | |
| `cenomi.tenant_contacts` | ✅ `TIMESTAMP` (nullable) | |
| `cenomi.users` | ✅ `TIMESTAMP` (nullable) | |
| `cenomi.unique_properties` | ⚠️ **unverified** | DDL not found in the migration set |
| `cenomi.announcement_event_malls` | ⚠️ **unverified** | join table |

**Pre-implementation task (blocking):** run against prod
```sql
SELECT table_name, column_name FROM information_schema.columns
WHERE table_schema='cenomi'
  AND column_name IN ('updated_at','created_at','deleted_at')
  AND table_name IN ('help_nodes','help_node_contents','unique_properties',
                     'announcements_events','announcement_event_malls',
                     'tenant_contacts','users','roles','properties');
```
and confirm the columns are **actually maintained** — a nullable `updated_at` that the
writing API never sets is worse than no column, because it silently drops changes. Check
for update triggers, and spot-check `SELECT count(*) FROM t WHERE updated_at IS NULL`
and `max(updated_at)`.

Where `updated_at` is unmaintained, the fix is a trigger on the Cenomi DB:

```sql
CREATE OR REPLACE FUNCTION cenomi.set_updated_at() RETURNS trigger AS $$
BEGIN NEW.updated_at := now(); RETURN NEW; END $$ LANGUAGE plpgsql;

CREATE TRIGGER trg_<table>_updated_at BEFORE UPDATE ON cenomi.<table>
FOR EACH ROW EXECUTE FUNCTION cenomi.set_updated_at();
```

That is a change to the platform DB and belongs to the `cenomi-api-2` team — raise it as a
dependency, don't add it from this repo.

**Per-source delta predicates.** The key subtlety: a document's content depends on
**several** tables, so the predicate must be `OR`-ed across all of them and must trigger on
changes to *ancestors* and *joined* rows, not just the primary row.

*help_content* — content depends on the content row **and** its node **and** up to two
ancestor nodes (breadcrumb, `url_pattern`):

```sql
WHERE n.is_visible = TRUE
  AND (c.updated_at  > :since
    OR n.updated_at  > :since
    OR p1.updated_at > :since
    OR p2.updated_at > :since)
```

Also handle visibility flips: a node whose `is_visible` went TRUE→FALSE no longer matches
the query, so its documents must be removed by reconciliation (§4.5), not by the delta
query.

*mall_info* — depends on `unique_properties` only:
`WHERE up.is_primary = true AND up.updated_at > :since`.
Malls change rarely; if `updated_at` proves unreliable here, **keep `mall_info` on a full
rebuild** — it is the cheapest source (one embedding per section, a few hundred docs).

*key_contact* — depends on `tenant_contacts`, `users`, `roles`, `properties`, **and**
`unique_properties.mall_information` (mall-manager emails). Compute the set of affected
`unique_property_id`s first, then rebuild every role group for those malls:

```sql
SELECT DISTINCT p.unique_property_id
FROM cenomi.tenant_contacts tc
JOIN cenomi.properties p ON p.property_id = tc.property_id
JOIN cenomi.users u ON u.user_id = tc.tenant_contact_user_id
JOIN cenomi.roles r ON r.role_id = tc.role_id
WHERE tc.updated_at > :since OR u.updated_at > :since OR r.updated_at > :since
UNION
SELECT unique_property_id FROM cenomi.unique_properties
WHERE is_primary = true AND updated_at > :since
```

Rebuild at **mall granularity**, not contact granularity — a removed contact changes an
existing group document rather than deleting one, and a whole-mall rebuild handles both.
Delete all `key_contact` docs for the affected malls
(`filter=source_type eq 'key_contact' and unique_property_id eq <id>`) before upserting
the fresh set.

*event* —
`WHERE ae.updated_at > :since OR ae.deleted_at > :since` (include soft-deletes so they can
be removed), plus the mall-mapping join table if it carries a timestamp; otherwise treat a
mapping change as requiring a full event rebuild (cheap — run `--full --source events`
nightly).

**Rows deleted or newly hidden** are covered in §4.5.

### 4.5 Deletion reconciliation

Deltas can't see hard-deleted rows. Three complementary mechanisms:

1. **Soft deletes are visible.** `announcements_events.deleted_at` and
   `help_nodes.is_visible=false` are readable — query them explicitly each run and delete
   the matching documents by filter (`source_type eq 'event' and announcement_event_id eq N`).
2. **Scoped delete-before-upsert.** For any entity being re-indexed, delete its existing
   documents by a **narrow filter** before uploading the new set, so a row that shrank from
   4 chunks to 2 doesn't leave orphans at `chunk_index` 2–3. Filters:
   `help_content_id eq N and language eq 'x'`, `unique_property_id eq N`,
   `announcement_event_id eq N`. This replaces the whole-source delete and eliminates the
   availability window (§2.4) — each entity is dark for milliseconds, not the whole source.
3. **Weekly reconciliation sweep.** A `--reconcile` mode that enumerates all
   `(source_type, source_id)` pairs present in the index, compares against the live key set
   from the DB, and deletes index entries with no matching row. Run it weekly (or nightly
   for `event`, which is the highest-churn source). This is the safety net for hard deletes
   and for any missed `updated_at`.

Plus keep a **weekly `--full` rebuild** as the ultimate backstop. Incremental indexing
drifts; a scheduled full rebuild bounds the drift to one week.

### 4.6 Concurrency and safety

- Take a Postgres advisory lock (`pg_try_advisory_lock`) on the chatbot DB for the whole
  run, keyed per mode. If the lock is held, log and exit `0` — a schedule overrun must not
  produce two writers.
- **`--full` and `--incremental` must never overlap**: full's `delete_by_source` would
  remove documents the incremental run just wrote. Use the same advisory lock key for both
  modes so they serialise.
- Set `last_run_at` at run start so a stuck job is visible in the table.
- Cap per-run work (`INDEXER_MAX_DOCS_PER_RUN`, default e.g. 5000). If a delta exceeds it,
  process the oldest slice, advance the watermark only to the last fully-processed
  `updated_at`, and let the next run continue. Prevents one bulk DB import from blowing the
  job's timeout and embedding budget in a single replica.

### 4.7 Observability

Emit structured events matching this repo's convention:

```
indexer.run_start      mode, sources, since
indexer.source_start   source, since, candidate_rows
indexer.embed_batch    source, batch_size, ms
indexer.upsert_batch   source, batch, succeeded, failed
indexer.source_done    source, docs_upserted, docs_deleted, ms, new_watermark
indexer.run_done       mode, total_upserted, total_deleted, ms, status
indexer.error          source, error, stage
```

Log the **estimated embedding token count and call count** per run — that's the cost line
and the number that justifies the incremental work. Alert on: `status != 'success'`, a
run whose duration exceeds 30 min, and any source whose `watermark_at` is more than 24 h
behind `now()`.

### 4.8 Expected impact

| | Full rebuild (today) | Incremental (proposed) |
| --- | --- | --- |
| Embedding calls/run | every chunk, all sources | only changed entities — typically near zero |
| Feasible cadence | daily | every 15 min |
| Content freshness | up to 24 h | ~15 min |
| Availability gap | seconds–minutes per source | milliseconds per entity |
| Backstop | — | weekly `--full` + weekly `--reconcile` |

---

## 5. Delivery plan

| # | Deliverable | Depends on |
| --- | --- | --- |
| 0 | **Verify `updated_at` coverage on prod** (§4.4 query); raise trigger requests with the platform team | — |
| 1 | `app/indexer` skeleton: config, async PG reader, chunker, html cleaner, mall parser + unit tests | — |
| 2 | `index/schema.py` + `index/writer.py`; `embed_batch` added to the embedding providers | 1 |
| 3 | Four source modules with full-rebuild semantics; `--full` / `--source` CLI reaching parity with the legacy job | 1, 2 |
| 4 | `Dockerfile.indexer` + `pipelines/sr-chatbot-indexer-prod.yml` + Container App Job (manual trigger) | 3 |
| 5 | **Cutover:** run `--full` from the new job, diff document counts per `source_type` against the legacy job, decommission the old job | 4 |
| 6 | Watermark table + Alembic migration + `state.py` + advisory lock | 3 |
| 7 | Delta queries + scoped delete-before-upsert per source; `--incremental` | 6, 0 |
| 8 | `--reconcile` sweep; schedule 15-min incremental + weekly full + weekly reconcile | 7 |

**Acceptance for step 5:** for each `source_type`, document count in the new index build is
within ±1% of the legacy build, and a fixed set of ~20 sample queries (reuse
`docs/chatbot-test-queries.md`) returns the same top-3 titles.

**Acceptance for step 8:** editing one help content row in staging is reflected in a
chatbot answer within 20 minutes, with `docs_upserted` in the single digits for that run.

---

## 6. Open questions for the team

1. Does `cenomi.unique_properties` have a maintained `updated_at`? If not, is a trigger
   acceptable, or does `mall_info` stay on full rebuild?
2. Should the event fan-out bug (§2.3, source 4) be fixed as part of the port, or kept
   bug-compatible for a clean A/B diff at cutover? *Recommendation: keep bug-compatible for
   step 5, fix in a follow-up so the diff is attributable.*
3. Do we want the indexer in the API image (one image, two entrypoints) or a separate
   image? *Recommendation: separate — the API image should not carry `tiktoken`,
   `pdfplumber`, or the Search admin key.*
4. Who owns the Azure Search **admin** key rotation for the job's secret binding?
5. Is a 15-minute cadence acceptable to the platform DB owners given the read load of the
   delta queries, or should it be 30/60 minutes to start?
