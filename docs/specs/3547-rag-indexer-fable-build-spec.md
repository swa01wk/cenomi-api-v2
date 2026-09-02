# Fable / Claude Build Spec — RAG Knowledge Indexer Job (Item 3547)

**Download this file and give it to Claude + Fable as the single source of truth.**

| Field | Value |
|-------|-------|
| **Ticket** | 3547 |
| **Repo** | `cenomi-api-v2` |
| **Implement in** | `service-request-chatbot/backend` |
| **Base branch** | `fix/staging` (cut `feature/3547-rag-indexer`) |
| **Do not touch** | Cenomi platform API repo — **no PR there** |
| **Index** | Azure AI Search `cenomi-help-index` (already queried by `faq_node`) |
| **Legacy reference** | `cenomi-ai-backend/indexer/` (production Container App Job) |

---

## 0. Prompt to paste into Fable / Claude

```
Implement ticket 3547 in cenomi-api-v2 exactly as specified in this document.

Repo: cenomi-api-v2
Workdir: service-request-chatbot/backend
Cut branch feature/3547-rag-indexer from fix/staging.

This repo already has the READ path for Azure AI Search (faq_node +
AzureSearchRepository + EmbeddingProvider registry). It does NOT have the
WRITE path (the indexer job). Port that job here.

Hard rules:
- Do all work in this repo. Do NOT change the Cenomi platform API repo.
- Do NOT copy Cenomi help/mall/event rows into the chatbot Postgres.
  Read Cenomi DB with raw SQL (sqlalchemy.text). Chatbot DB only stores
  indexer_watermarks.
- Do NOT concatenate records into opaque blobs like "12445saadnoman3feet12".
  Keep structured documents with breadcrumb / mall-name prefixes.
- Detect change from Postgres updated_at watermarks, NOT by reading vectors
  back from Azure Search. Use Search OData filters only to delete/replace
  documents for a known entity id.
- Embed with the SAME model the read path uses (EMBEDDING_MODEL /
  text-embedding-3-small, 1536 dims). Add embed_batch to the protocol AND
  every provider (openai, azure_openai, vertex_ai).
- Follow existing conventions: async SQLAlchemy + asyncpg, structlog events
  with dots (indexer.run_start), pydantic-settings on app.core.config.Settings,
  optional indexer extra in pyproject.toml so the API image stays lean.
- Alembic head today is revision 005 (005_rename_agents.py). Next watermark
  migration MUST be 006.
- Keep the event mall-join fan-out bug-compatible (last write wins).
- Fix the legacy key_contact role_key leak: derive ROLE_KEY from
  members[0]["role_name"], not a leaked inner-loop variable.
- Part A (--full) first, then Part B (--incremental / --reconcile) additive.
- Unit tests with no network/DB. Do not "fix" unrelated failing handover tests.

Deliver: app/indexer, embed_batch, Alembic 006, Dockerfile.indexer,
pipelines/sr-chatbot-indexer-prod.yml, tests/unit/indexer, tests/eval/indexer_eval.py,
and this spec remaining the contract.
```

---

## 1. Why this exists

The chatbot FAQ path (`faq_node`) runs hybrid BM25 + vector search against `cenomi-help-index`. Each indexed document is cleaned/chunked text plus a 1536-dim embedding.

**Today this repo only reads the index.** The job that fills the index still lives in legacy `cenomi-ai-backend` as a sync `psycopg2` script run as an Azure Container App Job. Every run deletes all documents for a `source_type` then re-embeds everything (costly, slow, availability gap of seconds–minutes per source).

This ticket ports that job here and adds incremental indexing.

### Current-repo facts (verify before coding)

- Python 3.11, FastAPI, async SQLAlchemy, `structlog`, Alembic.
- Embeddings: `EmbeddingProvider` protocol with `embed()` only. Registry in `app/integrations/embeddings/registry.py` with `openai`, `azure_openai`, `vertex_ai`. Providers use `from_settings()`.
- Search: raw `httpx` in `app/integrations/search/azure_search.py` (`api-version=2023-11-01`). **No semantic ranker** — index has no semantic config (HTTP 400 if added).
- Chatbot DB (`DATABASE_URL`) = sessions, drafts, users, traces. **Not** Cenomi help content.
- Alembic latest: `005_rename_agents.py` (`revision = "005"`, revises `"004"`).
- API Dockerfile: two-stage `python:3.11-slim`, `pip install .` (no extras), non-root `appuser`, `alembic upgrade head && uvicorn`.
- Prod pipeline: `pipelines/sr-chatbot-api-prod.yml`, tag `release-prod-ai-v2-api-*`, variable group `v2-ai-prod-vars`.

