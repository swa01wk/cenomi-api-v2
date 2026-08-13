"use client";

import { useState, useRef } from "react";
import { deleteDocument, uploadDocument } from "@/lib/api/upload-client";
import type { UploadedDoc } from "@/lib/types/chat";

// ─── Document type configuration ─────────────────────────────────────────────

type DocTypeConfig = {
  id: string;
  label: string;
};

const FM_DOC_TYPES: DocTypeConfig[] = [
  { id: "SR_HANDOVER_CHECKLIST", label: "Handover Checklist" },
  { id: "SR_HANDOVER_SITE_SURVEY", label: "Site Survey" },
  { id: "SR_COP_CHECKLIST_OTHER", label: "Other" },
];

const RDD_DOC_TYPES: DocTypeConfig[] = [
  { id: "DR_SR_HANDOVER_REPORT", label: "Handover Meeting Report" },
  { id: "SR_REJECTED_HANDOVER_REPORT", label: "Rejected Fitout and Handover Report" },
  { id: "SR_HANDOVER_OTHER", label: "Other" },
];

const MM_DOC_TYPES: DocTypeConfig[] = [
  { id: "SR_HANDOVER_OTHER", label: "Other (optional)" },
];

const MM_HANDED_OVER_DOC_TYPES: DocTypeConfig[] = [
  { id: "SR_HANDED_OVER_OTHER", label: "Other (optional)" },
];

function getDocTypes(
  userRole: string | null | undefined,
  workflowStage: string | null | undefined,
  subCategory?: string | null | undefined,
): DocTypeConfig[] {
  if (workflowStage === "CREATE_HANDED_OVER_SR" || subCategory === "HANDED_OVER") {
    return MM_HANDED_OVER_DOC_TYPES;
  }
  if (workflowStage === "CREATE_SR") return MM_DOC_TYPES;
  if (userRole === "FM_MANAGER" || userRole === "OPERATIONS") return FM_DOC_TYPES;
  if (userRole === "DD_ENGINEER") return RDD_DOC_TYPES;
  if (userRole === "MALL_MANAGER") return MM_DOC_TYPES;
  return [];
}

function getDocLabel(docTypeId: string): string {
  return (
    [...FM_DOC_TYPES, ...RDD_DOC_TYPES, ...MM_DOC_TYPES, ...MM_HANDED_OVER_DOC_TYPES].find(
      (d) => d.id === docTypeId,
    )?.label ?? docTypeId
  );
}

// ─── Props ────────────────────────────────────────────────────────────────────

interface DocumentUploadPanelProps {
  sessionId: string;
  srId: string | null;
  workflowStage: string | null | undefined;
  userRole: string | null | undefined;
  /** "HANDED_OVER" for Path B (Already Trading); "HANDOVER" or null for Path A. */
  subCategory?: string | null | undefined;
  uploadedDocuments: UploadedDoc[];
  onUploadComplete: (doc: UploadedDoc) => void;
  onDeleteComplete?: (documentId: string) => void;
  /** When true, in-chat DocumentRequirementCard owns upload UX — hide this panel. */
  preferChatDocumentCard?: boolean;
  /** Collected SR fields — used to gate RDD upload until dates are set. */
  collectedData?: Record<string, string | undefined> | null;
}

// ─── Component ────────────────────────────────────────────────────────────────

