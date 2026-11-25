import { expect } from 'chai';
import {
  classifyCaller,
  releaseVersion,
} from '../../../src/libs/utils/request-client.utils';

const TS_UA = 'speakeasy-sdk/typescript 2.0.1 2.946.0 1.0.0 @pipeshub-ai/sdk';
const PY_UA = 'speakeasy-sdk/python 2.0.1 2.946.0 1.0.0 pipeshub-sdk';
const GO_UA =
  'speakeasy-sdk/go 2.0.1 2.946.0 1.0.0 github.com/pipeshub-ai/pipeshub-sdk-go/v2';
const MCP_UA = 'speakeasy-sdk/mcp-typescript 0.0.4 2.846.1 1.0.0 mcp';
const BROWSER_UA =
  'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36';

/** Every value a classification may hold: closed sets and version-shaped strings. */
function expectBounded(result: Record<string, unknown>): void {
  expect(result.channel).to.be.oneOf(['web', 'sdk', 'mcp', 'api', 'slack', 'cli']);
  if (result.sdkLanguage !== undefined) {
    expect(result.sdkLanguage).to.be.oneOf(['typescript', 'python', 'go', 'unknown']);
  }
  for (const key of ['sdkVersion', 'mcpServerVersion', 'generatorVersion']) {
    const value = result[key];
    if (value === undefined) continue;
    expect(value).to.match(/^(unknown|\d{1,4}\.\d{1,4}\.\d{1,4}[-.a-z0-9]{0,12})$/);
  }
  expect(Object.keys(result)).to.satisfy((keys: string[]) =>
    keys.every((key) =>
      ['channel', 'sdkLanguage', 'sdkVersion', 'generatorVersion', 'mcpServerVersion'].includes(key),
    ),
  );
}