There is **no** `app/indexer` package to extend. Build it.

---

## 2. Goals / non-goals

### Goals

1. **Part A** — full rebuild parity with the legacy job: `--full` / `--source help|mall|contacts|events`.
2. **Part B** — incremental: watermarks, scoped delete-by-entity-id, `--reconcile`, advisory lock.
3. Same embedding model as `faq_node` (mismatch silently destroys retrieval).
4. Deterministic document ids so upserts are idempotent and cutover can A/B counts.

### Non-goals

| Item | Why |
|------|-----|
| Cenomi platform API code / migrations | Not required. Job is read-only against existing `cenomi.*` |
| Copying Cenomi tables into chatbot Postgres | Chatbot DB = watermarks only |
| Detecting diffs by fetching old vectors from Search | Expensive and wrong; use `updated_at` |
| Unstructured concatenated “customer” documents | This index is help/mall/contact/event, not PII |
| Enabling semantic search | Would 400 until a semantic config is added on the index **and** `azure_search.py` together |
| Shipping PDF sources enabled | Port as real code, default flags **false** |
| Fixing event mall fan-out | Keep bug-compatible for cutover |

---

## 3. Architecture

```
Cenomi Postgres (READ-ONLY)          Chatbot Postgres               Azure AI Search
cenomi.help_node_contents            indexer_watermarks             cenomi-help-index
cenomi.help_nodes                    pg_try_advisory_lock(3547,1)   hybrid BM25 + vector
cenomi.unique_properties
cenomi.tenant_contacts / users / roles
cenomi.announcements_events
        │                                     │                              ▲
        │ raw SQL via sqlalchemy.text()       │ watermark + lock             │
        ▼                                     ▼                              │
 python -m app.indexer.cli
        │  embed_batch (EmbeddingProvider — same model as faq_node)
        ├── --full          delete_by_source(source_type) then upsert
        ├── --incremental   delta SQL → scoped delete by id → upsert changed only
        └── --reconcile     index keys minus live DB keys → delete orphans
```

`faq_node` / API process stays read-only. Indexer is a **separate image** and Container App Job.

**Change detection vs apply:**

| Step | System | How |
|------|--------|-----|
| Find what changed | Cenomi Postgres | `updated_at` / `deleted_at` / `is_visible` |
| Replace those docs | Azure Search | OData filter on `help_content_id` / `unique_property_id` / `announcement_event_id` then upsert |

Do **not** round-trip Search to strcmp content.

---

## 4. File layout (create exactly)

```
service-request-chatbot/backend/
  app/indexer/
    __init__.py
    cli.py                 # argparse + asyncio.run  (python -m app.indexer.cli)
    config.py              # aliases, KEY_ROLES, require_* helpers
    documents.py           # make_null_doc / build_doc
    embedder.py            # batched embed_texts + token counts + semaphore
    state.py               # watermarks + advisory lock
    readers/
      __init__.py
      pg_reader.py         # CenomiPgReader, create_async_engine(CENOMI_DATABASE_URL)
      blob_reader.py       # download PDF by document_id (flagged sources)
    transform/
      __init__.py
      html_cleaner.py
      chunker.py           # tiktoken cl100k_base
      mall_information.py  # parse_mall_information — port quirks verbatim
      pdf_extractor.py     # pdfplumber, 1-indexed pages, skip empty
    sources/
      __init__.py          # SOURCE_MAP + enabled_sources()
      base.py              # IndexSource protocol
      help_content.py
      mall_info.py
      key_contact.py
      event.py
      user_guide.py        # disabled default
      download_center.py   # disabled default
    index/
      __init__.py
      schema.py            # ensure_index_exists
      writer.py            # upsert / delete / delete_by_source / delete_by_filter
                           # lazy-import azure-search-documents (API tests must not require it)
  Dockerfile.indexer
  alembic/versions/006_indexer_watermarks.py
  tests/unit/indexer/
  tests/eval/indexer_eval.py
  tests/integration/test_indexer_dry_run.py

pipelines/sr-chatbot-indexer-prod.yml
docs/specs/3547-rag-indexer-fable-build-spec.md   # this file
```

Also modify:

- `app/core/config.py` — optional indexer settings (API must still start without them)
- `app/integrations/embeddings/base.py` — add `embed_batch`
- `openai_embed.py`, `azure_openai_embed.py`, `vertex_ai_embed.py` — implement it; `embed()` becomes a 1-element batch
- `app/db/models.py` — `IndexerWatermark`
- `pyproject.toml` — `[project.optional-dependencies] indexer = [...]`; add `tiktoken` + `beautifulsoup4` (+ `pytest-cov`) to `dev` so unit tests run
- `.env.example` — commented indexer vars

---

## 5. Conventions

- Async everywhere: `asyncio.run` in CLI, `create_async_engine`, `AsyncOpenAI` / existing providers.
- Raw SQL only against Cenomi. **No ORM models for `cenomi.*`.**
- structlog: `log.info("indexer.source_start", source="help", candidate_rows=n)`
- `make_null_doc()` for every document so upserts clear stale optional fields.
- Indexer extra (not in API image):

```
tiktoken>=0.7.0
beautifulsoup4>=4.12.3
azure-search-documents>=11.6.0
pdfplumber>=0.11.0
azure-storage-blob>=12.20.0
```

- Search **writes** use the Azure SDK (option a). Read path stays raw httpx.
- Lazy-import the SDK inside writer/schema functions so `parse_args` unit tests work without the extra.

---

## 6. Configuration (`app.core.config.Settings`)

All optional / defaulted. `CENOMI_DATABASE_URL` is **not** `DATABASE_URL`.

| Env | Default | Notes |
|-----|---------|--------|
| `CENOMI_DATABASE_URL` | `None` | `postgresql+asyncpg://ro_user:...@host:5432/cenomi?ssl=require` |
| `INDEXER_CHUNK_MAX_TOKENS` | `500` | |
| `INDEXER_CHUNK_OVERLAP` | `50` | must be `< max_tokens` |
| `INDEXER_EMBED_BATCH_SIZE` | `50` | |
| `INDEXER_UPLOAD_BATCH_SIZE` | `1000` | |
| `INDEXER_MAX_CONCURRENCY` | `4` | embed batch semaphore |
| `INDEXER_DRY_RUN` | `false` | build + log; no Search writes; no watermark advance |
| `INDEXER_ENABLE_USER_GUIDES` | `false` | |
| `INDEXER_ENABLE_DOWNLOAD_CENTER` | `false` | |
| `INDEXER_WATERMARK_LAG` | `300` | **seconds** subtracted from run start |
| `INDEXER_MAX_DOCS_PER_RUN` | `5000` | incremental row cap |
| `INDEXER_MALL_INFO_FULL_ON_INCREMENTAL` | `false` | force mall full rebuild if `updated_at` unmaintained |
| `INDEXER_BLOB_CONNECTION_STRING` | `None` | PDF sources |
| `INDEXER_BLOB_CONTAINER` | `None` | blob name = `str(document_id)` |

Reuse existing: `AZURE_SEARCH_ENDPOINT`, `AZURE_SEARCH_API_KEY` (job needs **admin** key), `AZURE_SEARCH_INDEX_NAME`, `EMBEDDING_PROVIDER`, `EMBEDDING_MODEL`, `OPENAI_API_KEY`, `AZURE_AI_*`, `VERTEX_AI_*`.

---

## 7. `embed_batch`

Protocol:

```python
async def embed_batch(self, texts: list[str]) -> list[list[float]] | None:
    """Preserve input order. Empty list → []. Failure → None (not partial)."""
```

`embed(text)` = `embed_batch([text])` then `[0]`.

Indexer helper `embed_texts(provider, texts, source=...)` splits into batches of 50, runs up to 4 concurrent calls, preserves order, logs `indexer.embed_batch` (batch_size, ms) and `indexer.embed_done` (texts, tokens via tiktoken `cl100k_base`, calls). If any batch returns `None` or wrong length, raise.

Vertex AI: batch the provider’s native list input; same contract.

The job **must** use `get_embedding_provider()` so it embeds with whatever `EMBEDDING_PROVIDER` the API uses.

---

## 8. Index schema (`ensure_index_exists`)

Skip create if index name already exists. **No semantic configuration.**

| Field | Type | Attributes |
|-------|------|------------|
| `id` | String | key |
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
| `chunk_index` | Int32 | |
| `page_number` | Int32 | |

Vector: `HnswAlgorithmConfiguration(name="hnsw-algo")` + `VectorSearchProfile(name="cenomi-vector-profile", algorithm_configuration_name="hnsw-algo")`.

`make_null_doc()` keys = all fields above, values `None`. `build_doc(**fields)` copies onto that dict; unknown keys raise.

---

## 9. Transforms

