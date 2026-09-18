"use client";

import { useEffect, useState } from "react";
import { fetchSalesDataDocument } from "@/lib/api/upload-client";

interface SalesDataDocumentModalProps {
  documentId: string;
  onClose: () => void;
}

/**
 * Portal-equivalent of DocumentViewWrapperSalesData:
 * GET /document-view?document_id= → blob → in-app PDF/image viewer, else download.
 */
export function SalesDataDocumentModal({ documentId, onClose }: SalesDataDocumentModalProps) {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [url, setUrl] = useState<string | null>(null);
  const [contentType, setContentType] = useState<string | null>(null);
  const [fileName, setFileName] = useState("document");

  useEffect(() => {
    let objectUrl: string | null = null;
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetchSalesDataDocument(documentId)
      .then((doc) => {
        if (cancelled) {
          URL.revokeObjectURL(doc.url);
          return;
        }
        objectUrl = doc.url;
        setUrl(doc.url);
        setContentType(doc.contentType);
        setFileName(doc.fileName);
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Document not found");
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [documentId]);

  const mime = (contentType ?? "").toLowerCase();
  const isPdf = mime.includes("pdf") || /\.pdf$/i.test(fileName);
  const isImage = mime.startsWith("image/") || /\.(png|jpe?g|webp)$/i.test(fileName);

  return (
    <div
      className="fixed inset-0 z-[80] flex items-center justify-center bg-black/70 p-4"
      onClick={onClose}
      role="presentation"
    >
      <div
        className="relative w-full max-w-3xl max-h-[85vh] rounded-2xl border border-white/15 bg-[#12141f] shadow-[0_24px_60px_rgba(0,0,0,0.55)] overflow-hidden flex flex-col"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-label="Supporting document"
      >
        <div className="flex items-center justify-between gap-3 px-4 py-3 border-b border-white/10">
          <p className="text-sm font-semibold text-white truncate">{fileName}</p>
          <div className="flex items-center gap-2 flex-shrink-0">
            {url && (
              <a
                href={url}
                download={fileName}
                className="text-xs px-3 py-1.5 rounded-lg border border-white/15 text-white/80 hover:bg-white/10"
              >
                Download
              </a>
            )}
            <button
              type="button"
              onClick={onClose}
              className="text-xs px-3 py-1.5 rounded-lg border border-white/15 text-white/80 hover:bg-white/10"
            >
              Close
            </button>
          </div>
        </div>
        <div className="flex-1 min-h-[55vh] bg-black/30 flex items-center justify-center">
          {loading && <p className="text-sm text-white/50">Loading document…</p>}
          {!loading && error && (
            <p className="text-sm text-red-200 px-6 text-center">{error}</p>
          )}
          {!loading && !error && url && isPdf && (
            <iframe title={fileName} src={url} className="w-full h-[55vh] border-0 bg-white" />
          )}
          {!loading && !error && url && isImage && (
            // eslint-disable-next-line @next/next/no-img-element
            <img src={url} alt={fileName} className="max-h-[55vh] max-w-full object-contain" />
          )}
          {!loading && !error && url && !isPdf && !isImage && (
            <p className="text-sm text-white/60">Preview not supported — use Download.</p>
          )}
        </div>
      </div>
    </div>
  );
}
