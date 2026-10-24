import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import fs from 'fs';
import os from 'os';
import path from 'path';
import { ApiDocsService } from '../../../src/modules/api-docs/docs.service';

describe('ApiDocsService spec overlays', () => {
  const logger: any = { info: sinon.stub(), warn: sinon.stub(), error: sinon.stub(), debug: sinon.stub() };
  let dir: string;

  beforeEach(() => {
    dir = fs.mkdtempSync(path.join(os.tmpdir(), 'spec-overlay-'));
  });

  afterEach(() => {
    ApiDocsService.clearSpecOverlays();
    fs.rmSync(dir, { recursive: true, force: true });
  });

  async function combinedSpec(): Promise<any> {
    const service = new ApiDocsService(logger);
    await service.initialize();
    return service.getCombinedSpec();
  }

  it('leaves the spec as loaded when no overlay is registered', async () => {
    const before = await combinedSpec();

    expect(before.paths['/overlay/ping']).to.equal(undefined);
  });

  it('adds the paths, schema properties and tags of a registered overlay', async () => {
    const base = await combinedSpec();
    const schemaName = Object.keys(base.components.schemas)[0] as string;
    const overlay = path.join(dir, 'overlay.yaml');
    fs.writeFileSync(
      overlay,
      [
        'tags:',
        '  - name: Overlay',
        'paths:',
        '  /overlay/ping:',
        '    get:',
        '      summary: Ping',
        'components:',
        '  schemas:',
        `    ${schemaName}:`,
        '      properties:',
        '        overlayField:',
        '          type: string',
      ].join('\n'),
    );
    ApiDocsService.registerSpecOverlay(overlay);

    const merged = await combinedSpec();

    expect(merged.paths['/overlay/ping'].get.summary).to.equal('Ping');
    expect(merged.components.schemas[schemaName].properties.overlayField.type).to.equal('string');
    expect(merged.components.schemas[schemaName].properties).to.include.keys(
      Object.keys(base.components.schemas[schemaName].properties ?? {}),
    );
    expect(merged.tags.map((t: any) => t.name)).to.include('Overlay');
    expect(Object.keys(merged.paths).length).to.equal(Object.keys(base.paths).length + 1);
  });

  it('ignores __proto__, constructor and prototype keys in an overlay', async () => {
    const overlay = path.join(dir, 'polluting.yaml');
    // Written as text: a __proto__ key in an object literal sets the prototype instead.
    fs.writeFileSync(
      overlay,
      '{"__proto__": {"polluted": "yes"}, "constructor": {"prototype": {"polluted2": "yes"}},' +
        ' "paths": {"/overlay/safe": {"get": {"summary": "Safe"}}}}',
    );
    ApiDocsService.registerSpecOverlay(overlay);

    try {
      const merged = await combinedSpec();

      expect(({} as Record<string, unknown>).polluted).to.equal(undefined);
      expect(({} as Record<string, unknown>).polluted2).to.equal(undefined);
      expect(Object.prototype.hasOwnProperty.call(merged, '__proto__')).to.equal(false);
      expect(Object.getPrototypeOf(merged)).to.equal(Object.prototype);
      expect(merged.paths['/overlay/safe'].get.summary).to.equal('Safe');
    } finally {
      delete (Object.prototype as Record<string, unknown>).polluted;
      delete (Object.prototype as Record<string, unknown>).polluted2;
    }
  });

  it('drops a __proto__ key nested in an object that replaces a non-object value', async () => {
    const first = path.join(dir, 'a-null.yaml');
    const second = path.join(dir, 'b-replace.yaml');
    fs.writeFileSync(first, '{"paths": null}');
    // Written as text: a __proto__ key in an object literal sets the prototype instead.
    fs.writeFileSync(
      second,
      '{"paths": {"__proto__": {"polluted": "yes"}, "/overlay/kept": {"get": {"summary": "Kept"}}}}',
    );
    ApiDocsService.registerSpecOverlay(first);
    ApiDocsService.registerSpecOverlay(second);

    const { paths } = await combinedSpec();

    expect(Object.getPrototypeOf(paths)).to.equal(Object.prototype);
    expect(paths.polluted).to.equal(undefined);
    expect(paths['/overlay/kept'].get.summary).to.equal('Kept');
  });

  it('skips an unreadable overlay and still applies the others', async () => {
    const good = path.join(dir, 'good.yaml');
    fs.writeFileSync(good, ['paths:', '  /overlay/ok:', '    get:', '      summary: Ok', ''].join('\n'));
    ApiDocsService.registerSpecOverlay(path.join(dir, 'missing.yaml'));
    ApiDocsService.registerSpecOverlay(good);

    const merged = await combinedSpec();

    expect(merged.paths['/overlay/ok']).to.have.property('get');
    expect(Object.keys(merged.paths).length).to.be.greaterThan(1);
  });
});
