/**
 * lifecycle-eval.spec.ts — FM_REVIEW + RDD_REVIEW + Full Lifecycle evaluation.
 *
 * Drives Scenarios S34–S47 (lifecycle_scenarios.py) against the live backend.
 * Unlike agent-eval.spec.ts which runs single-phase CREATE_SR scenarios,
 * this spec handles multi-phase, multi-user lifecycle scenarios:
 *
 *   S34–S39  → FM_REVIEW phase (FM Manager / Operations)
 *   S40–S45  → RDD_REVIEW phase (DD Engineer)
 *   S46–S47  → Full 3-phase lifecycle (CREATE → FM → RDD → COMPLETE)
 *
 * Architecture:
 *   - Each phase authenticates as a different seeded user (JWT)
 *   - SR ID captured from CREATE_SR phase is passed to FM/RDD phases via `sr_id`
 *   - Before FM/RDD approval, a mock PDF is uploaded via multipart POST
 *   - Hard assertions (wrong workflow_stage, wrong ui.type) fail the scenario
 *
 * Run:
 *   npx playwright test --config playwright.live.config.ts lifecycle-eval.spec.ts
 *   npx playwright test --config playwright.live.config.ts lifecycle-eval.spec.ts --grep "S46"
 */

import { test, expect, TestInfo } from "@playwright/test";
import { execSync } from "node:child_process";
import * as path from "node:path";
import * as fs from "node:fs";

// ─── Constants ────────────────────────────────────────────────────────────────

const BACKEND_URL = process.env.BACKEND_URL ?? "http://127.0.0.1:8000";
const CHAT_ENDPOINT   = `${BACKEND_URL}/api/chat/service-request`;
const AUTH_ENDPOINT   = `${BACKEND_URL}/api/auth/login`;
const UPLOAD_ENDPOINT = `${BACKEND_URL}/api/v1/upload`;
const HEALTH_ENDPOINT = `${BACKEND_URL}/api/v1/health`;
const RESET_ENDPOINT  = `${BACKEND_URL}/dev/mock/reset`;

const TURN_TIMEOUT = 90_000;
const SLOW_TURN_MS = 15_000;

// ─── Types (mirrors lifecycle_scenarios.py) ───────────────────────────────────

interface Turn {
  message: string;
  note: string;
  expect_ui_type?: string | null;
  expect_workflow_stage?: string | null;
  expect_keywords?: string[];
  expect_no_submit?: boolean;
  action?: string | null;
  corrected_fields?: Record<string, unknown> | null;
  selected_lease_id?: string | null;
}

interface PhaseConfig {
  user_email: string;
  user_password: string;
  role: string;
  stage: string;
  turns: Turn[];
  open_existing_sr: boolean;
  upload_mock_document: boolean;
  document_type: string;
}

interface LifecycleScenario {
  id: number;
  name: string;
  goal: string;
  phases: PhaseConfig[];
  enabled: boolean;
  tags: string[];
}

// ─── Load scenarios from Python ───────────────────────────────────────────────

const LIFECYCLE_JSON_PATH = path.resolve(__dirname, "lifecycle_scenarios.json");
const BACKEND_DIR = path.resolve(__dirname, "../../../../../backend");

let _cache: LifecycleScenario[] | null = null;

function loadLifecycleScenarios(): LifecycleScenario[] {
  if (_cache) return _cache;

  // Try Python
  try {
    const pythonBin = process.env.PYTHON_BIN ?? "python";
    const script = path.join(BACKEND_DIR, "_dump_lifecycle.py");
    const dumpScript = [
      "import json, sys, dataclasses",
      "sys.path.insert(0, '.')",
      "from tests.eval.lifecycle_scenarios import LIFECYCLE_SCENARIOS",
      "print(json.dumps([dataclasses.asdict(s) for s in LIFECYCLE_SCENARIOS]))",
    ].join("\n");
    fs.writeFileSync(script, dumpScript);
    const raw = execSync(`${pythonBin} "${script}"`, {
      cwd: BACKEND_DIR, timeout: 15_000, encoding: "utf-8",
      shell: process.platform === "win32" ? "cmd.exe" : "/bin/bash",
    });
    fs.unlinkSync(script);
    _cache = JSON.parse(raw.trim()) as LifecycleScenario[];
    return _cache;
  } catch { /* fall through */ }

  // Try pre-generated JSON
  if (fs.existsSync(LIFECYCLE_JSON_PATH)) {
    try {
      _cache = JSON.parse(fs.readFileSync(LIFECYCLE_JSON_PATH, "utf-8")) as LifecycleScenario[];
      return _cache;
    } catch { /* fall through */ }
  }

  console.warn("[lifecycle-eval] Could not load lifecycle scenarios. Run: generate-lifecycle-scenarios");
  _cache = [];
  return _cache;
}

