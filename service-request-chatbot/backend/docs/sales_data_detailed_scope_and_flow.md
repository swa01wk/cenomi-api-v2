# Cenomi Sales Data Flow: End-to-End Technical Integration Document

This document provides a highly detailed mapping of the Sales Data architecture. It bridges the `cenomi-frontend` application and the `cenomi-api` backend, offering a step-by-step guide on how data is ingested, processed, structured by roles, and finalized. Use this document as a technical map when exploring the backend repository.

---

## 1. Roles & Access Control

Before diving into the APIs and workflows, it's essential to understand the actors interacting with the sales data flow. The system governs access via primary user roles with strict constraints:

### A. Tenant Company Admin (`UserType: "TENANT"`)
External users representing the tenant company interacting via the Tenant Portal.
* **Responsibilities:** They are the **only** users permitted to initiate the upload of raw POS transactional data, submit daily/monthly bulk aggregated sales, and upload supporting documents.
* **Permissions:** They push data forward into specific submission states (`UPLOADED_SUPPORTING_DOCUMENT`, `SUBMITTED`, `UPLOADED_AUDITED_SALES`, or `RE_UPLOADED`).
* **Restrictions:** They cannot view the internal administrative staging tables, nor can they `APPROVE` or `REJECT` their own sales data.

### B. Admin User (`UserType: "ADMIN"`)
Internal Cenomi staff interacting via the centralized Admin Portal.
* **Responsibilities:** Reviewing submitted sales figures, validating supporting documents, and managing the integration workflow.
* **Restrictions:** The system explicitly prevents general Admin users from acting on behalf of a tenant to push data into "Tenant-only" submission states. Furthermore, general admins without the specific team role cannot approve the finalized data.

### C. Sales Data Team (Role Name: `"SALES_DATA_TEAM"`, Role ID: `30`)
A specialized, highly privileged internal role assigned to certain Admin users.
* **Responsibilities:** They are the **only** team authorized to formally `APPROVE` the finalized sales data.
* **Permissions:** During data fetching and aggregation, the `SalesDataUsecase` explicitly checks for this `RoleID`. If an admin holds this role, they are granted unrestricted visibility to fetch, list, aggregate reports, and execute approvals across all properties, tenants, and lease codes, bypassing standard row-level assignment filters.

---

## 2. High-Level Entity Structure

There are three primary tables/entities governing sales data:

1. **`sales_data_transactions`** (`domain/salesdatatransaction.go`): Stores granular, receipt-level data ingested from POS integrations.
2. **`sales_data_stage`** (`domain/salesdata.go`): The centralized staging area. It holds aggregated daily/monthly sales data before it is reviewed and approved.
3. **`sales_data`** (`domain/salesdata.go`): The finalized ledger for approved sales data, which is synchronized back to external systems like the Yardi PMS.

---

## 3. Detailed Workflows & API Mappings

### Workflow A: Bulk Upload via Portal (Tenant Company Admin)

When a tenant admin logs into the portal and uploads their sales data (either manually filling out a form or uploading an aggregated CSV/Excel), this flow is triggered.

#### Frontend Execution (`cenomi-frontend`)
- **Trigger**: The Company Admin clicks the **Upload** button in the `<SalesDataUpload />` component.
- **Function**: `uploadSalesData(salesData, tenantId)` located in `libs/api/src/lib/salesdata/salesdataApi.ts`.
- **Endpoint Called**: `POST /sales-data`

#### Backend Execution (`cenomi-api`)
1. **Handler**: `sales_data_handler.go` -> `UpdateSalesData()` / `UpdateDailySalesData()`
2. **Usecase**: `SalesDataUsecase.UpdateSalesData` or `InsertDailySalesData`
3. **Deep Backend Logic**:
   - **Validation**: The backend iterates over the payload, validating the `LeaseCode` and parsing dates. In the daily flow, it explicitly asserts ownership by checking `lease.TenantProfileID == userTenantProfileID`.
   - **Conflict Checking**: For daily insertions, it queries `FetchSalesDataStageDetails`. If a staging record already exists for the given period, it halts and throws `ErrRecordAlreadyExists`.
   - **Persistence**: Using a `transactionRunner`, it pushes the bulk records into `sales_data_stage`.
   - **Auditing & Notification**: Once successfully inserted, the system triggers `auditLoggerUsecase` to log the transaction. More importantly, it executes `notifyUserOnSalesDataUpdate()`, which pushes a notification specifically to users possessing the `SALES_DATA_TEAM` role, alerting them that new data has been submitted.

