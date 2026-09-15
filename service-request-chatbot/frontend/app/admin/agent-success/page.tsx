"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import {
  getMetricsFeedback,
  getMetricsFunnel,
  getMetricsSummary,
  getMetricsUsers,
  type MetricsWindow,
} from "@/lib/api/observability-client";
import type { MetricsSummary } from "@/lib/types/observability";

const PILOT_TARGETS = [
  { kpi: "SR completion rate", target: "≥ 60%" },
  { kpi: "Usefulness (USEFUL / all FLOW_USEFUL)", target: "≥ 70%" },
  { kpi: "Feedback coverage on submitted SRs", target: "≥ 30%" },
  { kpi: "Graph + tool error rate", target: "< 2%" },
  { kpi: "p95 turn latency", target: "< 8000 ms" },
] as const;

type FunnelStep = { key: string; count: number };
type DropOff = { workflow_stage: string; last_sessions: number };
type UserRow = Record<string, number | string>;
type ReasonRow = { reason: string; count: number };

function pct(value: number | undefined): string {
  if (value === undefined || Number.isNaN(value)) return "—";
  return `${(value * 100).toFixed(1)}%`;
}

function num(value: number | undefined, digits = 0): string {
  if (value === undefined || Number.isNaN(value)) return "—";
  return digits > 0 ? value.toFixed(digits) : Math.round(value).toLocaleString();
}

function usd(value: number | undefined): string {
  if (value === undefined || Number.isNaN(value)) return "—";
  return `$${value.toFixed(4)}`;
}

