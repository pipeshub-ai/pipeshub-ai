import 'reflect-metadata';
import { expect } from 'chai';
import vm from 'vm';
import { createApiDocsRouter } from '../../../src/modules/api-docs/docs.routes';

// The docs page is one HTML string with its script inline, so a syntax error there only shows in a browser.
// Run that script in a VM and call its helpers directly.
describe('API docs page script', () => {
  let page: Record<string, any>;

  before(() => {
    const router = createApiDocsRouter({ get: () => ({}) } as any);
    const layer = (router as any).stack.find((l: any) => l.route?.path === '*');
    let html = '';
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

  it('writes JSON as a Python literal without touching strings', () => {
    expect(page.toPython({ a: true, b: null, c: 'true or null' })).to.equal(
      '{\n  "a": True,\n  "b": None,\n  "c": "true or null"\n}',
    );
  });
});