---

### Workflow B: Uploading Supporting Documents (Receipts/Evidence)

If the tenant company admin is required to upload a supporting document alongside their sales figures (e.g., an audited sales report or a scanned receipt), this utilizes the file management system.

#### Frontend Execution (`cenomi-frontend`)
- **Trigger**: The `<DndFileUploadSalesData />` component accepts a file drop.
- **Function**: `reuploadSalesDataFile(...)` located in `libs/api/src/lib/salesdata/salesdataApi.ts`.
- **Endpoint Called**: `PUT /files?file_extension={ext}&lease_id={id}&tenant_profile_id={id}`

#### Backend Execution (`cenomi-api`)
1. **Handler**: File Upload endpoints in the backend (handled outside of the standard sales handler, usually within the `file_upload` module).
2. **Processing**: The binary stream is captured, uploaded to cloud storage (e.g., S3), and a `DocumentID` is generated.
3. **Integration**: The `DocumentID` is subsequently linked to the specific `SalesData` record so reviewers can download it via the `SignedURL`.

---

### Workflow C: Real-time POS Transactional Data

For tenants with direct POS integrations, receipts are pushed individually in real-time or near real-time.

1. **Request**: Tenant system calls `POST /sales-data/transactions`.
2. **Backend Handler**: `sales_data_transaction_handler.go` -> `InsertSalesDataTransactions()`
3. **Backend Usecase**: `SalesDataTransactionUsecase.InsertSalesDataTransactions()`
   - **Lease Resolution**: A receipt identifies its lease using *exactly one* method: either via a direct `LeaseCode` or by mapping a POS `StoreCode` via the `StoreLeaseMappingRepository`.
   - **Deduplication Strategy**: It queries `FetchSalesDataTransaction` using the unique tuple of `(LeaseCode, ReceiptReference, BusinessDate)`. If any receipt in the batch is recognized as a duplicate, the *entire* batch halts and fails with an all-or-nothing validation error (`ErrSalesDataTransactionAlreadyExists`).
4. **Repository Storage**: Successful validations bulk insert into `sales_data_transactions` with a status of `RECEIVED`.

#### The Background Rollup Job (`SalesDataTransactionSyncUsecase.SyncReceiptsToDailySales`)
Because the main ledger tracks daily/monthly totals, granular receipts must be aggregated continuously:
1. **Batch Limits**: The job loops up to 50 times (`maxSyncIterations`) processing 200 groups (`chunkSize`) at a time to prevent memory exhaustion on large backlogs.
2. **Idempotent Aggregation**: It fetches pending `RECEIVED` receipts grouped by `(lease_code, business_date)`. It then sums the `NetAmount` of `SALE` and `EXCHANGE` transactions. **Crucially**, it recomputes the *full* total for that day, summing both `RECEIVED` and already `PROCESSED` rows to prevent double counting.
3. **Commit**: Upserts the aggregated total into `sales_data_stage` (setting `Source = TRANSACTIONAL` and updating `TransactionCount`), safely overwriting any previous calculation for that day. Marks the newly arrived receipts as `PROCESSED`.

---

### Workflow D: Yardi PMS Sync

The application keeps sales data synchronized with the Yardi Property Management System.

1. **Job Trigger**: A scheduled cron job or manual trigger invokes `SalesDataSyncUsecase.SyncSalesData()`.
2. **Fetching Data**: `SalesDataSyncRepository.FetchYardiSalesData()` queries the Yardi API to populate the raw `YardiSalesData` structures.
3. **Staging**: The data is mapped to the internal `SalesData` domain and inserted into `sales_data_stage` for normalization.

---

## 4. Data Review, Approval, and Visualization

Once data reaches `sales_data_stage`, it is visualized in the admin/tenant dashboards and moved through an approval process.

### Visualization APIs (Frontend Dashboards)
The frontend utilizes various endpoints to render grids and graphs:
- **`GET /sales-data`**: Fetches the approved main ledger data. (Called via `getSalesListingData`).
- **`GET /sales-data-stage`**: Fetches pending/staged data for Admin review.
- **`GET /sales-data-stage/stats`**: Provides dashboard KPI widgets (total amount, pending count, etc.).
- **`GET /sales-data/month-wise`**: Used by `getSalesGraphData()` to render the Monthly Performance graphs.
- **`GET /sales-data/company-filter-options`**: Used by the `<TenantTreeSelect />` component to build the hierarchical tenant dropdown.