### 9.1 `clean_html(html: str | None) -> str`

- `None` / `""` → `""`
- BeautifulSoup `html.parser`; decompose `script` and `style`
- `get_text(separator=" ")`; collapse whitespace `re.sub(r"\s+", " ", ...)`.strip()

### 9.2 `chunk_text(text, max_tokens=500, overlap=50) -> list[str]`

- tiktoken `cl100k_base`
- empty / whitespace → `[]`
- under limit → `[text]` passthrough (original string, not re-decoded)
- otherwise sliding window; `overlap < max_tokens`
- `count_tokens(text)` for cost logs

### 9.3 `parse_mall_information(mall_information, mall_name, *, total_gross_leasable_area=None, mall_services=None) -> list[{"section","content"}]`

Port quirks **verbatim**:

**Value formatting `_fmt_value`:**

- `None` → `""`
- list → join with `", "`
- dict: if key `"default"` exists, render the default as a bare value; other keys as `"k: v"` joined with `"; "` (so `{"default": "Food court", "extra": "Cinema"}` → `Food court; extra: Cinema`). If only `default`, just the bare value.
- scalar → `str`

**GLA:** column `total_gross_leasable_area` **overrides** `overview.total_area` (JSON is stale). Format `f"{int:,} sqm"` (integer thousands). If the JSON blob is missing/empty but GLA column has a value, still emit an overview section with just that line.

**Overview section** (merge timing into this section — do **not** emit a separate timing section; a separate timing doc loses top-K):

1. `MallDescriptionEn` else `MallDescriptionAr` (top-level or inside `overview`)
2. top-level `region` → `Region: …`
3. GLA line as above, else JSON `overview.total_area`
4. Labelled overview fields (first present key wins):

| JSON keys | Label |
|-----------|-------|
| floors | Floors |
| parking | Parking |
| facilities | Facilities |
| mall_emergency_hotline, emergency_hotline | Emergency Hotline |
| fire_department | Fire Department |
| ambulance | Ambulance |
| police | Police |
| mall_security | Mall Security |
| delivery_hours | Delivery Hours |
| delivery_location | Delivery Location |
| location_description | Location |
| emergency_assembly_points, assembly_points | Emergency Assembly Points |

5. **Timing** appended to overview. Header exactly:

   `Opening hours / operating hours / timings / working hours / schedule for {mall}:`

   Priority: `operations.working_hours` else `MallTiming` array (`day_en`/`dayen`/`day_ar`/`dayar` + `start_time` + `end_time` → `"{day}: {start} - {end}"`) else `working_time` / `WorkingTime`.

**Services:** if JSON lacks `mall_services`/`MallServices`, merge the `mall_services` **column**. Dict form: `"{service_name}: {loc1, loc2}"` where each entry location is `location` else `name` else `title` else `description` else `str(entry)`; empty list → `"{service_name}: available"`. List form: one line `Services: a, b, c`.

**Social:** `Social` or `social` → header `Social media links for {mall}:` then `{platform}: {url}` lines.

Accept JSON as dict, JSON string, or `None`.

---

## 10. Sources

CLI alias → `source_type`:

| CLI | `source_type` | Default enabled |
|-----|---------------|-----------------|
| `help` | `help_content` | yes |
| `mall` | `mall_info` | yes |
| `contacts` | `key_contact` | yes |
| `events` | `event` | yes |
| `guides` | `user_guide` | flag |
| `downloads` | `download_center` | flag |

Each source implements:

```python
name: str
source_type: str
enabled: bool | property
async def fetch_rows(reader, *, since: datetime | None) -> list[dict]  # since=None ⇒ full
async def fetch_deleted_ids(reader, *, since) -> list[int]
async def fetch_live_ids(reader) -> list[int]   # for --reconcile
async def build_documents(rows, provider) -> list[dict]
def scoped_filters(docs) -> list[str]           # OData, one per entity in docs
def id_filter(entity_id: int) -> str
```

Export pure builders (`pending_chunks_from_row`, etc.) so unit tests never need DB.

### 10.1 `help_content`

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
    CASE WHEN n.node_type = 'SUBCATEGORY' THEN n.name_ar END AS subcategory_name_ar,
    GREATEST(
        c.updated_at, n.updated_at,
        COALESCE(p1.updated_at, '-infinity'::timestamptz),
        COALESCE(p2.updated_at, '-infinity'::timestamptz)
    ) AS row_updated_at
