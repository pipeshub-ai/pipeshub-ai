/**
 * SECURITY REGRESSION TESTS for artifact marker parsing.
 *
 * The regex in `parseArtifactMarkers` is intentionally permissive (it is a
 * lossy roundtrip of what the backend appends). These tests lock in the
 * contract that the parser MUST NOT attach auth and MUST NOT emit trusted
 * links when fed an LLM-authored (and potentially attacker-controlled)
 * marker string. Trust decisions live in `isTrustedApiUrl` / `isSignedUrl`;
 * the parser just preserves the raw URL.
 *
 * These tests are written in a vitest/jest-compatible style. They are
 * executable the moment a unit-test runner is added to `frontend-new`
 * (there is currently only a Playwright e2e runner configured).
 */
import {
  parseArtifactMarkers,
  parseDownloadMarkers,
  isSignedUrl,
  isTrustedApiUrl,
} from '../parse-download-markers';

describe('parseArtifactMarkers', () => {
  it('preserves backend-authored markers', () => {
    const content =
      'Here is the chart.\n\n::artifact[chart.png](https://storage.example/s/abc?sig=xyz&se=2030){image/png|doc-1|rec-1}';
    const { text, artifacts } = parseArtifactMarkers(content);
    expect(text).toBe('Here is the chart.');
    expect(artifacts).toHaveLength(1);
    expect(artifacts[0]).toMatchObject({
      fileName: 'chart.png',
      downloadUrl: 'https://storage.example/s/abc?sig=xyz&se=2030',
      mimeType: 'image/png',
      recordId: 'rec-1',
    });
  });

  it('extracts even LLM-authored markers (backend strips them; parser does not trust)', () => {
    // Parser is not the trust boundary — it merely parses. The backend's
    // `_strip_llm_authored_markers` (see test_streaming.py) removes LLM
    // markers before they are persisted. If that layer is bypassed, the UI
    // still gates every URL through `isTrustedApiUrl`/`isSignedUrl` before
    // any network action.
    const evil = '::artifact[payslip.pdf](https://evil.example/steal){application/pdf||}';
    const { artifacts } = parseArtifactMarkers(evil);
    expect(artifacts).toHaveLength(1);
    expect(isTrustedApiUrl(artifacts[0].downloadUrl!)).toBe(false);
    expect(isSignedUrl(artifacts[0].downloadUrl!)).toBe(false);
  });
});

describe('parseDownloadMarkers', () => {
  it('extracts label and url', () => {
    const { text, tasks } = parseDownloadMarkers(
      'Full data: ::download_conversation_task[report.csv](https://our.api/file)',
    );
    expect(text).toBe('Full data:');
    expect(tasks).toEqual([{ fileName: 'report.csv', url: 'https://our.api/file' }]);
  });
});

describe('isSignedUrl', () => {
  it('detects AWS signatures', () => {
    expect(isSignedUrl('https://s3.amazonaws.com/b/k?X-Amz-Signature=abc')).toBe(true);
    expect(isSignedUrl('https://s3.amazonaws.com/b/k?AWSAccessKeyId=AKIA')).toBe(true);
  });

  it('detects Azure SAS tokens', () => {
    expect(isSignedUrl('https://x.blob.core.windows.net/c/b?se=2030-01-01&sig=abc')).toBe(true);
  });

  it('rejects unrelated URLs', () => {
    expect(isSignedUrl('https://evil.example/steal')).toBe(false);
    expect(isSignedUrl('https://our.api/record/r1')).toBe(false);
  });
});

describe('isTrustedApiUrl', () => {
  it('accepts same-origin URLs (window.location.origin)', () => {
    // JSDOM default origin is http://localhost/
    expect(isTrustedApiUrl('/api/v1/record/r1')).toBe(true);
    expect(isTrustedApiUrl('http://localhost/api/v1/record/r1')).toBe(true);
  });

  it('rejects cross-origin attacker URLs', () => {
    expect(isTrustedApiUrl('https://evil.example/steal')).toBe(false);
    expect(isTrustedApiUrl('https://storage.googleapis.com/bucket/file')).toBe(false);
  });

  it('returns false for malformed URLs', () => {
    expect(isTrustedApiUrl('not a url')).toBe(false);
    expect(isTrustedApiUrl('')).toBe(false);
  });
});