### Approval Process API & Edit Window Constraints
When an administrator from the **Sales Data Team** reviews staged data and clicks "Approve":

1. **Request**: The frontend invokes `updateSalesData(salesData)` which calls `PATCH /sales-data`.
2. **Backend Handler**: `Update` function in `sales_data_handler.go`.
3. **Backend Usecase**: `SalesDataUsecase.UpdateSalesDataStatus()`.
4. **Edit Window Constraints**: If a Tenant attempts to modify an already submitted record, the system verifies a dynamic `salesDataEditPeriod` (fetched from the MasterData configurations). If the edit window has elapsed since the `SalesPeriodEnd`, the request violently rejects with `ErrSalesEditWindowOver`.
5. **Approval Action**: The record's state transitions. If it reaches `APPROVED`, the system injects `ApprovedBy = UserID` and sets `ApprovedAt = time.Now()`.
6. **PMS Write-back**: The `SalesDataRepository.UpdateSalesDataPushToYardiStatus()` is triggered, passing the finalized state back to Yardi.

---

## 5. Future Evolution: AI Agent Integration (LangGraph Workflow)

To modernize the Sales Data ingestion and approval experience, the system will evolve from static forms/grids to a conversational, **LangGraph-powered AI Agent Workflow**. This feature will be rolled out in strategic phases.

### Phase 1: Conversational Upload & Action Framework

#### Tenant Experience (Ingestion)
1. **Chatbot Interface**: Instead of navigating to the standard `<SalesDataUpload />` form, the Tenant Company Admin interacts with an embedded AI Agent window.
2. **Tool-Driven Validation**: The tenant uploads the Excel/CSV directly into the chat. The Agent utilizes configured tools to parse the file natively and perform initial validations (e.g., verifying `LeaseCode` validity, ensuring the data is not blank, and resolving format disparities).
3. **Automated Submission**: Once validated by the Agent's reasoning engine, the Agent autonomously invokes the existing `POST /sales-data` backend API to persist the data to the database.
4. **Notification**: The Agent seamlessly calls the notification endpoint to alert the Sales Data Team of the new submission.

#### Sales Data Team Experience (Approval)
1. **Context-Aware Routing**: The Sales Data Team receives a notification on their dashboard. Clicking the notification opens the Agent Chatbot UI instead of a static review page.
2. **Context Injection**: The Agent is automatically injected with the state and context of the notification (tenant details, lease metrics, submitted amounts).
3. **Action Execution**: The Sales Data Admin can issue natural language commands (e.g., *"Approve this submission"* or *"Request audited documents from this tenant"*). The Agent will execute these actions by dynamically mapping them to the existing `PATCH /sales-data` endpoint and initiating the state transition.

### Phase 2: AI Anomaly Detection & Trend Analysis

Once the operational Chatbot framework is live, the Agent will be augmented with predictive analysis tools to proactively identify risks and reduce manual review time.

#### The Analysis Engine
1. **Historical Comparison**: Upon a tenant submitting data, the Agent will silently execute an analysis engine tool, pulling 6-12 months of historical sales data for that specific `LeaseCode`.
2. **Anomaly Identification**: If the AI detects a statistical anomaly—such as a tenant consistently reporting ~500k in sales over the last 6 months suddenly dropping to 50k—it will flag the submission.
3. **Proactive Intervention**: When the Sales Data Team opens the review window, the Agent will immediately highlight the anomaly. It will present a summary of the historical trend and proactively suggest requesting further evidence (e.g., *"This submission deviates from historical averages by 90%. I suggest requesting Audited Sales Documents before approval."*).
4. **Seamless Resolution**: The Admin can click an Agent-provided action button to immediately dispatch the document request to the tenant.

---

## Summary for Backend Exploration

When exploring the `cenomi-api` repository, trace the code in the following order:

1. **Handlers**: `sales_data/delivery/http/` -> Look at how requests are parsed and validated, specifically observing the `checkUserPermissions` checks.
2. **Usecases**: `sales_data/usecase/` -> This is where the core business logic, aggregation math, and status transitions occur. You'll see the `RoleIDs` checked for the `SALES_DATA_TEAM` and the `transactionRunner` execution here.
3. **Repositories**: `sales_data/repository/postgres/` -> Look here for the raw SQL/ORM queries that persist data to Postgres.
4. **Domains**: `domain/salesdata.go` and `domain/salesdatatransaction.go` -> This is your data dictionary. All constants (like Statuses, Sources, and Transaction Types) are defined here.
