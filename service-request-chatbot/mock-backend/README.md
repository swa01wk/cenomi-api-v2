# Sales Data Mock Backend

In-memory FastAPI server that simulates the real `cenomi-api` sales-data endpoints.

## Startup

```bash
pip install -r requirements.txt
uvicorn main:app --reload --port 8001
```

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/sales-data` | List rows (tenant-scoped via Bearer token) |
| POST | `/api/v1/sales-data` | Bulk submit (all-or-nothing) |
| PATCH | `/api/v1/sales-data` | Status update (admin) |
| GET | `/api/v1/sales-data/template` | Download CSV template |
| PUT | `/files` | Generic file upload |
| POST | `/dev/mock/reset` | Reset to seed data |
| GET | `/dev/mock/state` | Inspect current in-memory DB |

## Demo Tokens

| Token | Role | tenant_profile_id |
|-------|------|-------------------|
| `demo-tenant-csv` | TENANT_CSV_UPLOADER | 101 |
| `demo-tenant-doc` | TENANT_DOC_UPLOADER | 102 |
| `demo-admin` | SALES_DATA_APPROVER | 999 |

## Seed Data

| Lease | Status | Notes |
|-------|--------|-------|
| LC-1001 | PENDING | tenant_profile_id 101 |
| LC-1002 | PENDING | tenant_profile_id 101 |
| LC-1003 | NEED_REUPLOAD | tenant_profile_id 102 |
| LC-2001 | SUBMITTED | for admin review |
| LC-2002 | SUBMITTED | for admin review |

Reset with:
```bash
curl -X POST http://localhost:8001/dev/mock/reset
```
