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
 * executable the moment a unit-test runner is added to `frontend`
 * (there is currently only a Playwright e2e runner configured).
 */
import { describe, it, expect } from 'vitest';
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

  it('normalizes the record: placeholder (new persisted markers) to no downloadUrl', () => {
    // `_append_task_markers` (streaming.py) stops embedding signed URLs once
    // a marker carries a recordId — it emits `record:{recordId}` in the
    // `(url)` slot instead (a real URL would expire in ~10 min and become
    // permanent dead weight in the saved message). The parser must treat
    // that placeholder as "no direct URL", not as a literal fetchable link.
    const content = '::artifact[report.csv](record:rec-1){text/csv|doc-1|rec-1|SPREADSHEET|2}';
    const { artifacts } = parseArtifactMarkers(content);
    expect(artifacts).toHaveLength(1);
    expect(artifacts[0]).toMatchObject({
      fileName: 'report.csv',
      downloadUrl: '',
      recordId: 'rec-1',
      version: 2,
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

  it('strips short-form ::artifact[name] markers that LLMs hallucinate', () => {
    const content = 'Done – I updated the file.\n\n::artifact[football_rivals_poster.png]';
    const { text, artifacts } = parseArtifactMarkers(content);
    expect(text).toBe('Done – I updated the file.');
    expect(artifacts).toHaveLength(0);
  });

  it('strips short-form ::artifact[name](url) markers without braces', () => {
    const content = 'Output:\n\n::artifact[data.csv](https://example.com/file)';
    const { text, artifacts } = parseArtifactMarkers(content);
    expect(text).toBe('Output:');
    expect(artifacts).toHaveLength(0);
  });

  it('strips short-form markers while preserving full-form ones', () => {
    const content =
      'Here is output.\n\n::artifact[poster.png]\n\n::artifact[chart.png](record:r1){image/png|d1|r1||2}';
    const { text, artifacts } = parseArtifactMarkers(content);
    expect(text).toBe('Here is output.');
    expect(artifacts).toHaveLength(1);
    expect(artifacts[0]).toMatchObject({ fileName: 'chart.png', recordId: 'r1', version: 2 });
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
    // Vitest's jsdom environment defaults to http://localhost:3000/.
    expect(isTrustedApiUrl('/api/v1/record/r1')).toBe(true);
    expect(isTrustedApiUrl('http://localhost:3000/api/v1/record/r1')).toBe(true);
  });

  it('rejects cross-origin attacker URLs', () => {
    expect(isTrustedApiUrl('https://evil.example/steal')).toBe(false);
    expect(isTrustedApiUrl('https://storage.googleapis.com/bucket/file')).toBe(false);
  });

  it('treats a bare relative path/string as same-origin, not as untrusted', () => {
    // `new URL(x, trustedOrigin)` resolves any relative reference against
    // the trusted origin (the same rule that makes `/api/v1/record/r1`
    // above trusted) — so a plain string with no scheme is NOT a rejection
    // case; it is indistinguishable from a relative path on our own origin.
    expect(isTrustedApiUrl('not a url')).toBe(true);
    expect(isTrustedApiUrl('')).toBe(true);
  });

  it('returns false for a URL string the WHATWG URL parser cannot resolve at all', () => {
    expect(isTrustedApiUrl('http://')).toBe(false);
  });
});