FROM cenomi.help_node_contents c
JOIN      cenomi.help_nodes n  ON n.help_node_id  = c.help_node_id
LEFT JOIN cenomi.help_nodes p1 ON p1.help_node_id = n.parent_id
LEFT JOIN cenomi.help_nodes p2 ON p2.help_node_id = p1.parent_id
WHERE n.is_visible = TRUE
-- incremental add:
--   AND (c.updated_at > :since OR n.updated_at > :since
--        OR p1.updated_at > :since OR p2.updated_at > :since)
ORDER BY c.help_node_id, c.sort_order NULLS LAST, c.help_content_id
```

Per lang in `("en","ar")`: `text = clean_html(description_<lang>)`; skip if empty. Breadcrumb = join non-empty `page_name_*` / `category_name_*` / `subcategory_name_*` with `" > "`. Indexed body = `f"{breadcrumb}\n{title}\n{text}"` (or without breadcrumb). Title field = `f"{breadcrumb} — {title}"` or just title. Then `chunk_text`.

Document:

| Field | Value |
|-------|--------|
| `id` | `help_content_{help_content_id}_{lang}_{i}` |
| `source_type` | `help_content` |
| `source_id` | `help_content_id` |
| `language` | lang |
| `title` | breadcrumb title |
| `content` | chunk |
| `content_vector` | embedding |
| `category_name_en/ar` | **`page_name_en/ar`** (page, not category) |
| `url_pattern` | url_pattern |
| `help_content_id` | id |
| `chunk_index` | i |

Deleted/hidden: `is_visible = FALSE` (+ same `updated_at` OR when incremental). Filter: `source_type eq 'help_content' and help_content_id eq N`.

### 10.2 `mall_info`

```sql
SELECT
    up.unique_property_id, up.marketing_name, up.city, up.country,
    up.mall_information, up.mall_services, up.total_gross_leasable_area,
    TRIM((up.mall_information::jsonb) -> 'overview' ->> 'mall_manager') AS mall_manager
FROM cenomi.unique_properties up
WHERE up.is_primary = true
-- incremental: AND up.updated_at > :since
-- unless INDEXER_MALL_INFO_FULL_ON_INCREMENTAL or UndefinedColumn → full fetch
```

Per section from `parse_mall_information`: `indexed = f"{mall_name}\n{section_content}"`. **One embed per section, no chunking.** `mall_name` fallback `Property {id}`.

| Field | Value |
|-------|--------|
| `id` | `mall_info_{prop_id}_{section}` |
| `source_type` | `mall_info` |
| `source_id` | prop_id |
| `language` | `en` |
| `title` | `f"{mall_name} — {section.title()}"` |
| `mall_name` | marketing_name |
| `unique_property_id` | prop_id |
| `section` | overview / services / social |
| `chunk_index` | 0 |

Filter: `source_type eq 'mall_info' and unique_property_id eq N`.

If incremental SQL fails because `updated_at` is missing, log `indexer.mall_info_updated_at_missing` and full-rebuild that source.

### 10.3 `key_contact`

**Mall Manager (a)** — from JSON `overview.mall_manager`, comma-separated **emails**, resolve **each** email:

```sql
SELECT LOWER(email) AS email_lower,
       COALESCE(NULLIF(TRIM(CONCAT_WS(' ', first_name, last_name)), ''), '') AS full_name,
       COALESCE(phone, '') AS phone
FROM cenomi.users
WHERE LOWER(email) IN :emails
```

Render header `Mall Manager contacts for {mall_name}:` then `"- {name} | {email} | {phone}"` (omit blank email/phone). Id `key_contact_{prop_id}_MALL_MANAGER`.

**Other roles (b)** — `DISTINCT ON (property unique id, role_id, tenant_contact_user_id)`:

```sql
SELECT DISTINCT ON (p.unique_property_id, r.role_id, tc.tenant_contact_user_id)
    p.unique_property_id,
    r.role_name,
    COALESCE(r.role_name_en, r.role_name, '') AS role_name_en,
    COALESCE(r.role_name_ar, '') AS role_name_ar,
    COALESCE(u.first_name, '') AS first_name,
    COALESCE(u.last_name, '') AS last_name,
    COALESCE(u.email, '') AS email,
    COALESCE(u.phone, '') AS phone
FROM cenomi.tenant_contacts tc
INNER JOIN cenomi.users u ON tc.tenant_contact_user_id = u.user_id
INNER JOIN cenomi.roles r ON tc.role_id = r.role_id
INNER JOIN cenomi.properties p ON p.property_id = tc.property_id
WHERE p.unique_property_id IN :prop_ids
  AND tc.user_type = 'ADMIN'
  AND (tc.role_status = 'ACTIVE' OR tc.role_status IS NULL)
  AND r.role_name IN :roles
