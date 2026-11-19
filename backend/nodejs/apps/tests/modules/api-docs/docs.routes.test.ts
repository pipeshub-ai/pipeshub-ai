import 'reflect-metadata';
import { expect } from 'chai';
import fs from 'fs';
import path from 'path';
import vm from 'vm';
import { createApiDocsRouter } from '../../../src/modules/api-docs/docs.routes';

// The docs page is one HTML string with its script inline, so a syntax error there only shows in a browser.
// Run that script in a VM and call its helpers directly.
describe('API docs page script', () => {
  let page: Record<string, any>;
  let html = '';

  before(() => {
    const router = createApiDocsRouter({ get: () => ({}) } as any);
    const layer = (router as any).stack.find((l: any) => l.route?.path === '*');
    layer.route.stack[0].handle(
      {},
      {
        send: (body: string) => {
          html = body;
        },
      },
    );
    const script = html.slice(
      html.lastIndexOf('<script>') + '<script>'.length,
      html.lastIndexOf('</script>'),
    );

    page = {
      document: {
        addEventListener: () => undefined,
        getElementById: () => null,
      },
      window: { location: { origin: 'http://pipeshub.test' } },
      localStorage: { getItem: () => null },
      console,
      performance,
    };
    vm.createContext(page);
    vm.runInContext(script, page);
  });

  describe('fonts', () => {
    // An install with no route to the internet cannot reach fonts.googleapis.com.
    it('loads no font or stylesheet from another origin', () => {
      expect(html).to.not.match(/googleapis\.com|gstatic\.com/);
      expect(html).to.not.match(/<link[^>]+rel="stylesheet"/);
    });

    it('serves every font file it names from an installed package', () => {
      const names = [...html.matchAll(/\/api\/v1\/docs\/fonts\/(manrope|material-icons)\/([\w.-]+\.woff2)/g)];
      expect(names.length).to.be.greaterThan(0);
      for (const [, pkg, file] of names) {
        const dir =
          pkg === 'manrope'
            ? path.join(path.dirname(require.resolve('@fontsource/manrope/package.json')), 'files')
            : path.join(path.dirname(require.resolve('material-icons/package.json')), 'iconfont');
        expect(fs.existsSync(path.join(dir, file)), `${pkg}/${file}`).to.equal(true);
      }
    });

    it('mounts a static route for each font package', () => {
      const router = createApiDocsRouter({ get: () => ({}) } as any);
      const mounted = (router as any).stack
        .filter((l: any) => l.name === 'serveStatic')
        .map((l: any) => String(l.regexp ?? l.matchers?.[0]?.toString?.()));
      expect(mounted.length).to.equal(2);
    });
  });

  describe('renderMarkdown', () => {
    it('escapes HTML in the text instead of rendering it', () => {
      const html = page.renderMarkdown(
        '<img src=x onerror=alert(1)> and <script>alert(1)</script>',
      );
      expect(html).to.not.include('<img');
      expect(html).to.not.include('<script');
      expect(html).to.include('&lt;img src=x onerror=alert(1)&gt;');
    });

    it('links only http(s), relative and fragment targets', () => {
      expect(
        page.renderMarkdown('[docs](https://docs.pipeshub.com)'),
      ).to.include('<a href="https://docs.pipeshub.com"');
      expect(page.renderMarkdown('[x](javascript:alert(1))')).to.not.include(
        '<a ',
      );
    });

    it('keeps quotes from breaking out of a link attribute', () => {
      expect(page.renderMarkdown('[x](/a"onmouseover="alert(1))')).to.not.match(
        /href="[^"]*"onmouseover/,
      );
    });

    it('renders paragraphs, bullet lists with continuation lines, and bold', () => {
      const html = page.renderMarkdown(
        '**Rules:**\n- `email`: Required\n- `password`: only for\n  demo personas\n\nAfter.',
      );
      expect(html).to.equal(
        '<p><strong>Rules:</strong></p>' +
          '<ul><li><code>email</code>: Required</li><li><code>password</code>: only for demo personas</li></ul>' +
          '<p>After.</p>',
      );
    });

    it('does not format inside code spans or fenced code', () => {
      expect(page.renderMarkdown('`**not bold**`')).to.equal(
        '<p><code>**not bold**</code></p>',
      );
      expect(page.renderMarkdown('```json\n{"a": "<b>"}\n```')).to.equal(
        '<pre><code>{&quot;a&quot;: &quot;&lt;b&gt;&quot;}</code></pre>',
      );
    });

    it('turns headings into sub-headings of the page', () => {
      expect(page.renderMarkdown('## Authentication')).to.equal(
        '<h4>Authentication</h4>',
      );
    });
  });

  describe('generateExample', () => {
    it('builds the body from properties when a oneOf/anyOf only constrains them', () => {
      const example = page.generateExample({
        type: 'object',
        required: ['email'],
        properties: {
          email: { type: 'string', example: 'john@company.com' },
          role: { type: 'string', enum: ['admin', 'member'] },
          password: { type: 'string' },
        },
        anyOf: [
          { not: { required: ['password'] } },
          { properties: { email: { pattern: '@demo$' } } },
        ],
      });
      expect(example).to.deep.equal({
        email: 'john@company.com',
        role: 'admin',
      });
    });

    it('keeps every field when none is required or documented', () => {
      expect(
        page.generateExample({
          type: 'object',
          properties: { name: { type: 'string' }, size: { type: 'integer' } },
        }),
      ).to.deep.equal({ name: 'string', size: 0 });
    });
  });

  describe('code examples', () => {
    it('show a placeholder host, not the host serving the docs', () => {
      expect(page.exampleBaseUrl()).to.equal('https://your-pipeshub-host');
    });

    it('show the base URL the reader typed in Try It', () => {
      const { getElementById } = page.document;
      page.document.getElementById = (id: string) =>
        id === 'baseUrlInput' ? { value: 'https://pipeshub.example.com/' } : null;
      try {
        expect(page.exampleBaseUrl()).to.equal('https://pipeshub.example.com');
      } finally {
        page.document.getElementById = getElementById;
      }
    });
  });

  it('does not call the service health endpoints', () => {
    expect(html).to.not.include('/api/v1/health');
  });

  it('links the OpenAPI spec download from the header', () => {
    expect(html).to.include('href="/api/v1/docs/openapi.yaml" download="pipeshub-openapi.yaml"');
  });

  it('writes JSON as a Python literal without touching strings', () => {
    expect(page.toPython({ a: true, b: null, c: 'true or null' })).to.equal(
      '{\n  "a": True,\n  "b": None,\n  "c": "true or null"\n}',
    );
  });
});

