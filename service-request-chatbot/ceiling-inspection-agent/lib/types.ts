/** Shapes returned by the AI backend (POST /api/chat/service-request). */

export interface ChipOption {
  label: string;
  value: string;
  action?: string;
}

export interface Suggestions {
  kind: string;
  options: ChipOption[];
}

export interface CardField {
  key: string;
  label: string;
  value: string;
  editable?: boolean;
  inputType?: string;
}

export interface DocumentRow {
  key: string;
  label: string;
  required?: boolean;
  status: "uploaded" | "missing";
  document_id?: string;
  file_name?: string;
}

export interface ResponseUI {
  type?: string;
  message?: string;
  suggestions?: Suggestions;
  // confirmation_card
  requestType?: string;
  stage?: string;
  fields?: CardField[];
  confirmAction?: string;
  confirmLabel?: string;
  // document_requirement
  documents?: DocumentRow[];
  uploadContext?: { session_id?: string; sr_id?: string; allowedDocumentTypes?: string[] };
  workflow_complete?: boolean;
  cancelled?: boolean;
  [key: string]: unknown;
}

export interface ChatState {
  intent?: string | null;
  workflow_stage?: string | null;
  sr_id?: string | null;
  confirmation_status?: string | null;
}

/** Live form snapshot sent with every ceiling-agent turn (backend ceiling_form_state.py). */
export interface FormState {
  agent: string;
  sub_category: string | null;
  stage: string | null;
  sr_id: string | null;
  /** True once the SR left the create stage — review and ack happen in chat. */
  locked: boolean;
  banner: string | null;
  values: FormValues;
  missing: string[];
  active_field: string | null;
  documents: { document_id?: string; document_type?: string; file_name?: string }[];
}

export interface FormValues {
  company_name?: string;
  tenant_profile_id?: number;
  lease_code?: string;
  lease_brand_mall?: string;
  brand?: string;
  mall?: string;
  city?: string;
  contracted_area?: number | string;
  unit_codes?: string[];
  title?: string;
  description?: string;
  startDate?: string;
  endDate?: string;
  /** HH:MM — unset means the agent's 09:00 default. */
  inspectionTime?: string;
  inspectionEndTime?: string;
  notes?: string;
  comments?: string;
  inspection_done_by?: number;
}

export interface ChatResponse {
  session_id: string;
  active_agent: string | null;
  message: string;
  ui: ResponseUI;
  state: ChatState;
  form_state?: FormState | null;
}

export interface CompanyOption {
  tenant_profile_id: number;
  company_name: string;
}

export interface LeaseOption {
  lease_code: string;
  lease_brand_mall: string;
  brand: string;
  mall: string;
  city: string;
  contracted_area: number;
  unit_codes: string[];
  company_name: string;
  tenant_profile_id: number;
}

export interface FormOptions {
  sub_categories: { value: string; label: string; short_label: string }[];
  document_types: { value: string; label: string }[];
  inspection_done_by: { value: number; label: string };
  required_fields: string[];
  optional_fields: string[];
}

export interface InboxRow {
  notification_id: string;
  kind: string;
  sr_id: string | null;
  sub_category: string;
  message: string;
  created_at?: string | null;
  read: boolean;
}

export interface InboxGroup {
  sub_category: string;
  label: string;
  total_count: number;
  notifications: InboxRow[];
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  text: string;
  ui?: ResponseUI;
  at: Date;
}
