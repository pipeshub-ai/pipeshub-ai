// The runtime of the generated TypeScript/JavaScript stubs of a run's tools (WS-14, C3.15), seeded as
// _runtime.mjs in each turn's /pipeshub/session/sdk-<digest>/ts/. Every stub is one `ph --json tools call`, run as
// a child process with the arguments on stdin, so the stubs have no network path, credential or decision of their
// own: the gateway, the placeholder the egress proxy swaps, approvals and the journal are ph's, and an answer is
// exactly ph's (C3.16), errors included. The ph run is the session folder's (session/ph.py), as the Python stubs'.
import { spawn } from "node:child_process";
import { existsSync, realpathSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const SESSION = dirname(dirname(dirname(realpathSync(fileURLToPath(import.meta.url)))));
const SEEDED_PH = join(SESSION, "ph.py");
const IMAGE_PH = "/usr/local/bin/ph";

export class CallResult {
  constructor(tool, text) {
    this.tool = tool;
    this.text = text;
  }

  json() {
    return JSON.parse(this.text);
  }
}

export class CallError extends Error {
  constructor(code, exit, message) {
    super(message);
    this.name = new.target.name;
    this.code = code;
    this.exit = exit;
  }
}

export class ToolFailed extends CallError {}
export class ApprovalPending extends CallError {}
export class UsageError extends CallError {}
export class NotFound extends CallError {}
export class Unavailable extends CallError {}
export class BadAnswer extends CallError {}
export class NotPermitted extends CallError {}
export class NotInRun extends CallError {}

const ERRORS = {
  tool_error: ToolFailed,
  approval_pending: ApprovalPending,
  usage: UsageError,
  not_found: NotFound,
  unavailable: Unavailable,
  bad_answer: BadAnswer,
  not_permitted: NotPermitted,
  not_in_run: NotInRun,
};

function program() {
  if (existsSync(SEEDED_PH)) return ["python3", [SEEDED_PH]];
  return [IMAGE_PH, []];
}

function answerOf(stdout) {
  const lines = stdout.split("\n").filter((line) => line.trim());
  try {
    const answer = lines.length ? JSON.parse(lines[lines.length - 1]) : null;
    if (answer && typeof answer === "object") return answer;
  } catch {
    // An unreadable answer is reported below.
  }
  return null;
}

export function envelope(tool, args) {
  const [command, prefix] = program();
  return new Promise((resolve, reject) => {
    const child = spawn(command, [...prefix, "--json", "tools", "call", tool, "--json", "-"], {
      stdio: ["pipe", "pipe", "inherit"],
    });
    const out = [];
    child.stdout.on("data", (chunk) => out.push(chunk));
    child.on("error", (failed) =>
      reject(new NotInRun("not_in_run", 78, `pipeshub stubs: could not run ph (${failed.message}); they work inside a PipesHub engine run.`)),
    );
    child.on("close", () => {
      const answer = answerOf(Buffer.concat(out).toString("utf8"));
      if (answer === null) reject(new BadAnswer("bad_answer", 70, "pipeshub stubs: ph's answer could not be read."));
      else resolve(answer);
    });
    child.stdin.end(JSON.stringify(args ?? {}));
  });
}

export async function call(tool, args) {
  const answer = await envelope(tool, args ?? {});
  if (answer.ok === true) {
    const data = answer.data ?? {};
    return new CallResult(String(data.tool ?? tool), String(data.text ?? ""));
  }
  const error = answer.error ?? {};
  const code = String(error.code ?? "failed");
  const Kind = ERRORS[code] ?? CallError;
  throw new Kind(code, Number(error.exit ?? 1), String(error.message ?? ""));
}

// A generated stub's call: an optional argument left undefined is not sent (JSON.stringify drops it).
export function invoke(tool, args) {
  return call(tool, args ?? {});
}
