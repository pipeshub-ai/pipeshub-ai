import { expect } from 'chai';
import {
  finalShownText,
  ShownText,
  streamingShownText,
  stripModelAuthoredMarkers,
  withoutConfidenceTrailer,
  withoutDeadLinks,
} from '../../../../src/modules/agent-harness/projection/text-filter';

const stream = (chunks: string[]): { shown: string; text: ShownText } => {
  const text = new ShownText();
  let shown = '';
  for (const chunk of chunks) shown += text.push(chunk);
  return { shown, text };
};

describe('agent-harness projection: what streamed text may show (L-12)', () => {
  it('holds a model-authored artifact marker back while it streams and never shows it', () => {
    const marker = '::artifact[evil.csv](https://attacker.test/x){text/csv|x|x|document|1}';
    const raw = `Here it is.\n\n${marker} Done.`;
    for (let size = 1; size <= raw.length; size += 3) {
      const chunks = raw.match(new RegExp(`[\\s\\S]{1,${size}}`, 'g')) as string[];
      const { shown, text } = stream(chunks);
      const all = shown + text.finish(raw);
      expect(all, `chunks of ${size}`).to.equal('Here it is.\n\n Done.');
      expect(all).to.not.include('attacker');
    }
  });

  it('strips every marker form the legacy finalizer strips', () => {
    for (const marker of [
      '::artifact[a](u){m|d|r|t|1}',
      '::artifact[a]{m}',
      '::artifact[a](u)',
      '::artifact[a]',
      '::download_conversation_task[f](https://x)',
    ]) {
      expect(stripModelAuthoredMarkers(`x ${marker} y`), marker).to.equal('x  y');
    }
  });

  it('hides the confidence trailer, complete or still streaming', () => {
    const raw = 'The answer is 42.\n\n---\nConfidence: High';
    expect(withoutConfidenceTrailer(raw)).to.equal('The answer is 42.');
    const { shown, text } = stream(['The answer is 42.\n\n', '---\n', 'Confid', 'ence: Hi', 'gh']);
    // As legacy: the blank lines before the rule already streamed.
    expect(shown + text.finish(raw)).to.equal('The answer is 42.\n\n');
    expect(finalShownText(raw)).to.equal('The answer is 42.');
  });

  it('shows ordinary text, including double colons that are not markers, as it streams', () => {
    const { shown } = stream(['a::b ', 'C++ uses std::vector', '.']);
    expect(shown).to.equal('a::b C++ uses std::vector.');
  });

  describe('MR-18: a link the chat cannot open shows as its words', () => {
    const LINKED = 'Done. [Download fib.py](sandbox:/mnt/data/out/fib.py) and see [docs](https://example.test/d).';
    const SHOWN = 'Done. Download fib.py and see [docs](https://example.test/d).';

    it('holds a sandbox link back while it streams, at every split, and never shows its target', () => {
      for (let size = 1; size <= LINKED.length; size += 1) {
        const chunks = LINKED.match(new RegExp(`[\\s\\S]{1,${size}}`, 'g')) as string[];
        const text = new ShownText();
        let shown = '';
        for (const chunk of chunks) {
          shown += text.push(chunk);
          expect(shown, `chunks of ${size}`).to.not.include('sandbox:');
          expect(SHOWN.startsWith(shown), `chunks of ${size}: ${shown}`).to.equal(true);
        }
        expect(shown + text.finish(LINKED), `chunks of ${size}`).to.equal(SHOWN);
      }
    });

    it('holds a link whose words hold parentheses until it closes', () => {
      const raw = 'See [the plan (v2)](sandbox:/mnt/data/out/plan.md) now.';
      const chunks = raw.match(/[\s\S]{1,4}/g) as string[];
      const { shown, text } = stream(chunks);
      expect(shown).to.not.include('sandbox:').and.to.not.include('[');
      expect(shown + text.finish(raw)).to.equal('See the plan (v2) now.');
    });

    it('shows a link whole in one delta as its words at once, and plain text as it streams', () => {
      const { shown } = stream(['Saved ', '[fib.py](sandbox:/mnt/data/out/fib.py)', ' for you']);
      expect(shown).to.equal('Saved fib.py for you');
      expect(streamingShownText('Plain words stream')).to.equal('Plain words stream');
    });

    it('names a path label by its file and keeps what the chat opens', () => {
      expect(withoutDeadLinks('[out/a.csv](file:///workspace/out/a.csv) [/workspace/out/b.md](/workspace/out/b.md)'))
        .to.equal('a.csv b.md');
      expect(withoutDeadLinks('[notes](out/notes.txt)')).to.equal('notes');
      const kept = '[q](https://x.test/q) [m](mailto:a@b.test) [1](ref3) [file](/record/rec-1) [top](#top)';
      expect(withoutDeadLinks(kept)).to.equal(kept);
      expect(finalShownText('See [x](sandbox:/mnt/data/x.py).')).to.equal('See x.');
    });
  });
});
