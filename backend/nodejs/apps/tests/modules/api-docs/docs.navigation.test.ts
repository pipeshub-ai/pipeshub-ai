import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import fs from 'fs';
import os from 'os';
import path from 'path';
import {
  ApiDocsService,
  UnifiedApiDocs,
} from '../../../src/modules/api-docs/docs.service';

describe('ApiDocsService navigation from x-pipeshub-nav', () => {
  const logger: any = {
    info: sinon.stub(),
    warn: sinon.stub(),
    error: sinon.stub(),
    debug: sinon.stub(),
  };
  let dir: string;

  beforeEach(() => {
    dir = fs.mkdtempSync(path.join(os.tmpdir(), 'spec-nav-'));
  });

  afterEach(() => {
    ApiDocsService.clearSpecOverlays();
    fs.rmSync(dir, { recursive: true, force: true });
  });

  async function docsWith(overlayLines?: string[]): Promise<UnifiedApiDocs> {
    if (overlayLines) {
      const overlay = path.join(dir, 'overlay.yaml');
      fs.writeFileSync(overlay, overlayLines.join('\n'));
      ApiDocsService.registerSpecOverlay(overlay);
    }
    const service = new ApiDocsService(logger);
    await service.initialize();
    return service.getUnifiedDocs();
  }

  const tagsInNav = (docs: UnifiedApiDocs): string[] =>
    docs.categories.flatMap((c) => c.modules.flatMap((m) => m.tags));

  it('puts every tag of every endpoint in exactly one nav item', async () => {
    const docs = await docsWith();
    const listed = tagsInNav(docs);

    expect(new Set(listed).size).to.equal(listed.length);
    for (const endpoint of docs.endpoints) {
      for (const tag of endpoint.tags) {
        expect(listed, `${endpoint.method} ${endpoint.path}`).to.include(tag);
      }
    }
  });

  it('builds headings, nav items and accordions in spec order', async () => {
    const docs = await docsWith();
    const search = docs.categories.find((c) => c.name === 'Search & AI')!;
    const enterpriseSearch = search.modules.find(
      (m) => m.name === 'Enterprise Search',
    )!;

    expect(enterpriseSearch.id).to.equal('enterprise-search');
    expect(enterpriseSearch.icon).to.equal('search');
    const listed: string[] = [
      'Conversations',
      'Projects',
      'Code Graph',
      'Semantic Search',
      'Conversational Speech',
    ];
    expect(enterpriseSearch.tags.length).to.be.greaterThan(1);
    expect(enterpriseSearch.tags).to.deep.equal(
      listed.filter((tag) => enterpriseSearch.tags.includes(tag)),
    );
  });

  it('drops listed tags without endpoints, then empty nav items and headings', async () => {
    const docs = await docsWith([
      'x-pipeshub-nav:',
      '  - name: Ghost Heading',
      '    modules:',
      '      - name: Ghost Item',
      '        tags: [No Such Tag]',
    ]);

    expect(docs.categories.map((c) => c.name)).to.not.include('Ghost Heading');
    expect(tagsInNav(docs)).to.not.include('No Such Tag');
  });

  it('keeps the sidebar when an overlay has a null tag or a name, modules or tags of the wrong type', async () => {
    const docs = await docsWith([
      'tags:',
      '  - null',
      'x-pipeshub-nav:',
      '  - name: 123',
      '  - name: Bad Modules',
      '    modules: not-a-list',
      '  - name: System',
      '    modules:',
      '      - name: Bad Tags',
      '        tags: Health',
      '      - name: 456',
    ]);

    expect(docs.categories.map((c) => c.name)).to.include('Search & AI');
    expect(tagsInNav(docs)).to.include('Health');
  });

  it('shows a tag listed nowhere under Other, and merges an overlay into an existing heading', async () => {
    const docs = await docsWith([
      'tags:',
      '  - name: Unlisted',
      '    description: Not in the nav',
      '  - name: Overlay Tag',
      'paths:',
      '  /unlisted/ping:',
      '    get:',
      '      operationId: unlistedPing',
      '      tags: [Unlisted]',
      '  /overlay/ping:',
      '    get:',
      '      operationId: overlayPing',
      '      tags: [Overlay Tag, Users]',
      'x-pipeshub-nav:',
      '  - name: Identity & Access',
      '    modules:',
      '      - name: Overlay Item',
      '        tags: [Overlay Tag]',
    ]);

    const other = docs.categories.find((c) => c.name === 'Other')!;
    const unlisted = other.modules.find((m) => m.name === 'Unlisted')!;
    expect(unlisted).to.include({
      id: 'tag-unlisted',
      description: 'Not in the nav',
    });
    expect(unlisted.tags).to.deep.equal(['Unlisted']);

    expect(
      docs.categories.filter((c) => c.name === 'Identity & Access'),
    ).to.have.length(1);
    const identity = docs.categories.find(
      (c) => c.name === 'Identity & Access',
    )!;
    expect(identity.modules.map((m) => m.name)).to.include('Overlay Item');

    const overlayPing = docs.endpoints.find(
      (e) => e.operationId === 'overlayPing',
    )!;
    expect(overlayPing.tags).to.deep.equal(['Overlay Tag', 'Users']);
    expect(overlayPing.moduleId).to.equal('overlay-item');
  });

  it('takes the base path from the servers of the operation, else of the spec', async () => {
    const docs = await docsWith();
    const basePathOf = (p: string) =>
      docs.endpoints.find((e) => e.path === p)!.basePath;

    expect(basePathOf('/users')).to.equal('/api/v1');
    expect(basePathOf('/mcp')).to.equal('');
    expect(basePathOf('/.well-known/jwks.json')).to.equal('');
  });
});