describe('classifyCaller', () => {
  describe('the User-Agent the generated clients send', () => {
    it('reads the TypeScript SDK', () => {
      expect(classifyCaller({ 'user-agent': TS_UA })).to.deep.equal({
        channel: 'sdk',
        sdkLanguage: 'typescript',
        sdkVersion: '2.0.1',
        generatorVersion: '2.946.0',
      });
    });

    it('reads the Python SDK', () => {
      expect(classifyCaller({ 'user-agent': PY_UA })).to.deep.equal({
        channel: 'sdk',
        sdkLanguage: 'python',
        sdkVersion: '2.0.1',
        generatorVersion: '2.946.0',
      });
    });

    it('reads the Go SDK', () => {
      expect(classifyCaller({ 'user-agent': GO_UA })).to.deep.equal({
        channel: 'sdk',
        sdkLanguage: 'go',
        sdkVersion: '2.0.1',
        generatorVersion: '2.946.0',
      });
    });

    it('reads the MCP server as its own channel, not as an SDK', () => {
      expect(classifyCaller({ 'user-agent': MCP_UA })).to.deep.equal({
        channel: 'mcp',
        mcpServerVersion: '0.0.4',
        generatorVersion: '2.846.1',
      });
    });

    it('calls a language we do not publish unknown, and a malformed version unknown', () => {
      expect(
        classifyCaller({ 'user-agent': 'speakeasy-sdk/cobol latest 2.946.0 1.0.0 pkg' }),
      ).to.deep.equal({
        channel: 'sdk',
        sdkLanguage: 'unknown',
        sdkVersion: 'unknown',
        generatorVersion: '2.946.0',
      });
    });
  });

  describe('the request-id prefix', () => {
    const cases: [string, Record<string, string>][] = [
      ['sdk-ts-', { channel: 'sdk', sdkLanguage: 'typescript', sdkVersion: 'unknown' }],
      ['sdk-py-', { channel: 'sdk', sdkLanguage: 'python', sdkVersion: 'unknown' }],
      ['sdk-go-', { channel: 'sdk', sdkLanguage: 'go', sdkVersion: 'unknown' }],
      ['mcp-', { channel: 'mcp', mcpServerVersion: 'unknown' }],
    ];
    for (const [prefix, expected] of cases) {
      it(`reads ${prefix} when there is no User-Agent, and from a browser`, () => {
        const id = `${prefix}0f8fad5bd9cb469fa16570867728950e`;
        expect(classifyCaller({ 'x-request-id': id })).to.deep.equal(expected);
        expect(
          classifyCaller({ 'x-request-id': id, 'user-agent': BROWSER_UA }),
        ).to.deep.equal(expected);
      });
    }

    it('is not believed from a server-side caller that only passes the id on', () => {
      expect(
        classifyCaller({
          'x-request-id': 'sdk-py-0f8fad5bd9cb469fa16570867728950e',
          'user-agent': 'python-httpx/0.27.0',
        }),
      ).to.deep.equal({ channel: 'api' });
    });

    it('ignores an id that only contains a prefix somewhere else', () => {
      expect(classifyCaller({ 'x-request-id': 'anon-sdk-ts-1' })).to.deep.equal({
        channel: 'api',
      });
    });
  });

  describe('X-PipesHub-Client', () => {
    it('wins over the User-Agent and the request id', () => {
      expect(
        classifyCaller({
          'x-pipeshub-client': 'pipeshub-sdk-python/2.3.0',
          'user-agent': TS_UA,
          'x-request-id': 'sdk-go-1',
        }),
      ).to.deep.equal({ channel: 'sdk', sdkLanguage: 'python', sdkVersion: '2.3.0' });
    });

    it('gives the MCP server its real version and keeps the generator version of its User-Agent', () => {
      expect(
        classifyCaller({ 'x-pipeshub-client': 'pipeshub-mcp/1.4.2', 'user-agent': MCP_UA }),
      ).to.deep.equal({
        channel: 'mcp',
        mcpServerVersion: '1.4.2',
        generatorVersion: '2.846.1',
      });
    });

    it('gives a browser SDK its version', () => {
      expect(
        classifyCaller({
          'x-pipeshub-client': 'pipeshub-sdk-typescript/2.0.1',
          'user-agent': BROWSER_UA,
          'x-request-id': 'sdk-ts-1',
        }),
      ).to.deep.equal({ channel: 'sdk', sdkLanguage: 'typescript', sdkVersion: '2.0.1' });
    });

    it('keeps the channel of a well-formed name with an unknown language or version', () => {
      expect(classifyCaller({ 'x-pipeshub-client': 'pipeshub-sdk-rust/1.0.0' })).to.deep.equal({
        channel: 'sdk',
        sdkLanguage: 'unknown',
        sdkVersion: '1.0.0',
      });
      expect(classifyCaller({ 'x-pipeshub-client': 'pipeshub-sdk-go/main' })).to.deep.equal({
        channel: 'sdk',
        sdkLanguage: 'go',
        sdkVersion: 'unknown',
      });
    });

    it('is ignored when it is not ours, and the next source is used', () => {
      for (const value of ['acme-sdk/1.0.0', 'pipeshub-sdk-python', 'pipeshub-sdk-python/1.0.0 extra', '']) {
        expect(
          classifyCaller({ 'x-pipeshub-client': value, 'user-agent': GO_UA }),
        ).to.include({ channel: 'sdk', sdkLanguage: 'go', sdkVersion: '2.0.1' });
      }
    });
  });

  describe('X-PipesHub-Channel', () => {
    it('decides the channel when it is a known one', () => {
      expect(classifyCaller({ 'x-pipeshub-channel': 'slack' })).to.deep.equal({ channel: 'slack' });
      expect(
        classifyCaller({ 'x-pipeshub-channel': 'cli', 'user-agent': BROWSER_UA }),
      ).to.deep.equal({ channel: 'cli' });
    });

    it('drops SDK details that belong to another channel', () => {
      expect(
        classifyCaller({ 'x-pipeshub-channel': 'web', 'user-agent': TS_UA }),
      ).to.deep.equal({ channel: 'web' });
    });

    it('keeps SDK details when it agrees with them', () => {
      expect(
        classifyCaller({ 'x-pipeshub-channel': 'sdk', 'user-agent': PY_UA }),
      ).to.include({ channel: 'sdk', sdkLanguage: 'python', sdkVersion: '2.0.1' });
    });

    it('is ignored when it is any other tag', () => {
      expect(
        classifyCaller({ 'x-pipeshub-channel': 'docs-quickstart', 'user-agent': BROWSER_UA }),
      ).to.deep.equal({ channel: 'web' });
      expect(classifyCaller({ 'x-pipeshub-channel': 'SDK' })).to.deep.equal({ channel: 'api' });
    });
  });

  describe('everything else', () => {
    it('is web from a browser and api otherwise', () => {
      expect(classifyCaller({ 'user-agent': BROWSER_UA })).to.deep.equal({ channel: 'web' });
      expect(classifyCaller({ 'user-agent': 'curl/8.4.0' })).to.deep.equal({ channel: 'api' });
      expect(classifyCaller({})).to.deep.equal({ channel: 'api' });
    });
  });

  describe('hostile and oversized input', () => {
    const hostile: Record<string, unknown>[] = [
      { 'user-agent': `speakeasy-sdk/typescript ${'9'.repeat(5000)} 1.0.0 1.0.0 pkg` },
      { 'user-agent': `speakeasy-sdk/typescript 2.0.1 2.946.0 1.0.0 ${'a'.repeat(600)}` },
      { 'user-agent': 'speakeasy-sdk/typescript 2.0.1"}\nevil{org="x 2.946.0 1.0.0 pkg' },
      { 'user-agent': 'speakeasy-sdk/typescript 2.0.1-<script>alert(1)</script> 2.946.0 1.0.0 pkg' },
      { 'user-agent': 'speakeasy-sdk/python 2.0.1-ada@example.com 2.946.0 1.0.0 pkg' },
      { 'user-agent': 'speakeasy-sdk/../../etc 1.0.0 1.0.0 1.0.0 pkg' },
      { 'user-agent': ['speakeasy-sdk/go 2.0.1 2.946.0 1.0.0 pkg', 'x'] },
      { 'user-agent': `Mozilla/${'a'.repeat(100_000)}` },
      { 'x-pipeshub-client': `pipeshub-sdk-python/${'1'.repeat(4000)}` },
      { 'x-pipeshub-client': 'pipeshub-sdk-python/2.0.1\r\nX-Injected: 1' },
      { 'x-pipeshub-client': 'pipeshub-sdk-ada@example.com/2.0.1' },
      { 'x-pipeshub-client': 'pipeshub-mcp/1.0.0-anything-goes-here' },
      { 'x-pipeshub-client': ['pipeshub-mcp/1.0.0', 'pipeshub-mcp/2.0.0'] },
      { 'x-pipeshub-client': 42 },
      { 'x-pipeshub-channel': '{"$ne":null}' },
      { 'x-pipeshub-channel': 'constructor' },
      { 'x-pipeshub-channel': '__proto__' },
      { 'x-request-id': `sdk-ts-${'x'.repeat(100_000)}` },
      { 'x-request-id': ['sdk-ts-1', 'mcp-1'] },
      { 'x-request-id': null },
    ];

    for (const [index, headers] of hostile.entries()) {
      it(`never yields a value outside the closed sets (#${index + 1})`, () => {
        expectBounded(classifyCaller(headers as never) as never);
      });
    }

    it('does not parse an oversized header at all', () => {
      expect(
        classifyCaller({ 'user-agent': `${TS_UA}${' '.repeat(600)}` }),
      ).to.deep.equal({ channel: 'api' });
      expect(
        classifyCaller({ 'x-request-id': `sdk-ts-${'x'.repeat(600)}` }),
      ).to.deep.equal({ channel: 'api' });
    });

    it('does not take a channel from an inherited object property', () => {
      expect(classifyCaller({ 'x-pipeshub-channel': 'constructor' })).to.deep.equal({ channel: 'api' });
      expect(
        classifyCaller({ 'user-agent': 'speakeasy-sdk/constructor 1.0.0 1.0.0 1.0.0 pkg' }),
      ).to.include({ sdkLanguage: 'unknown' });
    });

    it('answers quickly on input built to make a pattern backtrack', () => {
      const started = Date.now();
      classifyCaller({ 'user-agent': `speakeasy-sdk/${'a-'.repeat(250)}` });
      classifyCaller({ 'x-pipeshub-client': `pipeshub-sdk-${'a'.repeat(490)}` });
      classifyCaller({ 'user-agent': `speakeasy-sdk/go ${'1.'.repeat(240)}` });
      expect(Date.now() - started).to.be.lessThan(100);
    });
  });

  describe('releaseVersion', () => {
    it('accepts the shapes our packages use, without the leading v', () => {
      expect(releaseVersion('2.0.1')).to.equal('2.0.1');
      expect(releaseVersion('v2.0.1')).to.equal('2.0.1');
      expect(releaseVersion('2.0.1-rc.1')).to.equal('2.0.1-rc.1');
      expect(releaseVersion('2.0.1rc1')).to.equal('2.0.1rc1');
      expect(releaseVersion('2.0.1.dev3')).to.equal('2.0.1.dev3');
      expect(releaseVersion('1.2.3-beta')).to.equal('1.2.3-beta');
    });

    it('refuses everything else', () => {
      for (const value of ['', '2', '2.0', '2.0.1.2.3', '2.0.1-custom', '2.0.1+build.5', '12345.0.0', ' 2.0.1', '2.0.1 ', 'latest', undefined, 7, null]) {
        expect(releaseVersion(value)).to.equal(undefined);
      }
    });
  });
});