// ─── HTTP helpers ─────────────────────────────────────────────────────────────

async function authenticate(
  request: import("@playwright/test").APIRequestContext,
  email: string,
  password: string,
): Promise<{ token: string; userId: string } | null> {
  const res = await request.post(AUTH_ENDPOINT, {
    data: { username: email, password },
    timeout: 15_000,
  });
  if (!res.ok()) return null;
  const body = await res.json() as { access_token: string; user_id: string };
  return { token: body.access_token, userId: body.user_id };
}

async function postTurn(
  request: import("@playwright/test").APIRequestContext,
  turn: Turn,
  sessionId: string | null,
  token: string,
  userId: string,
  srId: string | null,
): Promise<{ body: Record<string, unknown>; latencyMs: number }> {
  const payload: Record<string, unknown> = {
    user_id: userId,
    message: turn.message,
    attachments: [],
  };
  if (sessionId) payload.session_id = sessionId;
  if (turn.action) payload.action = turn.action;
  if (turn.corrected_fields) payload.corrected_fields = turn.corrected_fields;
  if (turn.selected_lease_id) payload.selected_lease_id = turn.selected_lease_id;
  if (srId) payload.sr_id = srId;

  const t0 = Date.now();
  const res = await request.post(CHAT_ENDPOINT, {
    data: payload,
    headers: { Authorization: `Bearer ${token}` },
    timeout: TURN_TIMEOUT,
  });
  const latencyMs = Date.now() - t0;
  expect(res.ok(), `HTTP ${res.status()} on turn: "${turn.message.slice(0, 60)}"`).toBeTruthy();
  return { body: await res.json() as Record<string, unknown>, latencyMs };
}

const MINIMAL_PDF = Buffer.from(
  "%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj " +
  "2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj " +
  "3 0 obj<</Type/Page/MediaBox[0 0 612 792]>>endobj\n" +
  "xref\n0 4\n0000000000 65535 f\n0000000009 00000 n\n" +
  "0000000058 00000 n\n0000000115 00000 n\n" +
  "trailer<</Size 4/Root 1 0 R>>\nstartxref\n190\n%%EOF",
);

async function uploadMockDocument(
  request: import("@playwright/test").APIRequestContext,
  sessionId: string,
  srId: string,
  documentType: string,
  token: string,
): Promise<boolean> {
  try {
    const res = await request.post(UPLOAD_ENDPOINT, {
      headers: { Authorization: `Bearer ${token}` },
      multipart: {
        document_type: documentType,
        session_id: sessionId,
        sr_id: srId,
        file: { name: "mock.pdf", mimeType: "application/pdf", buffer: MINIMAL_PDF },
      },
      timeout: 30_000,
    });
    return res.ok();
  } catch {
    return false;
  }
}

// ─── Assertion helpers ────────────────────────────────────────────────────────

const CONVERSATIONAL = new Set(["message", "text_question"]);

function assertTurn(
  turn: Turn,
  body: Record<string, unknown>,
): { hardFailures: string[]; softWarnings: string[] } {
  const hardFailures: string[] = [];
  const softWarnings: string[] = [];

  const ui = (body.ui ?? {}) as Record<string, unknown>;
  const state = (body.state ?? {}) as Record<string, unknown>;
  const uiType = (ui.type as string) ?? "";
  const workflowStage = (state.workflow_stage as string) ?? "";
  const msg = (body.message as string) ?? "";

  if (turn.expect_ui_type != null) {
    const exp = turn.expect_ui_type;
    if (exp === "message") {
      if (!CONVERSATIONAL.has(uiType)) {
        hardFailures.push(`ui.type: expected conversational, got '${uiType || "(empty)"}'`);
      }
    } else if (uiType !== exp) {
      hardFailures.push(`ui.type: expected '${exp}', got '${uiType || "(empty)"}'`);
    }
  }

  if (turn.expect_workflow_stage != null && workflowStage !== turn.expect_workflow_stage) {
    hardFailures.push(
      `workflow_stage: expected '${turn.expect_workflow_stage}', got '${workflowStage || "(empty)"}'`,
    );
  }

  if (turn.expect_no_submit && state.ready_to_submit === true) {
    hardFailures.push("ready_to_submit: expected false but got true");
  }

  if (turn.expect_keywords?.length) {
    const lower = msg.toLowerCase();
    if (!turn.expect_keywords.some((kw) => lower.includes(kw.toLowerCase()))) {
      softWarnings.push(`Keywords ${JSON.stringify(turn.expect_keywords)} not found. Got: "${msg.slice(0, 120)}"`);
    }
  }

  return { hardFailures, softWarnings };
}

