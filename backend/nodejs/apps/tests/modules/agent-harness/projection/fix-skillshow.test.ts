/**
 * FIX-SKILLSHOW: a step that read a skill names the skill, for every engine, in the activity's commands and in every
 * engine progress line (live and saved), from one recognizer; the command itself is kept as it ran.
 */
import { expect } from 'chai';
import {
  applyLive,
  emptyActivity,
  foldActivity,
} from '../../../../src/modules/agent-harness/projection/activity';
import { engineProgressFrame } from '../../../../src/modules/agent-harness/projection/run-projector';
import {
  commandSkills,
  toolSkills,
  withLineSkills,
} from '../../../../src/modules/agent-harness/projection/skill-use';
import { foldTimeline } from '../../../../src/modules/agent-harness/projection/timeline';
import { ActivityJournal } from '../support/activity-journal';

/** The first command of a Codex child run, as a manual test reported it. */
const CODEX_CHAIN =
  `/bin/bash -lc "cat /pipeshub/AGENTS.md && printf '\\n---PDF SKILL---\\n' && cat /skills/pdf/SKILL.md && ` +
  `printf '\\n---TOOLS INDEX---\\n' && cat /pipeshub/tools/INDEX.md"`;

describe('FIX-SKILLSHOW skill reads', () => {
  describe('commandSkills', () => {
    it("finds the PDF skill in the Codex run's chained first command", () => {
      expect(commandSkills(CODEX_CHAIN)).to.deep.equal(['pdf']);
    });

    it('reads `ph skill show <name>`, with or without a shell around it', () => {
      expect(commandSkills('ph skill show pdf')).to.deep.equal(['pdf']);
      expect(commandSkills("bash -c 'ph skill show docx --raw'")).to.deep.equal(['docx']);
      expect(commandSkills('/usr/local/bin/ph skills show xlsx')).to.deep.equal(['xlsx']);
    });

    it('reads any printing program on a SKILL.md, in any segment of a chain or a pipe', () => {
      expect(commandSkills("sed -n '1,200p' /skills/pdf/SKILL.md")).to.deep.equal(['pdf']);
      expect(commandSkills('ls /skills; head -50 /skills/docx/SKILL.md | nl')).to.deep.equal(['docx']);
      expect(commandSkills('cd /workspace && nl -ba ./skills/pptx/SKILL.md')).to.deep.equal(['pptx']);
      expect(commandSkills('FOO=1 sudo cat < /skills/pdf/SKILL.md 2>/dev/null')).to.deep.equal(['pdf']);
    });

    it('reads through quoting: escaped quotes in a double-quoted script, a line continuation, an empty word', () => {
      expect(commandSkills('bash -lc "cat \\"/skills/pdf/SKILL.md\\""')).to.deep.equal(['pdf']);
      expect(commandSkills('cat \\\n  /skills/pdf/SKILL.md')).to.deep.equal(['pdf']);
      expect(commandSkills("cat '' /skills/pdf/SKILL.md")).to.deep.equal(['pdf']);
      expect(commandSkills('bash make.sh /skills/pdf/SKILL.md')).to.deep.equal([], 'a shell running a script file');
      expect(commandSkills('FOO=1')).to.deep.equal([]);
    });

    it('names each skill once, in the order they were read', () => {
      expect(
        commandSkills('cat /skills/pdf/SKILL.md && cat /skills/xlsx/SKILL.md; less /skills/pdf/SKILL.md'),
      ).to.deep.equal(['pdf', 'xlsx']);
    });

    it('is no skill read when nothing printed a SKILL.md', () => {
      expect(commandSkills('cat /pipeshub/AGENTS.md && cat /pipeshub/tools/INDEX.md')).to.deep.equal([]);
      expect(commandSkills('node build.js')).to.deep.equal([]);
      expect(commandSkills("rm /skills/pdf/SKILL.md")).to.deep.equal([], 'deleting is not reading');
      expect(commandSkills("echo 'cat /skills/pdf/SKILL.md'")).to.deep.equal([], 'a quoted mention is not a read');
      expect(commandSkills('cat /skills/pdf/reference.md')).to.deep.equal([]);
      expect(commandSkills('ph skill list')).to.deep.equal([]);
      expect(commandSkills('graph show pdf')).to.deep.equal([]);
      expect(commandSkills('')).to.deep.equal([]);
    });

    it('a shell nested past its depth bound is left unread rather than walked forever', () => {
      const wrap = (script: string): string => `bash -c '${script.replace(/'/g, `'\\''`)}'`;
      const three = wrap(wrap(wrap('ph skill show pdf')));
      expect(commandSkills(three)).to.deep.equal(['pdf']);
      expect(commandSkills(wrap(three))).to.deep.equal([]);
    });
  });

  describe('toolSkills', () => {
    it("Claude Code's Skill tool names its skill (and an older build's `command`)", () => {
      expect(toolSkills('Skill', '{"skill":"pdf"}')).to.deep.equal(['pdf']);
      expect(toolSkills('Skill', { command: 'docx' })).to.deep.equal(['docx']);
      expect(toolSkills('Skill', '{"skill":""}')).to.deep.equal([]);
      expect(toolSkills('Skill', 'not json')).to.deep.equal([]);
      expect(toolSkills('Skill', '[1]')).to.deep.equal([]);
    });

    it("opencode's and Gemini CLI's skill tools, a file read on a SKILL.md and a shell tool's command", () => {
      expect(toolSkills('skill', '{"name":"pdf"}')).to.deep.equal(['pdf']);
      expect(toolSkills('activate_skill', '{"name":"xlsx"}')).to.deep.equal(['xlsx']);
      expect(toolSkills('Read', '{"file_path":"/skills/pdf/SKILL.md"}')).to.deep.equal(['pdf']);
      expect(toolSkills('read', '{"filePath":"/workspace/notes.md"}')).to.deep.equal([]);
      expect(toolSkills('bash', '{"command":"cat /skills/pdf/SKILL.md"}')).to.deep.equal(['pdf']);
      expect(toolSkills('WebFetch', '{"url":"https://x.test/skills/pdf/SKILL.md"}')).to.deep.equal([]);
      expect(toolSkills('Bash', undefined)).to.deep.equal([]);
      expect(toolSkills('Read', { path: 7 })).to.deep.equal([]);
      expect(toolSkills('Skill', `{"skill":"${'x'.repeat(9_000)}"}`)).to.deep.equal([], 'arguments past the bound are not parsed');
    });
  });

  describe('engine progress lines', () => {
    it('a command or tool line gains `skills`; any other line is left as it was', () => {
      expect(withLineSkills('command', CODEX_CHAIN, { callId: 'c1' })).to.deep.equal({ callId: 'c1', skills: ['pdf'] });
      expect(withLineSkills('tool_update', 'Skill', { args: '{"skill":"pdf"}' })).to.deep.include({ skills: ['pdf'] });
      const output = { callId: 'c1', stream: 'stdout' };
      expect(withLineSkills('output', 'cat /skills/pdf/SKILL.md', output)).to.equal(output);
      const plain = { callId: 'c2' };
      expect(withLineSkills('command', 'ls', plain)).to.equal(plain);
    });

    it('the live frame and the saved timeline both carry the skill, the command text unchanged', () => {
      const frame = engineProgressFrame(
        { tool: 'code_task', taskId: 't', kind: 'command', text: CODEX_CHAIN, data: { callId: 'c1' } },
        'run-1',
      );
      expect(frame.value).to.deep.include({ text: CODEX_CHAIN, data: { callId: 'c1', skills: ['pdf'] } });

      const journal = new ActivityJournal()
        .engine('started', 'Make a PDF')
        .engine('command', CODEX_CHAIN, { callId: 'c1' })
        .engine('tool', 'Skill', { callId: 'c2', args: '{"skill":"pdf"}' });
      const saved = foldTimeline(journal.events, journal.runId).engine!.frames;
      expect(saved[1]).to.deep.include({ text: CODEX_CHAIN, data: { callId: 'c1', skills: ['pdf'] } });
      expect(saved[2]!.data).to.deep.include({ skills: ['pdf'] });
      expect(saved[0]!.data).to.not.have.property('skills');
    });
  });

  describe('activity commands', () => {
    it('the Codex chain is listed with its skill and its whole command; a plain command names none', () => {
      const journal = new ActivityJournal()
        .engine('command', CODEX_CHAIN, { callId: 'c1' })
        .engine('command_done', '', { callId: 'c1', exitCode: 0 })
        .engine('command', 'node make-pdf.js', { callId: 'c2' })
        .commandFinished({ commandId: 'r1', source: 'engine', command: 'ph skill show pdf' });
      const { commands } = foldActivity(journal.events, { runId: journal.runId, runStatus: 'running' });
      expect(commands.map(({ commandId, command, skills }) => ({ commandId, command, skills }))).to.deep.equal([
        { commandId: 'c1', command: CODEX_CHAIN, skills: ['pdf'] },
        { commandId: 'c2', command: 'node make-pdf.js', skills: undefined },
        { commandId: 'r1', command: 'ph skill show pdf', skills: ['pdf'] },
      ]);
      expect(commands[1]).to.not.have.property('skills');
    });

    it('a command first seen live names its skill too', () => {
      const view = applyLive(emptyActivity('run-ux5'), {
        runId: 'run-ux5',
        fence: 1,
        modelAttempt: 0,
        n: 1,
        kind: 'command_started',
        data: { commandId: 'live-1', source: 'engine', command: "sed -n '1,80p' /skills/pdf/SKILL.md", startedAt: 1 },
      });
      expect(view.commands[0]).to.deep.include({ commandId: 'live-1', skills: ['pdf'] });
    });
  });
});
