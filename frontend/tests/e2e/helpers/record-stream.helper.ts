import { execFileSync } from 'child_process';
import JSZip from 'jszip';
import * as XLSX from 'xlsx';
import type { Page, Response } from '@playwright/test';

/**
 * Counts what the browser asked of the record stream route, and what the server converted.
 *
 * "One body" means exactly one 200 or 206 answer with a non-empty body for the record.
 */

const STREAM_RE = /\/api\/v1\/knowledgeBase\/stream\/record\/([^/?#]+)/;

export interface StreamCall {
  recordId: string;
  status: number;
  /** Length of the body the browser received; 0 for a 304. */
  bytes: number;
  ifNoneMatch: string | null;
  range: string | null;
  convertTo: string | null;
  etag: string | null;
  cacheControl: string | null;
}

export interface StreamLog {
  calls: StreamCall[];
  /** Waits for every response seen so far to finish reading its body. */
  settled(): Promise<void>;
  forRecord(recordId: string): StreamCall[];
  /** 200 or 206 answers with a body. */
  bodies(recordId?: string): StreamCall[];
  reset(): void;
}

export function recordStreamLog(page: Page): StreamLog {
  const calls: StreamCall[] = [];
  const pending: Promise<void>[] = [];

  const onResponse = (response: Response) => {
    const url = new URL(response.url());
    const match = STREAM_RE.exec(url.pathname);
    if (!match || response.request().method() !== 'GET') return;
    const req = response.request().headers();
    const status = response.status();
    const entry: StreamCall = {
      recordId: decodeURIComponent(match[1]),
      status,
      bytes: 0,
      ifNoneMatch: req['if-none-match'] ?? null,
      range: req['range'] ?? null,
      convertTo: url.searchParams.get('convertTo'),
      etag: response.headers()['etag'] ?? null,
      cacheControl: response.headers()['cache-control'] ?? null,
    };
    calls.push(entry);
    if (status === 304) return;
    pending.push(
      response
        .body()
        .then((b) => {
          entry.bytes = b.length;
        })
        // A body the browser dropped (an aborted load) counts as no body.
        .catch(() => undefined),
    );
  };
  page.on('response', onResponse);

  return {
    calls,
    async settled() {
      await Promise.all(pending.splice(0));
    },
    forRecord: (id) => calls.filter((c) => c.recordId === id),
    bodies: (id) => calls.filter((c) => (c.status === 200 || c.status === 206) && c.bytes > 0 && (!id || c.recordId === id)),
    reset() {
      calls.length = 0;
    },
  };
}

/**
 * Number of `record_preview_conversion` events the connectors service logged since `since`.
 * Throws when the log cannot be read: a test that cannot count conversions must not pass.
 *
 * E2E_COMPOSE_FILE (default deployment/docker-compose/docker-compose.integration.neo4j.yml, relative to the
 * repository root) and E2E_COMPOSE_SERVICE (default pipeshub-ai) name the stack.
 */
export function countConversions(since: Date): number {
  const file = process.env.E2E_COMPOSE_FILE ?? '../deployment/docker-compose/docker-compose.integration.neo4j.yml';
  const service = process.env.E2E_COMPOSE_SERVICE ?? 'pipeshub-ai';
  let out: string;
  try {
    out = execFileSync('docker', ['compose', '-f', file, 'logs', '--no-color', '--since', since.toISOString(), service], {
      encoding: 'utf8',
      maxBuffer: 256 * 1024 * 1024,
    });
  } catch (error) {
    throw new Error(`cannot read the ${service} logs to count conversions: ${(error as Error).message}`);
  }
  return out.split('\n').filter((l) => l.includes('record_preview_conversion')).length;
}

// ── Fixture documents, generated so no binary is committed ───────────────────

/** A PDF with `pages` pages, each carrying "PAGE-n" text, padded to about `targetBytes`. */
export function makePdf(pages: number, targetBytes = 0): Buffer {
  const objs: string[] = [];
  objs.push('<< /Type /Catalog /Pages 2 0 R >>');
  const kids = Array.from({ length: pages }, (_, i) => `${4 + i * 2} 0 R`).join(' ');
  objs.push(`<< /Type /Pages /Kids [${kids}] /Count ${pages} >>`);
  objs.push('<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>');
  for (let i = 0; i < pages; i++) {
    const text = `BT /F1 36 Tf 72 700 Td (PAGE-${i + 1}) Tj ET`;
    objs.push(`<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 3 0 R >> >> /Contents ${5 + i * 2} 0 R >>`);
    objs.push(`<< /Length ${text.length} >>\nstream\n${text}\nendstream`);
  }
  const used = objs.reduce((n, o) => n + o.length + 20, 64);
  if (targetBytes > used) {
    const pad = ' '.repeat(targetBytes - used - 40);
    objs.push(`<< /Length ${pad.length} >>\nstream\n${pad}\nendstream`);
  }
  let out = '%PDF-1.4\n';
  const offsets: number[] = [];
  objs.forEach((o, i) => {
    offsets.push(out.length);
    out += `${i + 1} 0 obj\n${o}\nendobj\n`;
  });
  const xref = out.length;
  out += `xref\n0 ${objs.length + 1}\n0000000000 65535 f \n`;
  for (const off of offsets) out += `${String(off).padStart(10, '0')} 00000 n \n`;
  out += `trailer\n<< /Size ${objs.length + 1} /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF\n`;
  return Buffer.from(out, 'latin1');
}

const CT_NS = 'http://schemas.openxmlformats.org/package/2006/content-types';
const REL_NS = 'http://schemas.openxmlformats.org/package/2006/relationships';
const DOC_REL = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships';

/** A DOCX with `paragraphs` paragraphs, each "PARA-n" followed by filler. */
export async function makeDocx(paragraphs: number): Promise<Buffer> {
  const zip = new JSZip();
  zip.file('[Content_Types].xml', `<?xml version="1.0" encoding="UTF-8"?><Types xmlns="${CT_NS}"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>`);
  zip.file('_rels/.rels', `<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="${REL_NS}"><Relationship Id="rId1" Type="${DOC_REL}/officeDocument" Target="word/document.xml"/></Relationships>`);
  const body = Array.from({ length: paragraphs }, (_, i) => {
    const filler = 'lorem ipsum dolor sit amet '.repeat(30);
    return `<w:p><w:r><w:t>PARA-${i + 1} ${filler}</w:t></w:r></w:p>`;
  }).join('');
  zip.file('word/document.xml', `<?xml version="1.0" encoding="UTF-8"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>${body}</w:body></w:document>`);
  return zip.generateAsync({ type: 'nodebuffer' });
}

/** A PPTX with `slides` slides, each titled "SLIDE-n". The server converts it to PDF for the preview. */
export async function makePptx(slides: number): Promise<Buffer> {
  const P = 'http://schemas.openxmlformats.org/presentationml/2006/main';
  const A = 'http://schemas.openxmlformats.org/drawingml/2006/main';
  const R = DOC_REL;
  const zip = new JSZip();
  const slideOverrides = Array.from({ length: slides }, (_, i) => `<Override PartName="/ppt/slides/slide${i + 1}.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/>`).join('');
  zip.file('[Content_Types].xml', `<?xml version="1.0" encoding="UTF-8"?><Types xmlns="${CT_NS}"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/ppt/presentation.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/><Override PartName="/ppt/slideMasters/slideMaster1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideMaster+xml"/><Override PartName="/ppt/slideLayouts/slideLayout1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideLayout+xml"/>${slideOverrides}</Types>`);
  zip.file('_rels/.rels', `<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="${REL_NS}"><Relationship Id="rId1" Type="${R}/officeDocument" Target="ppt/presentation.xml"/></Relationships>`);
  const slideIds = Array.from({ length: slides }, (_, i) => `<p:sldId id="${256 + i}" r:id="rId${10 + i}"/>`).join('');
  zip.file('ppt/presentation.xml', `<?xml version="1.0" encoding="UTF-8"?><p:presentation xmlns:p="${P}" xmlns:a="${A}" xmlns:r="${R}"><p:sldMasterIdLst><p:sldMasterId id="2147483648" r:id="rId1"/></p:sldMasterIdLst><p:sldIdLst>${slideIds}</p:sldIdLst><p:sldSz cx="9144000" cy="6858000"/><p:notesSz cx="6858000" cy="9144000"/></p:presentation>`);
  const slideRels = Array.from({ length: slides }, (_, i) => `<Relationship Id="rId${10 + i}" Type="${R}/slide" Target="slides/slide${i + 1}.xml"/>`).join('');
  zip.file('ppt/_rels/presentation.xml.rels', `<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="${REL_NS}"><Relationship Id="rId1" Type="${R}/slideMaster" Target="slideMasters/slideMaster1.xml"/>${slideRels}</Relationships>`);
  const tree = '<p:cSld><p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr/></p:spTree></p:cSld>';
  zip.file('ppt/slideMasters/slideMaster1.xml', `<?xml version="1.0" encoding="UTF-8"?><p:sldMaster xmlns:p="${P}" xmlns:a="${A}" xmlns:r="${R}">${tree}<p:clrMap bg1="lt1" tx1="dk1" bg2="lt2" tx2="dk2" accent1="accent1" accent2="accent2" accent3="accent3" accent4="accent4" accent5="accent5" accent6="accent6" hlink="hlink" folHlink="folHlink"/><p:sldLayoutIdLst><p:sldLayoutId id="2147483649" r:id="rId1"/></p:sldLayoutIdLst></p:sldMaster>`);
  zip.file('ppt/slideMasters/_rels/slideMaster1.xml.rels', `<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="${REL_NS}"><Relationship Id="rId1" Type="${R}/slideLayout" Target="../slideLayouts/slideLayout1.xml"/></Relationships>`);
  zip.file('ppt/slideLayouts/slideLayout1.xml', `<?xml version="1.0" encoding="UTF-8"?><p:sldLayout xmlns:p="${P}" xmlns:a="${A}" xmlns:r="${R}" type="blank">${tree}</p:sldLayout>`);
  zip.file('ppt/slideLayouts/_rels/slideLayout1.xml.rels', `<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="${REL_NS}"><Relationship Id="rId1" Type="${R}/slideMaster" Target="../slideMasters/slideMaster1.xml"/></Relationships>`);
  for (let i = 0; i < slides; i++) {
    zip.file(`ppt/slides/slide${i + 1}.xml`, `<?xml version="1.0" encoding="UTF-8"?><p:sld xmlns:p="${P}" xmlns:a="${A}" xmlns:r="${R}"><p:cSld><p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr/><p:sp><p:nvSpPr><p:cNvPr id="2" name="Title"/><p:cNvSpPr txBox="1"/><p:nvPr/></p:nvSpPr><p:spPr><a:xfrm><a:off x="500000" y="500000"/><a:ext cx="8000000" cy="1000000"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom></p:spPr><p:txBody><a:bodyPr/><a:lstStyle/><a:p><a:r><a:rPr lang="en-US" sz="4000"/><a:t>SLIDE-${i + 1}</a:t></a:r></a:p></p:txBody></p:sp></p:spTree></p:cSld></p:sld>`);
    zip.file(`ppt/slides/_rels/slide${i + 1}.xml.rels`, `<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="${REL_NS}"><Relationship Id="rId1" Type="${R}/slideLayout" Target="../slideLayouts/slideLayout1.xml"/></Relationships>`);
  }
  return zip.generateAsync({ type: 'nodebuffer' });
}

/** An XLSX with `rows` rows; column A holds "ROW-n". */
export function makeXlsx(rows: number): Buffer {
  const data = Array.from({ length: rows }, (_, i) => [`ROW-${i + 1}`, i + 1, `value ${i + 1}`]);
  const book = XLSX.utils.book_new();
  XLSX.utils.book_append_sheet(book, XLSX.utils.aoa_to_sheet([['name', 'n', 'text'], ...data]), 'Sheet1');
  return XLSX.write(book, { type: 'buffer', bookType: 'xlsx' }) as Buffer;
}

export const MIME = {
  pdf: 'application/pdf',
  pptx: 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
  xlsx: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  docx: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
  txt: 'text/plain',
} as const;