ORDER BY p.unique_property_id, r.role_id, tc.tenant_contact_user_id,
         r.role_name, u.first_name, u.last_name
```

`KEY_ROLES` (no `MALL_MANAGER`):

```
MALL_ADMIN, FM_MANAGER, MAINTENANCE_MANAGER, OPERATIONS_MANAGER, LEASING_MANAGER,
LEASING_ADMIN, KEY_ACCOUNT_MANAGER, TENANT_RELATION_OFFICER, MARKETING, FINANCE_MANAGER,
CUSTOMER_SERVICE_REPRESENTATIVE, CUSTOMER_RELATIONS_TEAM, AREA_MANAGER, REGIONAL_HEAD
```

Group by `role_name_en` else `role_name` else `"Staff"`. `ROLE_KEY = members[0]["role_name"].replace(" ","_").upper()`. Content header `"{display_role} contacts for {mall_name}:"`. Id `key_contact_{prop_id}_{ROLE_KEY}`. One embed per group, `chunk_index=0`, `language=en`.

Incremental: compute affected `unique_property_id`s then rebuild **whole mall**:

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

Then `filter=source_type eq 'key_contact' and unique_property_id eq N` before upsert.

### 10.4 `event` (bug-compatible fan-out)

```sql
SELECT ae.announcement_event_id, ae.title_en, ae.title_ar,
       ae.description_en, ae.description_ar,
       ae.start_date, ae.end_date, ae.is_event, ae.is_announcement,
       ae.applies_to_all_malls, ae.deleted_at,
       up.marketing_name AS mall_name
FROM cenomi.announcements_events ae
LEFT JOIN cenomi.announcement_event_malls aem
       ON ae.announcement_event_id = aem.announcement_event_id
LEFT JOIN cenomi.unique_properties up
       ON aem.unique_property_id = up.unique_property_id
