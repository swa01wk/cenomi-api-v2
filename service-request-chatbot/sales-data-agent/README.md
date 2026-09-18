# Sales Data Agent — Demo Frontend

A Next.js 15 demo app for the **Sales Data Agent** workflow.  
Runs on **port 3001** and connects to the AI backend (port 8000) and mock backend (port 8001).

---

## Demo Architecture

```
sales-data-agent/  ← this app (port 3001)
    ↕ chat turns (POST /api/chat/service-request)
cenomi-api-v2 AI backend (port 8000)  ← LangGraph nodes
    ↕ data API calls
mock-backend/  (port 8001)  ← in-memory FastAPI
```

---

## Quick Start

### 1 — Start the Mock Backend

```bash
cd ../mock-backend
pip install -r requirements.txt
uvicorn main:app --reload --port 8001
```

### 2 — Start the AI Backend

```bash
cd ../backend
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

### 3 — Start This Frontend

```bash
npm install
npm run dev          # runs on http://localhost:3001
```

---

## Demo Roles

| Role | Token | Leases | Scenario |
|------|-------|--------|----------|
| **Tenant CSV Uploader** | `demo-tenant-csv` | LC-1001, LC-1002 (PENDING) | Upload CSV or enter manually |
| **Tenant Doc Uploader** | `demo-tenant-doc` | LC-1003 (NEED_REUPLOAD) | Re-upload corrected data |
| **Sales Data Approver** | `demo-admin` | All submitted rows | Review and approve |

---

## Key User Flows

### CSV Upload Flow
1. Open app → login as **Tenant CSV Uploader**
2. Type: *"I want to submit sales data for August"*
3. Upload a CSV file (use template from the toolbar)
4. Layer 1 validation → Layer 2 validation → editable review table
5. Edit amounts if needed → click **Submit Sales Data**
6. Success confirmation

### Manual Entry Flow  
1. Login as **Tenant CSV Uploader**
2. Type: *"Sales for August 2026, lease LC-1001, SAR 85,000"*
3. Agent extracts fields → asks for any missing ones
4. Confirmation card → confirm → success

### Admin Review Flow
1. Login as **Sales Data Approver**
2. Type: *"Review sales data"*
3. See list of submitted rows
4. Type: *"Approve LC-2001"* → confirmation → approved

### Reset Demo Data
```bash
curl -X POST http://localhost:8001/dev/mock/reset
```

---

## Project Structure

```
sales-data-agent/
├── app/
│   ├── layout.tsx
│   ├── page.tsx              # redirects → /sales-data-chat
│   ├── login/page.tsx        # role selector
│   └── sales-data-chat/page.tsx
├── components/
│   ├── auth/AuthGuard.tsx
│   └── chatbot/
│       ├── SalesDataChat.tsx         # main orchestrator
│       ├── SalesDataReviewCard.tsx   # editable table
│       ├── FileUploadButton.tsx
│       ├── ManualEntryConfirmCard.tsx
│       ├── StatusSummaryCard.tsx
│       ├── SubmissionResultCard.tsx
│       ├── ChatInput.tsx
│       └── MessageBubble.tsx
└── lib/
    ├── api/
    │   ├── auth-client.ts    # demo role → Bearer token
    │   ├── chat-client.ts    # POST /api/chat/service-request
    │   └── upload-client.ts  # POST /api/v2/sales-data/upload
    └── types/chat.ts
```
