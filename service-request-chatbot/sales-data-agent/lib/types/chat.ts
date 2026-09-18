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
  show_file_upload?: boolean;
}

export interface PeriodMismatchWarningUI extends ResponseUIBase {
  type: "period_mismatch_warning";
  asked_label: string;
  file_label: string;
  includes_asked_month?: boolean;
  show_file_upload: boolean;
}

export interface PendingMonthItem {
  label: string;
  year_month: string;
}

export interface PendingMonthsListUI extends ResponseUIBase {
  type: "pending_months_list";
  leases: Array<{ lease_code: string; months: PendingMonthItem[] }>;
  show_file_upload: boolean;
  show_template_download?: boolean;
}

export interface OtherMonthsReminderUI extends ResponseUIBase {
  type: "other_months_reminder";
  lease_code: string;
  asked_label: string;
  asked_year_month: string;
  other_months: PendingMonthItem[];
  show_file_upload: boolean;
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
  brand?: string;
  property?: string;
  period: string;
  sales_period_start: string;
  sales_period_end: string;
  sales_amt: number;
  current_status: string;
}

export interface ManualEntryConfirmUI extends ResponseUIBase {
  type: "manual_entry_confirm";
  lease_code: string;
  brand?: string;
  property?: string;
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

export interface AdminConfirmTarget {
  lease_code: string;
  lease_id?: number;
  sales_period_start?: string;
  sales_period_end?: string;
  sales_amt?: number;
}

export interface AdminConfirmUI extends ResponseUIBase {
  type: "admin_confirm";
  action: string;
  action_label?: string;
  lease_code: string;
  targets?: AdminConfirmTarget[];
  unmatched?: string[];
  comment_text?: string;
}

export interface AdminActionSuccessUI extends ResponseUIBase {
  type: "admin_action_success";
  action: string;
  action_label?: string;
  lease_code: string;
  lease_codes?: string[];
  succeeded?: number;
  failed?: number;
}

export interface StaleNotificationUI extends ResponseUIBase {
  type: "stale_notification";
  show_listing_link: boolean;
}

export interface SupportingDocPromptUI extends ResponseUIBase {
  type: "supporting_doc_prompt";
  lease_code: string;
  lease_id?: number;
  sales_period_start: string;
  sales_period_end: string;
  current_status: string;
  target_status: string;
  ask_label?: string;
  sales_amt?: number | null;
  comment_text?: string | null;
  show_file_upload: boolean;
  accept?: string;
}

export interface SupportingDocSelectUI extends ResponseUIBase {
  type: "supporting_doc_select";
  rows: SalesDataRow[];
}

export interface SupportingDocConfirmUI extends ResponseUIBase {
  type: "supporting_doc_confirm";
  lease_code: string;
  lease_id?: number;
  sales_period_start: string;
  sales_period_end: string;
  filename: string;
  current_status: string;
  target_status: string;
  ask_label?: string;
  sales_amt?: number | null;
  comment_text?: string;
  comment_required?: boolean;
}

export interface SupportingDocSuccessUI extends ResponseUIBase {
  type: "supporting_doc_success";
  lease_code: string;
  status: string;
  document_id?: string;
  filename?: string;
  ask_label?: string;
}

export type ResponseUI =
  | SalesDataUploadPromptUI
  | CsvValidationErrorUI
  | LeaseValidationErrorUI
  | PeriodMismatchWarningUI
  | PendingMonthsListUI
  | OtherMonthsReminderUI
  | SalesDataReviewUI
  | ManualEntryConfirmUI
  | SubmissionSuccessUI
  | SubmissionErrorUI
  | StatusSummaryUI
  | AdminSalesDataListUI
  | AdminConfirmUI
  | AdminActionSuccessUI
  | StaleNotificationUI
  | SupportingDocPromptUI
  | SupportingDocSelectUI
  | SupportingDocConfirmUI
  | SupportingDocSuccessUI
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
  property_name?: string;
  company_name?: string;
  sales_period_start: string;
  sales_period_end: string;
  status: string;
  sales_amt?: number | null;
  comment_text?: string | null;
  document_id?: string | null;
}
