import { expect } from 'chai';
import { readdirSync, readFileSync, statSync } from 'fs';
import { join } from 'path';
import {
  AGUI,
  AGUI_CUSTOM,
} from '../../../../src/modules/agent-harness/projection/frames';
import {
  WAITING_PROVIDER,
  WAITING_SANDBOX,
  WAITING_STORE,
} from '../../../../src/modules/agent-harness/projection/run-projector';

const REPO = join(__dirname, '../../../../../../..');
const VECTORS = join(REPO, 'contracts/harness/vectors');
const HARNESS_SRC = join(__dirname, '../../../../src/modules/agent-harness');

type Entries = Record<
  string,
  { description: string; example?: Record<string, unknown> }
>;
interface UiStreamEvents {
  chat: { frames: Entries; custom: Entries; snapshotStatuses: Entries };
  activity: { events: Entries };
}
const vector = JSON.parse(
  readFileSync(join(VECTORS, 'ui-stream-events.json'), 'utf8'),
) as UiStreamEvents;

const files = (dir: string, suffix: string): string[] =>
  readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) return files(path, suffix);
    return name.endsWith(suffix) ? [path] : [];
  });

const sorted = (values: Iterable<string>): string[] => [...values].sort();

/**
 * UX-01 (C10.01, WP-18): ui-stream-events.json is everything the durable stream sends the web app, so the web
 * app's render-or-hide test (`wp18-ux01-stream-events.test.ts`) covers every frame Node can send.
 */
describe('UX-01: ui-stream-events.json is all the harness sends the web app', () => {
  it('names every AG-UI frame type of the stream, no more', () => {
    expect(sorted(Object.keys(vector.chat.frames))).to.deep.equal(
      sorted(Object.values(AGUI)),
    );
  });

  it('names every CUSTOM frame name, no more', () => {
    expect(sorted(Object.keys(vector.chat.custom))).to.deep.equal(
      sorted(Object.values(AGUI_CUSTOM)),
    );
  });

  it('every CUSTOM frame Node builds takes its name from AGUI_CUSTOM, never a literal', () => {
    const literal = /type:\s*AGUI\.CUSTOM,\s*name:\s*['"`]/;
    for (const path of files(HARNESS_SRC, '.ts')) {
      expect(literal.test(readFileSync(path, 'utf8')), path).to.equal(false);
    }
  });

  it('the telemetry frames the projector passes through are named in AGUI_CUSTOM', () => {
    const schema = JSON.parse(
      readFileSync(
        join(
          REPO,
          'contracts/harness/ext/pipeshub/events/x.pipeshub.telemetry.recorded.schema.json',
        ),
        'utf8',
      ),
    ) as {
      $defs: {
        PipeshubTelemetryRecordedData: {
          properties: { name: { enum: string[] } };
        };
      };
    };
    const names =
      schema.$defs.PipeshubTelemetryRecordedData.properties.name.enum;
    expect(names).to.have.length.greaterThan(0);
    for (const name of names)
      expect(Object.values(AGUI_CUSTOM), name).to.include(name);
  });

  it('names every STATE_SNAPSHOT status the projector writes', () => {
    const projector = readFileSync(
      join(HARNESS_SRC, 'projection/run-projector.ts'),
      'utf8',
    );
    const literals = [...projector.matchAll(/runState\(\s*'([a-z_]+)'/g)].map(
      (m) => m[1] as string,
    );
    const written = new Set<string>([
      ...literals,
      WAITING_PROVIDER,
      WAITING_SANDBOX,
      WAITING_STORE,
      // A sub-agent's snapshot: running, then its ChildStatus; a root run's end: completed, failed or cancelled.
      'running',
      'completed',
      'failed',
      'cancelled',
    ]);
    expect(literals.length).to.be.greaterThan(2);
    expect(sorted(Object.keys(vector.chat.snapshotStatuses))).to.deep.equal(
      sorted(written),
    );
  });

  it('every frame of the golden streams is in the vector', () => {
    const golden = files(VECTORS, '.frames.json');
    expect(golden.length).to.be.greaterThan(0);
    for (const path of golden) {
      const { frames } = JSON.parse(readFileSync(path, 'utf8')) as {
        frames: Array<Record<string, unknown>>;
      };
      // A golden stream keeps its frames bare or with their SSE id.
      for (const item of frames) {
        const frame = (item['frame'] ?? item) as Record<string, unknown>;
        const type = String(frame['type']);
        expect(vector.chat.frames, `${path} ${type}`).to.have.property(type);
        if (type === AGUI.CUSTOM) {
          expect(vector.chat.custom, path).to.have.property(
            String(frame['name']),
          );
        }
        const snapshot = frame['snapshot'] as
          Record<string, unknown> | undefined;
        if (type === AGUI.STATE_SNAPSHOT && snapshot?.['final'] !== true) {
          expect(vector.chat.snapshotStatuses, path).to.have.property(
            String(snapshot?.['status']),
          );
        }
      }
    }
  });

  it('names every activity stream event the activity service writes', () => {
    const service = readFileSync(
      join(HARNESS_SRC, 'application/run-activity-service.ts'),
      'utf8',
    );
    const written = new Set(
      [...service.matchAll(/this\.write\(\s*'([a-z_]+)'/g)].map(
        (m) => m[1] as string,
      ),
    );
    expect(written.size).to.be.greaterThan(2);
    expect(sorted(Object.keys(vector.activity.events))).to.deep.equal(
      sorted(written),
    );
  });

  it('every entry but CUSTOM and the statuses has an example of its own kind', () => {
    for (const [type, entry] of Object.entries(vector.chat.frames)) {
      if (type === AGUI.CUSTOM) continue;
      expect(entry.example?.['type'], type).to.equal(type);
    }
    for (const [name, entry] of Object.entries(vector.chat.custom)) {
      expect(entry.example?.['type'], name).to.equal(AGUI.CUSTOM);
      expect(entry.example?.['name'], name).to.equal(name);
    }
    for (const [name, entry] of Object.entries(vector.activity.events)) {
      expect(entry.example, name).to.be.an('object');
    }
  });
});
