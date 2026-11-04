import { expect } from 'chai';
import { classifyRequestId } from '../../../../src/libs/services/telemetry/client';

describe('telemetry client', () => {
  it('should read the SDK language from sdk-<lang>- ids', () => {
    expect(classifyRequestId('sdk-py-u1-abc')).to.deep.equal({ client: 'sdk', sdk: 'py' });
    expect(classifyRequestId('sdk-ts-abc')).to.deep.equal({ client: 'sdk', sdk: 'ts' });
    expect(classifyRequestId('sdk-go-abc')).to.deep.equal({ client: 'sdk', sdk: 'go' });
  });

  it('should bound an unknown SDK language', () => {
    expect(classifyRequestId('sdk-rust-abc')).to.deep.equal({ client: 'sdk', sdk: 'other' });
  });

  it('should treat mcp- ids as MCP', () => {
    expect(classifyRequestId('mcp-u1-abc')).to.deep.equal({ client: 'mcp', sdk: 'none' });
  });

  it('should treat the web app, anonymous and system ids as other', () => {
    for (const id of ['665f0c2a1b2c3d4e5f607182-V1StGXR8_Z5jdHi6B-myT', 'anon-abc', 'sys-abc', 'sdkfoo', undefined]) {
      expect(classifyRequestId(id).client).to.equal('other');
    }
  });
});