export function DocumentUploadPanel({
  sessionId,
  srId,
  workflowStage,
  userRole,
  subCategory = null,
  uploadedDocuments,
  onUploadComplete,
  onDeleteComplete,
  preferChatDocumentCard = false,
  collectedData = null,
}: DocumentUploadPanelProps) {
  const [selectedDocType, setSelectedDocType] = useState<string>("");
  const [comment, setComment] = useState("");
  const [isUploading, setIsUploading] = useState(false);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [uploadSuccess, setUploadSuccess] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const isPathB = subCategory === "HANDED_OVER" || workflowStage === "CREATE_HANDED_OVER_SR";
  const docTypes = getDocTypes(userRole, workflowStage, subCategory);
  const rddDatesReady =
    Boolean(collectedData?.actual_handover_date) &&
    Boolean(collectedData?.fitout_start_date) &&
    Boolean(collectedData?.fitout_end_date);
  const leaseReady = Boolean(collectedData?.lease_id || collectedData?.lease_code);
  const mmOptionalUploadReady =
    collectedData?.mm_optional_upload_status === "awaiting_upload";
  const pathBUploadReady = isPathB && leaseReady;
  const shouldShow =
    !preferChatDocumentCard &&
    docTypes.length > 0 &&
    ((workflowStage === "FM_REVIEW" && srId !== null) ||
      (workflowStage === "RDD_REVIEW" && rddDatesReady && srId !== null) ||
      (workflowStage === "CREATE_SR" && leaseReady && mmOptionalUploadReady) ||
      (isPathB && pathBUploadReady));

  if (!shouldShow) return null;

  async function handleFileChange(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file || !selectedDocType) return;
    // CREATE_SR and CREATE_HANDED_OVER_SR may upload without srId once lease is resolved.
    if (workflowStage !== "CREATE_SR" && !isPathB && !srId) return;

    setIsUploading(true);
    setUploadError(null);
    setUploadSuccess(null);

    try {
      const result = await uploadDocument(
        file,
        selectedDocType,
        sessionId,
        srId,
        comment,
      );
      const label = getDocLabel(selectedDocType);
      onUploadComplete({
        documentId: result.document_id,
        documentType: selectedDocType,
        documentTypeLabel: label,
        filename: file.name,
        signedUrl: result.signed_url,
      });
      setUploadSuccess(`${file.name} uploaded as ${label}.`);
      setSelectedDocType("");
      setComment("");
      if (fileInputRef.current) fileInputRef.current.value = "";
    } catch (err) {
      setUploadError(err instanceof Error ? err.message : "Upload failed. Please try again.");
    } finally {
      setIsUploading(false);
    }
  }

  async function handleDelete(doc: UploadedDoc) {
    if (!doc.documentId) return;
    setDeletingId(doc.documentId);
    setUploadError(null);
    try {
      await deleteDocument(doc.documentId, sessionId, srId);
      onDeleteComplete?.(doc.documentId);
      setUploadSuccess(`${doc.filename} removed.`);
    } catch (err) {
      setUploadError(err instanceof Error ? err.message : "Delete failed.");
    } finally {
      setDeletingId(null);
    }
  }

  return (
    <div className="rounded-lg border border-zinc-200 bg-white p-4 dark:border-zinc-700 dark:bg-zinc-900">
      <h3 className="mb-3 text-sm font-semibold text-zinc-700 dark:text-zinc-300">
        {workflowStage === "CREATE_SR" || isPathB ? "Optional Documents" : "Upload Documents"}
      </h3>

      <div className="flex flex-col gap-2">
        <select
          value={selectedDocType}
          onChange={(e) => setSelectedDocType(e.target.value)}
          disabled={isUploading}
          className="w-full rounded-md border border-zinc-300 bg-white px-3 py-2 text-sm text-zinc-700 focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500 disabled:opacity-50 dark:border-zinc-600 dark:bg-zinc-800 dark:text-zinc-200"
        >
          <option value="">Select document type…</option>
          {docTypes.map((dt) => (
            <option key={dt.id} value={dt.id}>
              {dt.label}
            </option>
          ))}
        </select>

        <input
          type="text"
          value={comment}
          onChange={(e) => setComment(e.target.value)}
          disabled={isUploading}
          placeholder="Comment (optional)"
          className="w-full rounded-md border border-zinc-300 bg-white px-3 py-2 text-sm text-zinc-700 focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500 disabled:opacity-50 dark:border-zinc-600 dark:bg-zinc-800 dark:text-zinc-200"
        />

        <label
          className={`inline-flex w-fit cursor-pointer rounded-md border px-4 py-2 text-sm font-medium transition-colors ${
            selectedDocType && !isUploading
              ? "border-blue-600 bg-blue-600 text-white hover:bg-blue-700"
              : "cursor-not-allowed border-zinc-300 bg-zinc-100 text-zinc-400 dark:border-zinc-600 dark:bg-zinc-700 dark:text-zinc-500"
          }`}
        >
          {isUploading ? "Uploading…" : "Choose file"}
          <input
            ref={fileInputRef}
            type="file"
            className="hidden"
            disabled={!selectedDocType || isUploading}
            onChange={handleFileChange}
          />
        </label>
      </div>

      {uploadError && (
        <p className="mt-2 text-xs text-red-600 dark:text-red-400">{uploadError}</p>
      )}

      {uploadSuccess && (
        <p className="mt-2 text-xs text-green-600 dark:text-green-400">{uploadSuccess}</p>
      )}

      {uploadedDocuments.length > 0 && (
        <div className="mt-4">
          <p className="mb-1.5 text-xs font-medium text-zinc-500 dark:text-zinc-400">
            Attached ({uploadedDocuments.length})
          </p>
          <ul className="space-y-1.5">
            {uploadedDocuments.map((doc, idx) => (
              <li
                key={doc.documentId ?? `${doc.documentType}-${idx}`}
                className="flex items-center gap-2 rounded-md border border-green-200 bg-green-50 px-3 py-2 text-xs dark:border-green-900 dark:bg-green-950/40"
              >
                <span className="text-green-600 dark:text-green-400">✓</span>
                <span className="flex-1 truncate font-medium text-zinc-700 dark:text-zinc-200">
                  {doc.filename}
                </span>
                <span className="shrink-0 rounded bg-green-100 px-1.5 py-0.5 text-green-800 dark:bg-green-900 dark:text-green-200">
                  {doc.documentTypeLabel}
                </span>
                {doc.documentId ? (
                  <button
                    type="button"
                    disabled={deletingId === doc.documentId}
                    onClick={() => void handleDelete(doc)}
                    className="shrink-0 text-red-600 underline disabled:opacity-50"
                  >
                    {deletingId === doc.documentId ? "…" : "Delete"}
                  </button>
                ) : null}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
