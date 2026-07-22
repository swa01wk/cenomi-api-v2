"use client";

import type { ResponseUIExistingHandover } from "@/lib/types/chat";

type Props = {
  data: ResponseUIExistingHandover;
};

export function ExistingHandoverCard({ data }: Props) {
  return (
    <div className="rounded-lg border border-sky-200 bg-sky-50 p-4 dark:border-sky-800/40 dark:bg-sky-950/20">
      <h2 className="mb-1 text-xs font-semibold uppercase tracking-wide text-sky-800 dark:text-sky-400">
        Handover already exists
      </h2>
      {data.leaseCode && (
        <p className="mb-1 text-xs text-sky-700 dark:text-sky-500">
          Lease {data.leaseCode}
        </p>
      )}
      <p className="mb-3 text-sm font-medium text-sky-900 dark:text-sky-100">
        Service Request #{data.srId}
      </p>
      <a
        href={data.detailsUrl}
        target="_blank"
        rel="noopener noreferrer"
        className="inline-flex items-center rounded-md bg-sky-600 px-3 py-2 text-xs font-medium text-white transition hover:bg-sky-700 focus:outline-none focus:ring-2 focus:ring-sky-500/40"
      >
        View existing SR
      </a>
    </div>
  );
}
