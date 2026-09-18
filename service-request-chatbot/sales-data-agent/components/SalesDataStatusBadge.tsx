"use client";

import {
  salesDataAdminLabel,
  salesDataAdminTooltip,
  salesDataBadgeTone,
} from "@/lib/salesDataStatusDisplay";

const TONE_CLASS: Record<string, string> = {
  pending: "border-amber-400/40 text-amber-100",
  submitted: "border-sky-400/40 text-sky-100",
  approved: "border-emerald-400/40 text-emerald-100",
  other: "border-white/20 text-white/80",
};

const DOT_CLASS: Record<string, string> = {
  pending: "bg-amber-400",
  submitted: "bg-sky-400",
  approved: "bg-emerald-400",
  other: "bg-white/50",
};

interface SalesDataStatusBadgeProps {
  status: string | null | undefined;
  compact?: boolean;
}

/** Portal-style admin status: collapsed badge + hover tooltip for the real enum. */
export function SalesDataStatusBadge({ status, compact = false }: SalesDataStatusBadgeProps) {
  const label = salesDataAdminLabel(status);
  const tooltip = salesDataAdminTooltip(status);
  const tone = salesDataBadgeTone(status);

  return (
    <span
      title={tooltip}
      aria-label={tooltip}
      className={`inline-flex items-center gap-1.5 rounded-full border whitespace-nowrap ${
        TONE_CLASS[tone]
      } ${compact ? "text-[10px] px-1.5 py-0.5" : "text-[11px] px-2 py-0.5"}`}
    >
      <span className={`rounded-full ${DOT_CLASS[tone]} ${compact ? "h-1.5 w-1.5" : "h-1.5 w-1.5"}`} />
      {label}
    </span>
  );
}
