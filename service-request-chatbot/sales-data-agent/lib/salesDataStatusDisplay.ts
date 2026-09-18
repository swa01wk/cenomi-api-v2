/**
 * Portal-matching sales-data status labels.
 *
 * Admin listing (StatusHighlighterSalesData + LabelPicker type=salesdata):
 * several enums collapse to one badge; hover tooltip is the specific status.
 */

const SUBMITTED_STATUSES = new Set([
  "SUBMITTED",
  "UPLOADED_SUPPORTING_DOCUMENT",
  "RE_UPLOADED",
  "UPLOADED_AUDITED_SALES",
]);

const PENDING_STATUSES = new Set([
  "PENDING",
  "NEED_REUPLOAD",
  "NEED_UPLOAD_SUPPORTING_DOCUMENT",
  "NEED_UPLOAD_AUDITED_SALES",
]);

const TOOLTIPS: Record<string, string> = {
  PENDING: "Pending",
  NEED_REUPLOAD: "Need Reupload",
  NEED_UPLOAD_SUPPORTING_DOCUMENT: "Supporting Document Requested",
  NEED_UPLOAD_AUDITED_SALES: "Audited Sales Data Requested",
  SUBMITTED: "Submitted",
  UPLOADED_SUPPORTING_DOCUMENT: "Uploaded Supporting Document",
  UPLOADED_AUDITED_SALES: "Uploaded Audited Sales",
  RE_UPLOADED: "Sales Data Reuploaded",
  APPROVED: "Approved",
};

export type SalesDataBadgeTone = "pending" | "submitted" | "approved" | "other";

export function salesDataAdminLabel(status: string | null | undefined): string {
  const key = (status ?? "").toUpperCase();
  if (SUBMITTED_STATUSES.has(key)) return "Submitted";
  if (PENDING_STATUSES.has(key)) return "Pending";
  if (key === "APPROVED") return "Approved";
  if (!key) return "—";
  return key.replace(/_/g, " ");
}

export function salesDataAdminTooltip(status: string | null | undefined): string {
  const key = (status ?? "").toUpperCase();
  return TOOLTIPS[key] ?? salesDataAdminLabel(status);
}

export function salesDataBadgeTone(status: string | null | undefined): SalesDataBadgeTone {
  const label = salesDataAdminLabel(status);
  if (label === "Pending") return "pending";
  if (label === "Submitted") return "submitted";
  if (label === "Approved") return "approved";
  return "other";
}
