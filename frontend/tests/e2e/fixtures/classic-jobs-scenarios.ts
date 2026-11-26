/**
 * CJ-5: the scripted turns behind the classic jobs specs.
 *
 * Every scenario is a turn's text: a plain first line (the job's goal, so its card has a title a person would write)
 * and then the scripted model's directives, one per line (`harness_core/testing/engines/script.py`). Two stacks run
 * them, and each scenario names the one it needs:
 *
 * - **engine** (`run.sh durable`, the `harness-engine` project): the engine agent's loop hands the whole turn to
 *   `code_task` (CTL-6), and the engine child runs the directives in a broker sandbox (opencode on the scripted
 *   model). Its commands, files and artifacts are real.
 * - **loop** (`run.sh harness`, the `harness` project): the tool agent's turn in deep mode; the coordinator plans
 *   one step and hands the whole turn to one sub-agent (`spawn_agent`, `deployment/e2e/fake_llm/server.py`
 *   `_delegate`), whose own loop runs the directives with the agent's tools (the scripted SaaS).
 *
 * A `hold` keeps the job's next model call open until the spec releases it (`ScriptedModel.release`), so a job is
 * "working" for exactly as long as a spec needs it to be, never for a guessed number of seconds.
 */

/** The scripted SaaS's tools on both stacks: a read, and a write the seeded approval policy asks about. */
export const READ_TOOL = 'lookup_note';
export const WRITE_TOOL = 'create_ticket';

export type JobStack = 'engine' | 'loop';

export interface JobScenario {
  stack: JobStack;
  /** The turn's first line: what the person asked for, and the job card's title. */
  goal: string;
  /** The whole turn as typed into the chat. */
  prompt: string;
  /** The answer the turn ends with, once the job is back. */
  answer: string;
  /** A file the job makes (an output), when it makes one. */
  file?: string;
  /** A write the job asks the person to allow, when it asks. */
  ticket?: string;
}

const turn = (goal: string, ...directives: string[]): string => [goal, ...directives].join('\n');

/** A loop's tools are disclosed lazily (LZ1): its turn looks them up first, as QA-6's loop scripts do. */
const discover = (...tools: string[]): string =>
  `tool search_tools ${JSON.stringify({ query: tools.join(' '), make_visible: true })}`;

/** BGUI-11: what CJ-E1's engine leaves beside its deliverable, as a recorded IPL job left notes and its script. */
export const ENGINE_REPORT_WORKING_FILES = ['notes/progress.md', 'make_report.py'] as const;

/**
 * CJ-E1: a code_task that runs a while and finishes with an output, and leaves working files beside it (BGUI-11). The
 * `hold` sits after the command, so the card shows a now line from a real command while the job is held "working".
 */
export function engineReport(tag: string): JobScenario {
  const file = `report-${tag}.pdf`;
  const goal = `Build the quarterly PDF report ${tag}.`;
  const answer = `The quarterly report is in ${file}.`;
  return {
    stack: 'engine',
    goal,
    file,
    answer,
    prompt: turn(
      goal,
      `shell mkdir -p out notes && printf '%%PDF-1.4\\n%% Q3 report ${tag}\\n%%%%EOF\\n' > out/${file} && printf 'done: the report\\n' > notes/progress.md && printf 'print(1)\\n' > make_report.py`,
      'hold',
      `say ${answer}`,
    ),
  };
}

/**
 * BGUI-10 (engine): a recorded IPL report. The coder's loop loads the PDF skill before it hands off (HANDOFF-CTX), and writes
 * its own notes as the task (`handoff-first`, `handoff-notes`: deployment/e2e/fake_llm/server.py). The job's Brief
 * shows the notes as its task, the request as "You asked", and "Loaded the PDF skill", never the prompt.
 */
export function engineCarried(tag: string): JobScenario & { notes: string } {
  const file = `ipl-${tag}.pdf`;
  const goal = `Can you prepare a pdf report of ipl 2026 event ${tag}`;
  const notes = `Prepare a PDF report on the IPL 2026 season ${tag} with the PDF skill already loaded.`;
  const answer = `The IPL report is in ${file}.`;
  return {
    stack: 'engine',
    goal,
    notes,
    file,
    answer,
    prompt: turn(
      goal,
      `handoff-first load_skill ${JSON.stringify({ name: 'pdf' })}`,
      `handoff-notes ${notes}`,
      `shell mkdir -p out && printf '%%PDF-1.4\\n%% IPL ${tag}\\n%%%%EOF\\n' > out/${file}`,
      `say ${answer}`,
    ),
  };
}

