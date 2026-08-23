/** Chat turn request sent to the AI backend. */
export interface ChatRequest {
  sessionId?: string | null;
  message: string;
  action?: string;
  attachmentIds?: string[];
  correctedFields?: Record<string, unknown>;
}

/** Chat turn response from the AI backend. */
export interface ChatResponse {
  sessionId: string;
  responseUI: ResponseUI;
  workflowStage: string | null;
  intent: string | null;
  activeAgent: string | null;
}

/** A single message in the chat history. */
export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  text: string;
  timestamp: Date;
  ui?: ResponseUI;
}

// ---------------------------------------------------------------------------
// Response UI variants returned by the AI backend
// ---------------------------------------------------------------------------

export interface ResponseUIBase {
  type: string;
  message: string;
}

export interface SalesDataUploadPromptUI extends ResponseUIBase {
  type: "sales_data_upload_prompt";
  show_file_upload: boolean;
  show_template_download: boolean;
}

export interface CsvValidationErrorUI extends ResponseUIBase {
  type: "csv_validation_error";
  filename: string;
  errors: Array<{ row: number; lease_code: string; errors: string[] }>;
}

export interface LeaseValidationErrorUI extends ResponseUIBase {
  type: "lease_validation_error";
  errors: Array<{ lease_code: string; _error: string }>;
}

export interface SalesDataReviewUI extends ResponseUIBase {
  type: "sales_data_review";
  rows: SalesDataReviewRow[];
  filename: string;
  row_count: number;
}

export interface SalesDataReviewRow {
  lease_code: string;
  lease_id?: number;
  period: string;
  sales_period_start: string;
  sales_period_end: string;
  sales_amt: number;
  current_status: string;
}

export interface ManualEntryConfirmUI extends ResponseUIBase {
  type: "manual_entry_confirm";
  lease_code: string;
  sales_period_start: string;
  sales_period_end: string;
  sales_amt: number;
}

export interface SubmissionSuccessUI extends ResponseUIBase {
  type: "submission_success";
  row_count: number;
}

export interface SubmissionErrorUI extends ResponseUIBase {
  type: "submission_error";
  error_type: string;
  errors: Array<{ index?: number; field?: string; message: string; code?: string }>;
  show_reupload: boolean;
}

export interface StatusSummaryUI extends ResponseUIBase {
  type: "status_summary";
  rows: SalesDataRow[];
  grouped: Record<string, number>;
}

export interface AdminSalesDataListUI extends ResponseUIBase {
  type: "admin_sales_data_list";
  rows: SalesDataRow[];
}

export interface AdminConfirmUI extends ResponseUIBase {
  type: "admin_confirm";
  action: string;
  lease_code: string;
  comment_text?: string;
}

export interface AdminActionSuccessUI extends ResponseUIBase {
  type: "admin_action_success";
  action: string;
  lease_code: string;
}

export interface StaleNotificationUI extends ResponseUIBase {
  type: "stale_notification";
  show_listing_link: boolean;
}

export type ResponseUI =
  | SalesDataUploadPromptUI
  | CsvValidationErrorUI
  | LeaseValidationErrorUI
  | SalesDataReviewUI
  | ManualEntryConfirmUI
  | SubmissionSuccessUI
  | SubmissionErrorUI
  | StatusSummaryUI
  | AdminSalesDataListUI
  | AdminConfirmUI
  | AdminActionSuccessUI
  | StaleNotificationUI
  | (ResponseUIBase & Record<string, unknown>);

// ---------------------------------------------------------------------------
// Domain types
// ---------------------------------------------------------------------------

export interface SalesDataRow {
  id?: string;
  lease_id?: number;
  lease_code: string;
  brand_name?: string;
  mall_name?: string;
  sales_period_start: string;
  sales_period_end: string;
  status: string;
  sales_amt?: number | null;
  comment_text?: string | null;
}
