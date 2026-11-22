// Argument parsing and output for `pipeshub-native`. The contract the desktop app relies on:
//   pipeshub-native start  [--data-dir D] [--json] [--detach]   foreground until stopped; --json prints one JSON object per line:
//                                {"event":"progress","stage","message"} ... {"event":"ready","url","ports","healthy_after_s","pid","data_dir"}
//                                then {"event":"stopping"} {"event":"stopped"} on TERM/INT. Exit 0 stopped on request, 1 start-up failed,
//                                3 a service died. --detach returns after "ready" and leaves the launcher running.
//   pipeshub-native stop   [--data-dir D] [--json]               stops a running launcher and everything under it (exit 0 also if none runs, 1 stop_failed)
//   pipeshub-native status [--data-dir D] [--json]               {"running":bool,"phase","pid","url","ports","services"...}; exit 0 running, 3 not
//   pipeshub-native health [--data-dir D] [--json]               {"healthy":bool,"checks":{...}}; exit 0 healthy, 1 not
// Every JSON object has an "event" field. Other options: --bundle D (default: the folder above the launcher's), --port-base N.
import { type Emit, type Options, health, start, startDetached, status, stop } from "./launcher.ts";

export interface Parsed { cmd: string; json: boolean; detach: boolean; opts: Options; rest: string[] }

export function parse(argv: string[]): Parsed {
  const opts: Options = {};
  const rest: string[] = [], pos: string[] = [];
  let json = false, detach = false;
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i]!;
    // "" would mean the current folder, and "--json" is the next option, not a value
    const val = () => { const v = argv[++i]; if (!v || v.startsWith("--")) throw new Error(`${a} needs a value`); return v; };
    if (a === "--json") json = true;
    else if (a === "--detach") detach = true;
    else if (a === "--data-dir") { opts.dataDir = val(); rest.push(a, opts.dataDir); }
    else if (a.startsWith("--data-dir=")) { opts.dataDir = a.slice(11); if (!opts.dataDir) throw new Error("--data-dir needs a value"); rest.push(a); }
    else if (a === "--bundle") { opts.bundle = val(); rest.push(a, opts.bundle); }
    else if (a === "--port-base") {
      const n = Number(val());
      if (!Number.isInteger(n) || n < 1024 || n > 65000) throw new Error("--port-base needs a port between 1024 and 65000");
      opts.portBase = n; rest.push(a, String(n));
    }
    else if (a.startsWith("--")) throw new Error(`unknown option ${a}`);
    else pos.push(a);
  }
  if (pos.length > 1) throw new Error(`unexpected argument ${pos[1]}`);
  return { cmd: pos[0] ?? "", json, detach, opts, rest };
}

const USAGE = "usage: pipeshub-native start|stop|status|health [--data-dir DIR] [--json]   (start also: --detach)";

export function emitter(json: boolean, out: (s: string) => void = (s) => process.stdout.write(s + "\n")): Emit {
  return (e) => {
    if (json) return out(JSON.stringify(e));
    const { event, ...rest } = e;
    out(event === "progress" ? `... ${rest.message}` : event === "ready" ? `ready: ${rest.url} (${rest.healthy_after_s} s)`
      : [event, ...Object.entries(rest).map(([k, v]) => `${k}=${typeof v === "object" ? JSON.stringify(v) : v}`)].join(" "));
  };
}

export async function run(argv: string[], selfCmd: string[], out: (s: string) => void = (s) => process.stdout.write(s + "\n")): Promise<number> {
  let p: Parsed;
  try { p = parse(argv); } catch (e) { process.stderr.write(`${(e as Error).message}\n${USAGE}\n`); return 2; }
  const emit = emitter(p.json, out);
  try {
    switch (p.cmd) {
      case "start": return await (p.detach ? startDetached(p.opts, emit, p.rest, selfCmd) : start(p.opts, emit));
      case "stop": return await stop(p.opts, emit);
      case "status": { const r = status(p.opts); emit({ event: "status", ...r.body }); return r.code; }
      case "health": { const r = await health(p.opts); emit({ event: "health", ...r.body }); return r.code; }
      case "version": out("pipeshub-native 1"); return 0;
    }
  } catch (e) {
    // Anything not already reported (an unwritable data folder, say) still reaches a --json caller as one error event.
    emit({ event: "error", code: p.cmd === "stop" ? "stop_failed" : "start_failed", message: (e as Error).message });
    return 1;
  }
  process.stderr.write(USAGE + "\n");
  return 2;
}