// ─── SR ID extraction ─────────────────────────────────────────────────────────

const UUID_RE = /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/i;

function extractSrId(body: Record<string, unknown>, currentSrId: string | null): string | null {
  const state = (body.state ?? {}) as Record<string, unknown>;
  const fromState = state.sr_id as string | null;
  if (fromState) return fromState;

  const fromBody = body.sr_id as string | null;
  if (fromBody) return fromBody;

  const msg = (body.message as string) ?? "";
  const m = msg.match(UUID_RE);
  if (m) return m[0];

  return currentSrId;
}

// ─── Session ID manifest ──────────────────────────────────────────────────────

const MANIFEST_PATH = path.resolve(
  __dirname,
  "../../../../playwright-report-live/lifecycle-session-ids.json",
);

const _manifestEntries: Array<{
  scenario_id: number;
  scenario_name: string;
  phase_idx: number;
  phase_role: string;
  session_id: string;
  sr_id: string;
  passed: boolean;
}> = [];

function appendToManifest(entry: (typeof _manifestEntries)[number]): void {
  _manifestEntries.push(entry);
  try {
    fs.mkdirSync(path.dirname(MANIFEST_PATH), { recursive: true });
    let existing: typeof _manifestEntries = [];
    if (fs.existsSync(MANIFEST_PATH)) {
      try { existing = JSON.parse(fs.readFileSync(MANIFEST_PATH, "utf-8")); } catch { /* ignore */ }
    }
    const key = `${entry.scenario_id}-${entry.phase_idx}`;
    const merged = [
      ...existing.filter((e) => `${e.scenario_id}-${e.phase_idx}` !== key),
      entry,
    ];
    fs.writeFileSync(MANIFEST_PATH, JSON.stringify(merged, null, 2));
  } catch { /* best effort */ }
}

// ─── Health check ─────────────────────────────────────────────────────────────

test.beforeAll(async ({ request }) => {
  const res = await request.get(HEALTH_ENDPOINT, { timeout: 10_000 });
  expect(res.ok(), `Backend unreachable at ${BACKEND_URL}`).toBeTruthy();
});

// ─── Generate one test per scenario ───────────────────────────────────────────

const scenarios = loadLifecycleScenarios().filter((s) => s.enabled);