describe('API docs spec download', () => {
  const download = (specYaml: string | null) => {
    const router = createApiDocsRouter({ get: () => ({ getSpecYaml: () => specYaml }) } as any);
    const layer = (router as any).stack.find((l: any) => l.route?.path === '/openapi.yaml');
    const res: Record<string, any> = { headers: {} as Record<string, string> };
    res.status = (code: number) => { res.statusCode = code; return res; };
    res.attachment = (name: string) => { res.headers['content-disposition'] = 'attachment; filename="' + name + '"'; return res; };
    res.type = (type: string) => { res.headers['content-type'] = type; return res; };
    res.send = (body: string) => { res.body = body; return res; };
    res.json = (body: unknown) => { res.body = body; return res; };
    layer.route.stack[0].handle({}, res);
    return res;
  };

  it('sends the spec as a YAML attachment', () => {
    const res = download('openapi: 3.0.0\n');
    expect(res.statusCode).to.be.undefined;
    expect(res.headers['content-type']).to.equal('application/yaml');
    expect(res.headers['content-disposition']).to.equal('attachment; filename="pipeshub-openapi.yaml"');
    expect(res.body).to.equal('openapi: 3.0.0\n');
  });

  it('answers 404 when no spec is loaded', () => {
    expect(download(null).statusCode).to.equal(404);
  });
});