-- full: WHERE ae.deleted_at IS NULL
-- incremental: WHERE ae.updated_at > :since OR ae.deleted_at > :since
```

- `mall_name` fallback: `"All Malls"` if `applies_to_all_malls` else `""`
- `type_line`: Event / Announcement / `Event, Announcement`; default Event
- `date_line`: `Date: {start} to {end}` or `Date: {start}` or omit
- Per lang: skip empty title. Content newline-join type+title, date_line, `Mall: {mall_name}`, `clean_html(description)`. Then chunk.
- `id = event_{event_id}_{lang}_{i}`
- Soft-deleted rows: do not upsert; delete via `announcement_event_id`
- Filter: `source_type eq 'event' and announcement_event_id eq N`

LEFT JOIN fans N malls → N rows, **same id**, last write wins. **Do not fix in this ticket.**

### 10.5 PDF sources (disabled)

`INDEXER_ENABLE_USER_GUIDES` / `INDEXER_ENABLE_DOWNLOAD_CENTER` default false. If CLI `--source guides` while flag off, log `indexer.source_disabled` and skip.

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

Blob download by `document_id`. `pdfplumber`, 1-indexed `(page_number, text)`, skip empty pages. Chunk per page. Ids:

- `user_guide_{guide_id}_{lang}_{page}_{chunk}`
- `download_center_{document_id}_{page}_{chunk}`

---

## 11. Writer

- `upsert_documents` batches of 1000; log `indexer.upsert_batch` with succeeded/failed; any failed doc ⇒ source fails (do not advance watermark).
- `delete_by_source`: page `search("*", filter=source_type eq '…', select=id, top=1000)` until page `< 1000`.
- `delete_by_filter`: same paging for scoped deletes.
- Dry-run: never call Search writes (reconcile dry-run may *read* the index if credentials exist; do not delete).

---

## 12. CLI

```
python -m app.indexer.cli --full
python -m app.indexer.cli --source help|mall|contacts|events
python -m app.indexer.cli --incremental
python -m app.indexer.cli --incremental --source events
python -m app.indexer.cli --since 2026-08-01T00:00:00Z
python -m app.indexer.cli --full --dry-run
python -m app.indexer.cli --reconcile
```

- `--full` / `--incremental` / `--reconcile` mutually exclusive.
- `--source` alone implies `--full`.
- `--since` ignored with `--full` (log warning).
- Attempt **every** selected source; aggregate failures.
- Exit: `0` ok, `1` unhandled or all failed, `2` partial.
- Docker `CMD ["--incremental"]` so a mode is always passed.

Events: `indexer.run_start`, `indexer.source_start`, `indexer.embed_batch`, `indexer.embed_done`, `indexer.upsert_batch`, `indexer.source_done`, `indexer.run_done`, `indexer.error`.

---

## 13. Part B — watermarks & lock

Alembic **006** (revises **005**), chatbot DB:

```sql
CREATE TABLE indexer_watermarks (
    source        TEXT PRIMARY KEY,
    last_run_at   TIMESTAMPTZ NOT NULL,
    watermark_at  TIMESTAMPTZ NOT NULL,
    last_status   TEXT NOT NULL,
    docs_upserted INTEGER NOT NULL DEFAULT 0,
    docs_deleted  INTEGER NOT NULL DEFAULT 0,
    error         TEXT NULL,
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

`source` values: `help_content` | `mall_info` | `key_contact` | `event` (plus PDF types if enabled).

Rules:

- Advance `watermark_at` only after that source’s upserts all succeed. On failure: `last_status='failed'`, watermark unchanged (at-least-once; ids are deterministic).
- New watermark = `run_started_at - INDEXER_WATERMARK_LAG` (default 300s), **not** `max(updated_at)` of rows.
- If capped by `INDEXER_MAX_DOCS_PER_RUN`, sort by `row_updated_at` ascending and set watermark to last processed `row_updated_at`.
- No row → treat source as **full** (delete_by_source then upsert), then insert watermark.
- `--since` overrides stored value; persist only on success.
- Dry-run: no `record_run_start` / no watermark writes.
- `last_run_at` at start of a real run so stuck jobs are visible.

Advisory lock on chatbot DB, **same key for full and incremental**:

```sql
SELECT pg_try_advisory_lock(3547, 1);
```

Hold the **same connection** for the whole run (session-level lock). If not acquired: log `indexer.lock_held`, exit **0**. Unlock in `finally`.

**Full path:** `delete_by_source` then upsert (legacy availability gap — acceptable for weekly backstop).

**Incremental path:** scoped delete-before-upsert per entity in the new doc set + delete `fetch_deleted_ids`. No whole-source delete.

**Reconcile:** page index `(source_id)` for that `source_type`, minus `fetch_live_ids`, delete orphans via `id_filter`.

---

## 14. Docker + pipeline

`Dockerfile.indexer`: copy API two-stage build; `pip install ".[indexer]"`; copy `app` only (no alembic required in job image — API applies 006); non-root `appuser`; **no** EXPOSE; **no** healthcheck.

```
ENTRYPOINT ["python", "-m", "app.indexer.cli"]
CMD ["--incremental"]
```

`pipelines/sr-chatbot-indexer-prod.yml` modelled on `sr-chatbot-api-prod.yml`:

- Tag trigger `release-prod-ai-v2-indexer-*`
- Variable group `v2-ai-prod-vars` + `indexer_repository` + `indexer_job_name`
- Build context `service-request-chatbot/backend`, `Dockerfile.indexer`, `--platform linux/amd64`
- `az containerapp job update --name $(indexer_job_name) --image "$IMAGE"`
- Verify `az containerapp job show … properties.template.containers[0].image` equals expected tag

**Infra (document, do not provision in code):** scheduled job cron `*/15 * * * *` args `--incremental`; separate manual job args `["--full"]`; `replicaTimeout` 3600; `parallelism` 1; `replicaRetryLimit` 1. Lock prevents full∩incremental overlap.

---

## 15. Tests (required)

`tests/unit/indexer/` — no network, no DB. Stub embedder: `embed_batch` returns short vectors in order.

Must cover:

| Area | Cases |
|------|--------|
| chunker | under-limit passthrough; overlap tokens match; empty/whitespace/`None` → `[]` |
| html_cleaner | script/style gone; `&amp;`; whitespace collapse; `None` → `""` |
| mall_information | missing blob + GLA only; GLA overrides JSON total_area; three timing shapes; dict/list/scalar overview; services from column vs JSON; social block |
| help_content builder | exact id `help_content_42_en_0`; breadcrumb prefix on body **and** title; `category_name_en` = page name; skip empty lang; null optional fields |
| mall_info builder | mall-name prefix; `mall_info_{id}_{section}` |
| key_contact builder | comma-split manager emails; `role_key` from group (`FM_MANAGER` not leaked loop var); ids |
| event builder | skip empty title; All Malls fallback; type/date lines; skip `deleted_at` rows |
| watermark | run_start − lag; capped_at wins if earlier |
| CLI | `--source` implies full; `--full` ⊥ `--incremental`; `--since` parse `Z`; `--reconcile` |
| writer | fake async SearchClient: upsert batching, delete_by_source paging |
| embed_batch | OpenAI + Azure providers with stub client; empty list; order |

`tests/eval/indexer_eval.py` — offline contract eval (print PASS/FAIL table, exit 1 on fail). Run: `PYTHONPATH=$(pwd) python -m tests.eval.indexer_eval`.

`tests/integration/test_indexer_dry_run.py` — skip unless `RUN_INDEXER_INTEGRATION=1` and `CENOMI_DATABASE_URL`; `--full --source mall --dry-run`.

Optional live FAQ scenarios S70–S74 in `tests/eval/scenarios.py` tagged `faq-rag`: `expect_ui_type=message`, `expect_no_submit=True`, keywords **soft** so static FAQ fallback still passes.

Do **not** change unrelated handover unit tests.

---

## 16. Orchestration algorithm (`cli.async_main`)

1. `configure_logging()`; `run_started_at = now(utc)`.
2. Resolve sources; if none enabled, `run_done` success exit 0.
3. Open chatbot engine; connect; `pg_try_advisory_lock`; if false exit 0.
4. `CenomiPgReader(CENOMI_DATABASE_URL)`; `get_embedding_provider()` or fail.
5. If not dry-run (or mode=reconcile): require search config; `ensure_index_exists` only when not dry-run; `make_search_client`.
6. For each source:
   - incremental + no watermark + no `--since` → treat as full
   - mall + `INDEXER_MALL_INFO_FULL_ON_INCREMENTAL` → treat as full
   - fetch_rows; on mall `updated_at` missing → full fetch
   - incremental: `_cap_rows` by `row_updated_at`
   - `build_documents`
   - dry-run: log counts
   - else full: `delete_by_source` then upsert; incremental: scoped_filters + deleted_ids then upsert
   - success: save watermark (run_start − lag, or capped_at)
   - failure: save failed, **do not** advance watermark; continue next source
7. Unlock; dispose; exit 0/1/2.

---

## 17. Acceptance

**Part A / cutover**

- `--full --dry-run --source mall` builds docs, writes nothing.
- `--full` document counts per `source_type` within ±1% of legacy job.
- ~20 queries in `docs/faq-rag-testing.md` keep the same top-3 titles after cutover.
- Ids and prefixes match §10 exactly (unit tests lock this).

**Part B**

- First incremental with no watermark does a full build then inserts a row.
- Failed source retries the same window.
- Editing one help row in staging appears in FAQ within ~20 minutes; `docs_upserted` in the single digits.
- Concurrent second run exits 0 with `indexer.lock_held`.

**Repo**

- API `pip install .` still has no tiktoken/pdfplumber/search SDK.
- Job image `pip install ".[indexer]"`.
- `python -m pytest tests/unit/indexer` all pass without Azure/DB.

---

## 18. Implementation order (Fable tasks)

1. Settings + `embed_batch` on protocol + 3 providers + unit tests for OpenAI/Azure stubs.
2. `documents.py`, `html_cleaner`, `chunker`, `mall_information` + tests.
3. `schema.py` / `writer.py` (lazy SDK) + fake-client tests.
4. Four live sources’ **builders** (no DB) + tests for ids/prefixes/role_key fix.
5. `pg_reader` + `fetch_*` SQL; `cli.py` `--full`/`--source`/`--dry-run`.
6. `Dockerfile.indexer` + prod pipeline YAML.
7. Alembic 006 + `IndexerWatermark` + `state.py` + lock.
8. Incremental SQL, scoped delete, `--incremental`/`--since`/`--reconcile`, caps, mall fallback.
9. Disabled PDF modules + flags.
10. `tests/eval/indexer_eval.py` + optional S70–S74.

---

## 19. Out of scope reminders for the implementer

- No Cenomi API repo changes, triggers, or tickets required to ship.
- If `unique_properties.updated_at` is absent at runtime, fallback already specified — do not block on platform.
- Do not add `queryType=semantic` in `azure_search.py`.
- Do not put indexer dependencies in the un-extra API install.
- Do not use `--incremental` as an alias of `--full` (legacy did; this spec drops that once Part B exists). Until Part B lands, `--incremental` may log a warning and run full — but land Part B in the same PR if possible.

---

*End of spec. Implement exactly this. If a detail is missing, match the live `cenomi-help-index` document shape and the read path in `app/integrations/search/azure_search.py`.*