for (const scenario of scenarios) {
  test(`S${scenario.id}: ${scenario.name}`, async ({ request }, testInfo) => {
    testInfo.annotations.push({ type: "goal",      description: scenario.goal });
    testInfo.annotations.push({ type: "tags",      description: scenario.tags.join(", ") });
    testInfo.annotations.push({ type: "phases",    description: scenario.phases.map((p) => p.stage).join(" → ") });

    // Reset mock state
    await request.post(RESET_ENDPOINT, { timeout: 5_000 }).catch(() => { /* ignore */ });

    let capturedSrId: string | null = null;
    const allSoftWarnings: string[] = [];

    for (let phaseIdx = 0; phaseIdx < scenario.phases.length; phaseIdx++) {
      const phase = scenario.phases[phaseIdx];

      // Authenticate as this phase's user
      const auth = await authenticate(request, phase.user_email, phase.user_password);
      expect(auth, `Authentication failed for ${phase.user_email}`).not.toBeNull();
      const { token, userId } = auth!;

      let sessionId: string | null = null;
      const turnLog: string[] = [];
      let docUploaded = false;

      for (let turnIdx = 0; turnIdx < phase.turns.length; turnIdx++) {
        const turn = phase.turns[turnIdx];
        const turnNum = turnIdx + 1;

        // Upload doc after first turn (so we have sessionId + srId)
        if (phase.upload_mock_document && !docUploaded && sessionId && capturedSrId) {
          const uploaded = await uploadMockDocument(
            request, sessionId, capturedSrId, phase.document_type, token,
          );
          if (!uploaded) {
            allSoftWarnings.push(`Phase ${phaseIdx + 1} doc upload failed — approval may be blocked`);
          }
          docUploaded = true;
        }

        // Determine sr_id to pass
        const srIdForTurn = (phaseIdx > 0 && turnIdx === 0) ? capturedSrId : capturedSrId;

        const { body, latencyMs } = await postTurn(
          request, turn, sessionId, token, userId, srIdForTurn,
        );

        // Update session + sr_id
        sessionId = (body.session_id as string) ?? sessionId;
        capturedSrId = extractSrId(body, capturedSrId);

        const { hardFailures, softWarnings } = assertTurn(turn, body);
        if (latencyMs > SLOW_TURN_MS) {
          allSoftWarnings.push(`P${phaseIdx + 1}T${turnNum}: slow ${latencyMs}ms`);
        }
        if (softWarnings.length) {
          allSoftWarnings.push(...softWarnings.map((w) => `P${phaseIdx + 1}T${turnNum}: ${w}`));
        }

        const state = (body.state ?? {}) as Record<string, unknown>;
        turnLog.push(
          `P${phaseIdx + 1}T${turnNum} [${hardFailures.length ? "FAIL" : "PASS"}] ` +
          `${phase.role} "${turn.message.slice(0, 40)}" ` +
          `stage=${state.workflow_stage ?? "?"} ui=${(body.ui as Record<string, unknown>)?.type ?? "?"} ${latencyMs}ms`,
        );

        if (hardFailures.length > 0) {
          const failMsg = hardFailures.join("; ");
          testInfo.annotations.push({
            type: "failure",
            description: `Phase ${phaseIdx + 1} Turn ${turnNum}: ${failMsg}`,
          });
          testInfo.annotations.push({ type: "turn_log", description: turnLog.join("\n") });
          if (sessionId) {
            appendToManifest({
              scenario_id: scenario.id, scenario_name: scenario.name,
              phase_idx: phaseIdx + 1, phase_role: phase.role,
              session_id: sessionId, sr_id: capturedSrId ?? "",
              passed: false,
            });
          }
          expect.soft(hardFailures, `P${phaseIdx + 1}T${turnNum}: ${failMsg}`).toHaveLength(0);
          throw new Error(`S${scenario.id} failed at Phase ${phaseIdx + 1} Turn ${turnNum}: ${failMsg}`);
        }
      }

      // Record session for this phase
      if (sessionId) {
        appendToManifest({
          scenario_id: scenario.id, scenario_name: scenario.name,
          phase_idx: phaseIdx + 1, phase_role: phase.role,
          session_id: sessionId, sr_id: capturedSrId ?? "",
          passed: true,
        });
      }
    }

    testInfo.annotations.push({ type: "turn_log",    description: _manifestEntries.filter(e => e.scenario_id === scenario.id).map(e => `P${e.phase_idx}[${e.phase_role}] sid=${e.session_id.slice(0, 8)}`).join(" | ") });
    testInfo.annotations.push({ type: "sr_id",        description: capturedSrId ?? "none" });
    if (allSoftWarnings.length) {
      testInfo.annotations.push({ type: "soft_warnings", description: allSoftWarnings.join("\n") });
    }
  });
}

// ─── Summary ──────────────────────────────────────────────────────────────────

test("@lifecycle-summary: export session observability", async ({ request }, testInfo) => {
  const summaryPath = path.resolve(
    __dirname,
    "../../../../playwright-report-live/lifecycle-summary.json",
  );
  fs.mkdirSync(path.dirname(summaryPath), { recursive: true });
  fs.writeFileSync(summaryPath, JSON.stringify({
    timestamp: new Date().toISOString(),
    backend_url: BACKEND_URL,
    scenario_count: scenarios.length,
    sessions: _manifestEntries,
  }, null, 2));

  testInfo.annotations.push({
    type: "sessions_collected",
    description: `${_manifestEntries.length} phase sessions captured`,
  });

  const health = await request.get(HEALTH_ENDPOINT);
  expect(health.ok()).toBeTruthy();
});
