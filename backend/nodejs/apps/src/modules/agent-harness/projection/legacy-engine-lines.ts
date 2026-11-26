/**
 * Engine progress lines journaled or saved before FIX-SKILLSHOW and FIX-TOOLWORDS, read as those changes write them
 * (SKILLSHOW-FU-1, TOOLWORDS-FU-2). A read-time projection: no journal event or stored message is rewritten.
 *
 * - A line names the skills its step read (`withLineSkills`), so an old answer's chained `cat …/SKILL.md` reads as
 *   "Read the PDF skill".
 * - An opencode tool line named the gateway tool as opencode exposes it, `tools_<name>` (`launch.exposed_tool_name`),
 *   and its ask carried no `callId`, so the chat drew "Tools MCP …" and a second row for the ask. The line takes the
 *   gateway's name, and the ask is linked to the waiting call of that tool with the same arguments, as the presenter
 *   links a new one. No other engine journals a `tools_` name: Claude Code's are `mcp__tools__…`, ACP's are stripped.
 */
import { withLineSkills } from './skill-use';

/** opencode's MCP server name for the Tool Gateway (`harness_engines/opencode/launch.py` `MCP_SERVER`). */
const OPENCODE_GATEWAY_PREFIX = 'tools_';
const TOOL_LINES: ReadonlySet<string> = new Set(['tool', 'tool_update']);

export interface EngineLine {
  kind: string;
  text: string;
  data: Record<string, unknown>;
}

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value);

const str = (value: unknown): string | undefined =>
  typeof value === 'string' && value !== '' ? value : undefined;

function canonical(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(canonical);
  if (!isRecord(value)) return value;
  return Object.fromEntries(
    Object.keys(value)
      .sort()
      .map((key) => [key, canonical(value[key])]),
  );
}

/** A call's arguments as one string whatever their key order or spacing; the raw text if it is not JSON. */
function argsKey(args: unknown): string {
  if (typeof args !== 'string') return JSON.stringify(canonical(args ?? {}));
  try {
    return JSON.stringify(canonical(JSON.parse(args)));
  } catch {
    return args;
  }
}

const callKey = (tool: string, args: unknown): string =>
  `${tool}\u0000${argsKey(args)}`;

/**
 * Reads one run's engine lines in order. Lines are the caller's own objects: a line is given a new `data` (never the
 * journal payload changed) and an ask seen before its call is updated in place when the call arrives.
 */
export class LegacyEngineLines {
  /** Calls of a gateway tool waiting for their result, by tool and arguments, oldest first. */
  private readonly waiting = new Map<
    string,
    Array<{ callId: string; line: EngineLine }>
  >();
  /** Asks with no `callId` whose call has not been seen yet (the saved order can put the ask first). */
  private readonly unlinked = new Map<string, EngineLine[]>();

  line<L extends EngineLine>(line: L): L {
    const data =
      'skills' in line.data
        ? line.data
        : withLineSkills(line.kind, line.text, line.data);
    let text = line.text;
    if (
      TOOL_LINES.has(line.kind) &&
      text.startsWith(OPENCODE_GATEWAY_PREFIX) &&
      text.length > OPENCODE_GATEWAY_PREFIX.length
    ) {
      text = text.slice(OPENCODE_GATEWAY_PREFIX.length);
    }
    const callId = str(data['callId']);
    if (line.kind === 'tool' && callId !== undefined) {
      const key = callKey(text, data['args']);
      const ask = this.unlinked.get(key)?.shift();
      const out: L = { ...line, text, data };
      if (ask !== undefined) {
        ask.data = { ...ask.data, callId };
        named(out, ask);
      } else
        this.waiting.set(key, [
          ...(this.waiting.get(key) ?? []),
          { callId, line: out },
        ]);
      return out;
    }
    if (line.kind === 'tool_result' && callId !== undefined) {
      for (const [key, calls] of this.waiting) {
        const at = calls.findIndex((call) => call.callId === callId);
        if (at >= 0) {
          calls.splice(at, 1);
          if (calls.length === 0) this.waiting.delete(key);
          break;
        }
      }
    } else if (line.kind === 'approval' && callId === undefined) {
      const key = callKey(str(data['toolName']) ?? text, data['argsJson']);
      const call = this.waiting.get(key)?.shift();
      const out: L = {
        ...line,
        text,
        data: call !== undefined ? { ...data, callId: call.callId } : data,
      };
      if (call !== undefined) named(call.line, out);
      else this.unlinked.set(key, [...(this.unlinked.get(key) ?? []), out]);
      return out;
    }
    return text === line.text && data === line.data
      ? line
      : { ...line, text, data };
  }
}

/** A call the gateway named only on its ask takes the ask's display name, as a new call's line carries it. */
function named(call: EngineLine, ask: EngineLine): void {
  const displayName = str(ask.data['displayName']);
  if (
    displayName !== undefined &&
    str(call.data['displayName']) === undefined
  ) {
    call.data = { ...call.data, displayName };
  }
}

/** A saved answer's parts with each `engine_task` part's lines read by `LegacyEngineLines`; as given when it has none. */
export function withSavedEngineLines<P>(parts: P): P {
  if (!Array.isArray(parts) || !parts.some(isEngineTask)) return parts;
  const out: unknown[] = parts.map((part: unknown) => {
    if (!isEngineTask(part)) return part;
    const reader = new LegacyEngineLines();
    const frames = part.frames.map((frame: unknown) =>
      isRecord(frame)
        ? reader.line({
            ...frame,
            kind: typeof frame['kind'] === 'string' ? frame['kind'] : '',
            text: typeof frame['text'] === 'string' ? frame['text'] : '',
            data: isRecord(frame['data']) ? frame['data'] : {},
          })
        : frame,
    );
    return { ...part, frames };
  });
  return out as P;
}

function isEngineTask(
  part: unknown,
): part is Record<string, unknown> & { frames: unknown[] } {
  return (
    isRecord(part) &&
    part['type'] === 'engine_task' &&
    Array.isArray(part['frames'])
  );
}
