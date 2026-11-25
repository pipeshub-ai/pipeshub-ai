// Types of the stubs' runtime (_runtime.mjs): what `ph --json tools call` answers, as values and errors.

/** A tool's answer: its name and the text of its result. */
export declare class CallResult {
  readonly tool: string;
  readonly text: string;
  constructor(tool: string, text: string);
  /** The result's text read as JSON (most tools answer JSON). */
  json(): unknown;
}

/** A failed call, as `ph --json tools call` reports it: its error code, the exit code ph ends with, the message. */
export declare class CallError extends Error {
  readonly code: string;
  readonly exit: number;
  constructor(code: string, exit: number, message: string);
}
/** `tool_error` (exit 1): the tool ran and failed, or the gateway refused the call. */
export declare class ToolFailed extends CallError {}
/** `approval_pending` (exit 75): the call waits for a person. End the turn; do not call it again until told. */
export declare class ApprovalPending extends CallError {}
/** `usage` (exit 64): the arguments were not a JSON object. */
export declare class UsageError extends CallError {}
/** `not_found` (exit 65): this run has no tool of that name. */
export declare class NotFound extends CallError {}
/** `unavailable` (exit 69): the tool gateway could not be reached or did not answer in time. */
export declare class Unavailable extends CallError {}
/** `bad_answer` (exit 70): the tool gateway's answer could not be read. */
export declare class BadAnswer extends CallError {}
/** `not_permitted` (exit 77): the gateway refused the sandbox's credentials (the run has ended). */
export declare class NotPermitted extends CallError {}
/** `not_in_run` (exit 78): not inside a PipesHub engine run. */
export declare class NotInRun extends CallError {}

export interface Envelope {
  ok: boolean;
  command: string | null;
  data?: { tool: string; text: string };
  error?: { code: string; exit: number; message: string };
}

/** `ph --json tools call <tool> --json -` with the arguments on stdin: its envelope. */
export declare function envelope(tool: string, args: Record<string, unknown>): Promise<Envelope>;
/** Calls any tool of this run by its full name (`<toolset>__<tool>`), exactly as `ph tools call` does. */
export declare function call(tool: string, args?: Record<string, unknown>): Promise<CallResult>;
export declare function invoke(tool: string, args?: Record<string, unknown>): Promise<CallResult>;