/**
 * BGUI-12 (engine): a recorded IPL job cited the pages it read as numbered links. The scripted engine reads none (no outside
 * request leaves the lane), so its two numbered links are its own: no tool read them, no citation is stored, and the
 * job's reply in its panel names each by its site and links it, never a bare number.
 */
export const ENGINE_CITED_PAGES = [
  { url: 'https://www.iplt20.com/news/article/final-report', site: 'iplt20' },
  { url: 'https://www.espncricinfo.com/series/ipl-2026/caps', site: 'espncricinfo' },
] as const;

export function engineCited(tag: string): JobScenario {
  const goal = `Sum up the IPL 2026 final ${tag}.`;
  const [final, caps] = ENGINE_CITED_PAGES;
  const answer = `RCB won the IPL 2026 final ${tag} [1](${final.url}) and Rabada took the Purple Cap [2](${caps.url}).`;
  return { stack: 'engine', goal, answer, prompt: turn(goal, `say ${answer}`) };
}

/** CJ-E2 (engine): the job's write asks; nothing runs until the person allows it. */
export function engineApproval(tag: string): JobScenario {
  const ticket = `CJ5 ${tag}`;
  const goal = `File the Q3 report ticket ${tag}.`;
  const answer = `Ticket ${ticket} is filed.`;
  return {
    stack: 'engine',
    goal,
    ticket,
    answer,
    prompt: turn(goal, `tool ${WRITE_TOOL} ${JSON.stringify({ title: ticket, body: 'From CJ-5.' })}`, `say ${answer}`),
  };
}

/**
 * CJ-E3: a job held working until the person stops it. The answer is never released, so a "Done" on the card could
 * only come from a bug.
 */
export function engineStoppable(tag: string): JobScenario {
  const goal = `Re-run the slow export ${tag}.`;
  return {
    stack: 'engine',
    goal,
    answer: `The export ${tag} finished.`,
    prompt: turn(goal, `shell echo exporting ${tag}`, 'hold', `say The export ${tag} finished.`),
  };
}

/**
 * A recorded skill-reading command, as the engine runs it: one chained shell command that reads AGENTS.md, the
 * PDF skill and the tools index. `;` instead of `&&`, so a sandbox without one of the files still reads the rest:
 * the label is about what the command reads, not whether every file was there.
 */
export const SKILL_READ_COMMAND =
  "cat /pipeshub/AGENTS.md; printf '\\n---PDF SKILL---\\n'; cat /skills/pdf/SKILL.md; printf '\\n---TOOLS INDEX---\\n'; cat /pipeshub/tools/INDEX.md; true";

/** CJ-E6: a chained command that reads `/skills/pdf/SKILL.md`; the step reads "Read the PDF skill". */
export function engineSkillRead(tag: string): JobScenario {
  const goal = `Make the PDF summary ${tag} the way the PDF skill says.`;
  const answer = `The PDF summary ${tag} follows the skill.`;
  return { stack: 'engine', goal, answer, prompt: turn(goal, `shell ${SKILL_READ_COMMAND}`, `say ${answer}`) };
}

/** A loop sub-agent that reads a note, is held working, then answers (CJ-E4, CJ-E5, CJ-E7, the shared viewer). */
export function loopHeld(tag: string): JobScenario {
  const goal = `Summarise the release notes ${tag}.`;
  const answer = `The release notes ${tag} are summarised.`;
  return {
    stack: 'loop',
    goal,
    answer,
    // The hold sits between the lookup and the read: after a sub-agent's last tool call the scripted model's deep-mode
    // coordinator answers instead (`_delegate`), so a hold placed after the read would never block.
    prompt: turn(goal, discover(READ_TOOL), 'hold', `tool ${READ_TOOL} ${JSON.stringify({ topic: 'release' })}`, `say ${answer}`),
  };
}

/** CJ-E2 (loop): a loop sub-agent's write asks the person; Needs you, the badge and the dot follow it. */
export function loopApproval(tag: string): JobScenario {
  const ticket = `CJ5 loop ${tag}`;
  const goal = `File the release ticket ${tag}.`;
  const answer = `Ticket ${ticket} is filed.`;
  return {
    stack: 'loop',
    goal,
    ticket,
    answer,
    prompt: turn(
      goal,
      discover(READ_TOOL, WRITE_TOOL),
      `tool ${READ_TOOL} ${JSON.stringify({ topic: 'release' })}`,
      `tool ${WRITE_TOOL} ${JSON.stringify({ title: ticket, body: 'From CJ-5.' })}`,
      `say ${answer}`,
    ),
  };
}