export default function AgentSuccessPage() {
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [summary, setSummary] = useState<MetricsSummary | null>(null);
  const [funnel, setFunnel] = useState<{ steps: FunnelStep[]; drop_off_by_stage: DropOff[] } | null>(
    null,
  );
  const [users, setUsers] = useState<{ items: UserRow[]; total: number } | null>(null);
  const [feedback, setFeedback] = useState<{
    thumbs_up: number;
    thumbs_down: number;
    thumbs_up_rate: number;
    useful: number;
    not_useful: number;
    usefulness_rate: number;
    csat_avg: number | null;
    csat_n: number;
    not_useful_reasons: ReasonRow[];
  } | null>(null);

  const windowFilter: MetricsWindow | undefined = useMemo(() => {
    if (!from && !to) return undefined;
    return {
      from: from ? new Date(`${from}T00:00:00Z`).toISOString() : undefined,
      to: to ? new Date(`${to}T23:59:59Z`).toISOString() : undefined,
    };
  }, [from, to]);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const results = await Promise.allSettled([
        getMetricsSummary(windowFilter),
        getMetricsFunnel(windowFilter),
        getMetricsUsers(windowFilter),
        getMetricsFeedback(windowFilter),
      ]);
      const labels = ["summary", "funnel", "users", "feedback"] as const;
      const failures = results
        .map((r, i) => (r.status === "rejected" ? `${labels[i]}: ${String(r.reason)}` : null))
        .filter((x): x is string => x !== null);
      if (results[0].status === "fulfilled") setSummary(results[0].value);
      if (results[1].status === "fulfilled") setFunnel(results[1].value);
      if (results[2].status === "fulfilled") setUsers(results[2].value);
      if (results[3].status === "fulfilled") setFeedback(results[3].value);
      setError(failures.length ? failures.join(" · ") : null);
    } finally {
      setLoading(false);
    }
  }, [windowFilter]);

  useEffect(() => {
    void load();
  }, [load]);

  const cards: { label: string; value: string }[] = [
    { label: "SR completion", value: pct(summary?.sr_completion_rate) },
    { label: "Usefulness", value: pct(summary?.usefulness_rate) },
    { label: "Feedback coverage", value: pct(summary?.feedback_coverage) },
    { label: "Cost / successful SR", value: usd(summary?.cost_per_successful_sr) },
    { label: "p95 latency", value: `${num(summary?.p95_latency_ms)} ms` },
    { label: "Idle abandoned", value: num(summary?.idle_abandoned) },
    { label: "Total tokens", value: num(summary?.total_tokens) },
    { label: "Total cost", value: usd(summary?.total_cost) },
  ];

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">AI Success</h1>
          <p className="mt-1 text-sm text-zinc-500 dark:text-zinc-400">
            Product scorecard for the service-request agent. Default window is the last 7 days.
            Trace debugging stays on{" "}
            <Link href="/admin/agent-observability" className="underline">
              Observability
            </Link>
            .
          </p>
        </div>
        <div className="flex flex-wrap items-end gap-2">
          <label className="text-xs text-zinc-500">
            From
            <input
              type="date"
              value={from}
              onChange={(e) => setFrom(e.target.value)}
              className="mt-1 block rounded-md border border-zinc-200 bg-white px-2 py-1 text-sm dark:border-zinc-700 dark:bg-zinc-900"
            />
          </label>
          <label className="text-xs text-zinc-500">
            To
            <input
              type="date"
              value={to}
              onChange={(e) => setTo(e.target.value)}
              className="mt-1 block rounded-md border border-zinc-200 bg-white px-2 py-1 text-sm dark:border-zinc-700 dark:bg-zinc-900"
            />
          </label>
          <button
            type="button"
            onClick={() => void load()}
            disabled={loading}
            className="rounded-md border border-zinc-200 bg-white px-3 py-1.5 text-sm text-zinc-700 hover:bg-zinc-50 disabled:opacity-50 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-300"
          >
            {loading ? "Loading…" : "Refresh"}
          </button>
        </div>
      </div>

      {error && (
        <p className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
          {error}
        </p>
      )}

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        {cards.map((card) => (
          <div
            key={card.label}
            className="rounded-lg border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900"
          >
            <p className="mb-1 text-xs font-medium uppercase tracking-wide text-zinc-500">
              {card.label}
            </p>
            <p className="text-xl font-semibold tabular-nums">{loading ? "—" : card.value}</p>
          </div>
        ))}
      </div>

      <section className="rounded-lg border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900">
        <h2 className="text-sm font-semibold">Pilot targets (reference, not enforced)</h2>
        <ul className="mt-2 space-y-1 text-sm text-zinc-600 dark:text-zinc-300">
          {PILOT_TARGETS.map((row) => (
            <li key={row.kpi} className="flex justify-between gap-4">
              <span>{row.kpi}</span>
              <span className="tabular-nums text-zinc-500">{row.target}</span>
            </li>
          ))}
        </ul>
        <p className="mt-3 text-xs text-zinc-400">
          Pass: completion and usefulness both ≥ target, errors &lt; 2%. Investigate: completion
          high, usefulness low. Fail: high abandon at confirm/create, low usefulness, unbounded
          cost per SR.
        </p>
      </section>

      <section>
        <h2 className="mb-2 text-sm font-semibold">Funnel</h2>
        <div className="overflow-x-auto rounded-lg border border-zinc-200 dark:border-zinc-800">
          <table className="w-full text-left text-sm">
            <thead className="bg-zinc-50 text-xs uppercase tracking-wide text-zinc-500 dark:bg-zinc-900">
              <tr>
                <th className="px-3 py-2">Step</th>
                <th className="px-3 py-2">Count</th>
                <th className="px-3 py-2">From previous</th>
              </tr>
            </thead>
            <tbody>
              {(funnel?.steps ?? []).map((step, i, arr) => {
                const prev = i === 0 ? null : arr[i - 1].count;
                const conv = prev && prev > 0 ? pct(step.count / prev) : "—";
                return (
                  <tr key={step.key} className="border-t border-zinc-100 dark:border-zinc-800">
                    <td className="px-3 py-2 font-mono text-xs">{step.key}</td>
                    <td className="px-3 py-2 tabular-nums">{step.count}</td>
                    <td className="px-3 py-2 tabular-nums">{conv}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </section>

      <section>
        <h2 className="mb-2 text-sm font-semibold">Drop-off by last stage (no SR submitted)</h2>
        <div className="overflow-x-auto rounded-lg border border-zinc-200 dark:border-zinc-800">
          <table className="w-full text-left text-sm">
            <thead className="bg-zinc-50 text-xs uppercase tracking-wide text-zinc-500 dark:bg-zinc-900">
              <tr>
                <th className="px-3 py-2">Workflow stage</th>
                <th className="px-3 py-2">Sessions</th>
              </tr>
            </thead>
            <tbody>
              {(funnel?.drop_off_by_stage ?? []).length === 0 ? (
                <tr>
                  <td className="px-3 py-3 text-zinc-400" colSpan={2}>
                    No drop-off rows in this window.
                  </td>
                </tr>
              ) : (
                (funnel?.drop_off_by_stage ?? []).map((row) => (
                  <tr key={row.workflow_stage} className="border-t border-zinc-100 dark:border-zinc-800">
                    <td className="px-3 py-2 font-mono text-xs">{row.workflow_stage}</td>
                    <td className="px-3 py-2 tabular-nums">{row.last_sessions}</td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </section>

      <section>
        <h2 className="mb-2 text-sm font-semibold">
          Per-user tokens {users ? `(${users.total})` : ""}
        </h2>
        <div className="overflow-x-auto rounded-lg border border-zinc-200 dark:border-zinc-800">
          <table className="w-full text-left text-sm">
            <thead className="bg-zinc-50 text-xs uppercase tracking-wide text-zinc-500 dark:bg-zinc-900">
              <tr>
                <th className="px-3 py-2">User</th>
                <th className="px-3 py-2">Sessions</th>
                <th className="px-3 py-2">Turns</th>
                <th className="px-3 py-2">Tokens</th>
                <th className="px-3 py-2">Cost</th>
                <th className="px-3 py-2">SRs</th>
                <th className="px-3 py-2">Useful</th>
              </tr>
            </thead>
            <tbody>
              {(users?.items ?? []).length === 0 ? (
                <tr>
                  <td className="px-3 py-3 text-zinc-400" colSpan={7}>
                    No users in this window.
                  </td>
                </tr>
              ) : (
                (users?.items ?? []).map((row) => (
                  <tr key={String(row.user_id)} className="border-t border-zinc-100 dark:border-zinc-800">
                    <td className="px-3 py-2 font-mono text-xs">{String(row.user_id).slice(0, 8)}…</td>
                    <td className="px-3 py-2 tabular-nums">{row.sessions}</td>
                    <td className="px-3 py-2 tabular-nums">{row.turns}</td>
                    <td className="px-3 py-2 tabular-nums">{row.total_tokens}</td>
                    <td className="px-3 py-2 tabular-nums">{usd(Number(row.total_cost))}</td>
                    <td className="px-3 py-2 tabular-nums">{row.sr_submitted}</td>
                    <td className="px-3 py-2 tabular-nums">
                      {row.useful}/{Number(row.useful) + Number(row.not_useful)}
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </section>

      <section>
        <h2 className="mb-2 text-sm font-semibold">Feedback</h2>
        <p className="mb-2 text-sm text-zinc-500">
          Thumbs {pct(feedback?.thumbs_up_rate)} up ({feedback?.thumbs_up ?? 0}/
          {(feedback?.thumbs_up ?? 0) + (feedback?.thumbs_down ?? 0)}) · Usefulness{" "}
          {pct(feedback?.usefulness_rate)} · CSAT{" "}
          {feedback?.csat_avg == null ? "—" : feedback.csat_avg.toFixed(1)} (n={feedback?.csat_n ?? 0})
        </p>
        <div className="overflow-x-auto rounded-lg border border-zinc-200 dark:border-zinc-800">
          <table className="w-full text-left text-sm">
            <thead className="bg-zinc-50 text-xs uppercase tracking-wide text-zinc-500 dark:bg-zinc-900">
              <tr>
                <th className="px-3 py-2">Not-useful reason</th>
                <th className="px-3 py-2">Count</th>
              </tr>
            </thead>
            <tbody>
              {(feedback?.not_useful_reasons ?? []).length === 0 ? (
                <tr>
                  <td className="px-3 py-3 text-zinc-400" colSpan={2}>
                    No not-useful reasons in this window.
                  </td>
                </tr>
              ) : (
                (feedback?.not_useful_reasons ?? []).map((row) => (
                  <tr key={row.reason} className="border-t border-zinc-100 dark:border-zinc-800">
                    <td className="px-3 py-2 font-mono text-xs">{row.reason}</td>
                    <td className="px-3 py-2 tabular-nums">{row.count}</td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}
