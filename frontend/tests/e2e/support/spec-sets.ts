/**
 * Which Playwright project runs which spec. Playwright matches `testMatch` and `testIgnore` against a spec's absolute
 * path, so a pattern that is not anchored also matches the folders above the tests: in a checkout under a folder
 * named `…-harness/`, `harness/.*\.spec\.ts` ignored every authenticated spec (WP65-PW). Each pattern here is anchored
 * on the e2e folder itself.
 */

const SEPARATOR = String.raw`[\\/]`;

function escaped(text: string): string {
  return text.replace(/[.*+?^${}()|[\]\\]/g, String.raw`\$&`);
}

/** The e2e folder's absolute path as a pattern, whichever separator the platform writes. */
function anchor(e2eDir: string): string {
  return escaped(e2eDir.replace(/[\\/]+$/, ''))
    .split(/\\\\|\//)
    .join(SEPARATOR);
}

/** A spec under the e2e folder: `relative` is a pattern over the path below it, `/` between folders. */
export function underE2e(e2eDir: string, relative: string): RegExp {
  return new RegExp(`^${anchor(e2eDir)}${SEPARATOR}${relative.split('/').join(SEPARATOR)}$`);
}

// Tests that need a real AI model; they run in the `ai` project.
const AI = [String.raw`chat/chat-cited-answer\.spec\.ts`, String.raw`chat/chat-link-labels\.spec\.ts`, String.raw`agents/.*\.spec\.ts`];

// The agent harness's browser specs (QA-5). They need the harness stack of deployment/e2e/run.sh (the scripted
// model and SaaS, the durable worker, the seeded agents), so their project exists only on that lane.
const HARNESS = [
  // A `harness/*-engine.spec.ts` needs engine runs, which only run.sh durable's stack has: it runs in
  // harness-engine alone (CJ-5, ANSWER-CONTRACT-1 ran in both and failed on the harness stack).
  String.raw`harness/(?!.*-engine\.spec\.ts$).*\.spec\.ts`,
  String.raw`chat/chat-approval-card\.spec\.ts`,
  String.raw`chat/chat-approval-batch-order\.spec\.ts`,
  String.raw`chat/chat-activity-panel\.spec\.ts`,
  String.raw`chat/chat-tool-names\.spec\.ts`,
  String.raw`chat/chat-timeline\.spec\.ts`,
  String.raw`chat/chat-durable-run-ui\.spec\.ts`,
  String.raw`chat/chat-code-task-card\.spec\.ts`,
  String.raw`chat/chat-engine-agent-account\.spec\.ts`,
  String.raw`workspace/ai-accounts\.spec\.ts`,
  String.raw`workspace/u4-manifest-approval\.spec\.ts`,
  String.raw`workspace/u4-schedules\.spec\.ts`,
  String.raw`workspace/ui3-trigger-settings\.spec\.ts`,
  // FIX-RAIL1: a running chat stays listed after New chat, on the scripted model.
  String.raw`chat/chat-running-stays-listed\.spec\.ts`,
  // QA-CLASSIC: the QA journeys across the classic chat, Needs you and Schedules, on the scripted model.
  String.raw`qa/journey-.*\.spec\.ts`,
  // QA-CLASSIC-FU-6: the agent build-and-chat journey runs on the scripted model too (it keeps its real-model run in `ai`).
  String.raw`agents/agent-create-chat\.spec\.ts`,
];

// A real engine run in a broker sandbox (run.sh durable): nightly, on its own stack.
const HARNESS_ENGINE = [
  String.raw`chat/chat-durable-engine-e2e\.spec\.ts`,
  // CJ-5: the classic jobs' code_task journeys (job card, panel, Stop, the skill step) on the real engine path.
  String.raw`harness/classic-jobs-engine\.spec\.ts`,
  // ANSWER-CONTRACT-1: the grounding question before a job, and a job's own question, on a default chat.
  String.raw`harness/answer-contract-engine\.spec\.ts`,
  // R19-FU-3: WP-89's workspace files and degradation notice on the real engine path.
  String.raw`harness/wp89-in-flight-engine\.spec\.ts`,
  // DP7-MVP-1: CF-07's ph round trip and CF-08's deny on the release engine.
  String.raw`harness/dp7-cf-engine\.spec\.ts`,
  // FIX-TOOLWORDS: a job call that asks first is one step in the classic Activity panel, in plain words.
  String.raw`chat/chat-engine-tool-words\.spec\.ts`,
  // QA-CLASSIC: code job → Jobs → Needs you → files, and how long a job takes to start, on the real engine path.
  String.raw`qa/engine-journey-.*\.spec\.ts`,
  String.raw`qa/latency-engine\.spec\.ts`,
];

// QA-CLASSIC (scripts/qa_full.py), each on the harness lane's stack: the UX captures against their baselines, the
// browser latency budgets, and the journeys that need indexed records (the QA stack runs indexing).
const QA_UX = [String.raw`qa/ux-shots\.spec\.ts`];
// QAFINAL-FU-UX: the engine job's card and panel, against the same baselines, on run.sh durable-up's stack (engine
// runs in a broker sandbox). Not in harness-engine: run.sh durable would run it with no baselines.
const QA_UX_ENGINE = [String.raw`qa/ux-shots-engine\.spec\.ts`];
const QA_LATENCY = [String.raw`qa/latency(-pages)?\.spec\.ts`];
const QA_INDEXED = [String.raw`qa/indexed-journey-.*\.spec\.ts`];

// Browser performance specs (EV-15, PF-17): every API is served by the spec itself, so they need only the built app.
const PERF = [String.raw`perf/.*\.spec\.ts`];

// What the authenticated project leaves to others: the login page, setup, seed, first-run and responsive folders.
const OWN_PROJECTS = [String.raw`auth/login\.spec\.ts`, 'setup/.*', 'seed/.*', 'first-run/.*', 'responsive/.*'];

export interface SpecSets {
  ai: RegExp[];
  harness: RegExp[];
  harnessEngine: RegExp[];
  perf: RegExp[];
  qaUx: RegExp[];
  qaUxEngine: RegExp[];
  qaLatency: RegExp[];
  qaIndexed: RegExp[];
  /** Everything the authenticated project does not run. */
  notAuthenticated: RegExp[];
  setup: RegExp;
  seed: RegExp;
  firstRun: RegExp;
  login: RegExp;
  /** Every screen at phone, tablet and desktop sizes. */
  responsive: RegExp;
}

export function specSets(e2eDir: string): SpecSets {
  const under = (relative: string) => underE2e(e2eDir, relative);
  const ai = AI.map(under);
  const harness = HARNESS.map(under);
  const harnessEngine = HARNESS_ENGINE.map(under);
  const perf = PERF.map(under);
  const qaUx = QA_UX.map(under);
  const qaUxEngine = QA_UX_ENGINE.map(under);
  const qaLatency = QA_LATENCY.map(under);
  const qaIndexed = QA_INDEXED.map(under);
  return {
    ai,
    harness,
    harnessEngine,
    perf,
    qaUx,
    qaUxEngine,
    qaLatency,
    qaIndexed,
    notAuthenticated: [...OWN_PROJECTS.map(under), ...ai, ...harness, ...harnessEngine, ...perf, ...qaUx, ...qaUxEngine, ...qaLatency, ...qaIndexed],
    setup: under(String.raw`setup/.*\.setup\.ts`),
    seed: under(String.raw`seed/.*\.spec\.ts`),
    firstRun: under(String.raw`first-run/.*\.spec\.ts`),
    login: under(String.raw`auth/login\.spec\.ts`),
    responsive: under(String.raw`responsive/.*\.spec\.ts`),
  };
}
