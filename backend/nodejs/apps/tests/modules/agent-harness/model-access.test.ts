/**
 * A1, pure: every vendor row (VR, the shared vector table) and the signed
 * release data (SG) on Node's side.
 */
import { expect } from 'chai';
import { generateKeyPairSync, sign, KeyObject } from 'node:crypto';
import { readdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import {
  AccessCandidate,
  decideAccess,
  extendVendorRules,
  ModelAccessRefusal,
  Offering,
  OrgAccessPolicy,
  releaseVendorRules,
  shippedVendorRules,
  VENDOR_RULES_EXTENSION_SCHEMA,
  VendorRules,
  verifiedDocument,
  verifyVendorRules,
} from '../../../src/modules/agent-harness/domain/model-access';
import {
  RELEASE_KEYS,
  VENDOR_RULE_EXTENSIONS,
  VENDOR_RULES_SIGNATURE_TEXT,
  VENDOR_RULES_TEXT,
} from '../../../src/modules/agent-harness/domain/vendor-rules-release';

const REPO = join(__dirname, '../../../../../..');
const HARNESS_ENGINES = join(REPO, 'backend/python/harness_engines');
const KEY_ID = 'pipeshub-vendor-rules-a1';

interface VectorCase {
  id: string;
  row: string;
  candidate: AccessCandidate;
  offering: Offering;
  policy: OrgAccessPolicy;
  expect: { allowed: string } | { refused: string; rule: string | null };
}

const VECTORS = JSON.parse(
  readFileSync(join(REPO, 'contracts/harness/vectors/vendor-rules.json'), 'utf8'),
) as { cases: VectorCase[] };

const outcome = (c: VectorCase): VectorCase['expect'] => {
  try {
    return {
      allowed: decideAccess(releaseVendorRules(), c.candidate, c.offering, c.policy).id,
    };
  } catch (error) {
    if (!(error instanceof ModelAccessRefusal)) throw error;
    return { refused: error.code, rule: error.rule };
  }
};

describe('A1 VR: every vendor row (shared with Python)', () => {
  for (const c of VECTORS.cases) {
    it(`${c.id}: ${c.row}`, () => {
      expect(outcome(c)).to.deep.equal(c.expect);
    });
  }

  it('VR-19: every shipped row decides at least one case', () => {
    const deciding = new Set(
      VECTORS.cases.map((c) => ('allowed' in c.expect ? c.expect.allowed : c.expect.rule)),
    );
    for (const rule of releaseVendorRules().rules) {
      expect(deciding.has(rule.id), rule.id).to.equal(true);
    }
  });
});

const keypair = (): { privateKey: KeyObject; raw: string } => {
  const { privateKey, publicKey } = generateKeyPairSync('ed25519');
  const jwk = publicKey.export({ format: 'jwk' }) as { x: string };
  return {
    privateKey,
    raw: Buffer.from(jwk.x, 'base64url').toString('base64'),
  };
};

const signed = (privateKey: KeyObject, document: string, keyId = KEY_ID): string =>
  JSON.stringify({
    alg: 'Ed25519',
    keyId,
    sig: sign(null, Buffer.from(document, 'utf8'), privateKey).toString('base64'),
  });

const detailOf = (
  document: string | null,
  signature: string | null,
  keys: Record<string, string>,
): string | undefined => {
  try {
    verifyVendorRules(document, signature, keys);
  } catch (error) {
    if (!(error instanceof ModelAccessRefusal)) throw error;
    expect(error.code).to.equal('vendor_rules_invalid');
    return error.detail;
  }
  return 'accepted';
};

describe('A1 SG: signed release data (Node)', () => {
  const document = VENDOR_RULES_TEXT;

  it('SG-1: a document signed by a trusted key loads', () => {
    const { privateKey, raw } = keypair();
    const rules = verifyVendorRules(document, signed(privateKey, document), {
      [KEY_ID]: raw,
    });
    expect(rules.rules.length).to.equal(
      (JSON.parse(document) as { rules: unknown[] }).rules.length,
    );
  });

  it('SG-2: a tampered document is refused', () => {
    const { privateKey, raw } = keypair();
    const signature = signed(privateKey, document);
    const flipped = document.replace('"prohibited"', '"allowed"   ');
    expect(flipped).to.not.equal(document);
    expect(detailOf(flipped, signature, { [KEY_ID]: raw })).to.equal('bad_signature');
  });

  it('SG-3: another key under the release id, and an unknown id', () => {
    const signer = keypair();
    const trusted = keypair();
    expect(
      detailOf(document, signed(signer.privateKey, document), { [KEY_ID]: trusted.raw }),
    ).to.equal('bad_signature');
    expect(
      detailOf(document, signed(signer.privateKey, document, 'someone-else'), {
        [KEY_ID]: trusted.raw,
      }),
    ).to.equal('unknown_key');
  });

  it('SG-4: missing document, missing or unusable signature', () => {
    const { raw } = keypair();
    const keys = { [KEY_ID]: raw };
    expect(detailOf(null, '{}', keys)).to.equal('missing');
    expect(detailOf(document, null, keys)).to.equal('unsigned');
    expect(detailOf(document, 'not json', keys)).to.equal('unsigned');
    expect(
      detailOf(document, JSON.stringify({ alg: 'RS256', keyId: KEY_ID, sig: 'AA==' }), keys),
    ).to.equal('unsigned');
    expect(
      detailOf(document, JSON.stringify({ alg: 'Ed25519', keyId: KEY_ID, sig: '*no*' }), keys),
    ).to.equal('unsigned');
  });

  it('SG-5: a signed document that is not a rule document is malformed', () => {
    const { privateKey, raw } = keypair();
    const base = JSON.parse(document) as { rules: Array<Record<string, unknown>> };
    const first = base.rules[0] as Record<string, unknown>;
    const variants: unknown[] = [
      { ...base, rules: [...base.rules, first] },
      { ...base, rules: [{ ...first, status: 'tolerated' }] },
      { ...base, rules: [{ ...first, accountKind: 'org_magic' }] },
      { ...base, rules: [{ ...first, offering: ['everywhere'] }] },
      { ...base, rules: [{ ...first, widen: true }] },
      { ...base, schema: 'something/2' },
      [base],
    ];
    for (const variant of variants) {
      const text = JSON.stringify(variant);
      expect(detailOf(text, signed(privateKey, text), { [KEY_ID]: raw })).to.equal(
        'malformed',
      );
    }
  });

  it('SG-6: the shipped rules verify against the shipped release key', () => {
    const rules = VENDOR_RULE_EXTENSIONS.reduce(
      (held, e) => extendVendorRules(held, verifiedDocument(e.text, e.signature, RELEASE_KEYS)),
      verifyVendorRules(VENDOR_RULES_TEXT, VENDOR_RULES_SIGNATURE_TEXT, RELEASE_KEYS),
    );
    expect(rules).to.deep.equal(releaseVendorRules());
  });

  it("SG-9: Node's embedded extensions are the Python package's extension files, in name order", () => {
    const names = readdirSync(HARNESS_ENGINES)
      .filter((name) => /^vendor_rules\..+\.json$/.test(name) && name !== 'vendor_rules.json')
      .sort();
    expect(VENDOR_RULE_EXTENSIONS.map((e) => e.text)).to.deep.equal(
      names.map((name) => readFileSync(join(HARNESS_ENGINES, name), 'utf8')),
    );
  });

  it("SG-8: Node's embedded text and signature are the Python package files byte for byte", () => {
    expect(VENDOR_RULES_TEXT).to.equal(
      readFileSync(join(HARNESS_ENGINES, 'vendor_rules.json'), 'utf8'),
    );
    expect(VENDOR_RULES_SIGNATURE_TEXT).to.equal(
      readFileSync(join(HARNESS_ENGINES, 'vendor_rules.json.sig'), 'utf8'),
    );
  });
});

describe('A1 SG: an edition offering extension (Node)', () => {
  // A base no row of which names `managed`: the extension's offering must be declared yet undecided by the rules.
  const base = (): VendorRules => {
    const shipped = verifyVendorRules(VENDOR_RULES_TEXT, VENDOR_RULES_SIGNATURE_TEXT, RELEASE_KEYS);
    return {
      version: shipped.version,
      rules: shipped.rules.map((rule) => ({
        ...rule,
        offering: rule.offering.filter((o) => o !== 'managed'),
      })),
    };
  };
  const extension = (fields: Record<string, unknown>): string =>
    JSON.stringify({
      schema: VENDOR_RULES_EXTENSION_SCHEMA,
      version: 'x1',
      offering: 'managed',
      rules: ['anthropic-api-key-org'],
      ...fields,
    });
  const refusal = (rules: VendorRules, text: string): string | undefined => {
    try {
      extendVendorRules(rules, text);
    } catch (error) {
      if (!(error instanceof ModelAccessRefusal)) throw error;
      return error.detail;
    }
    return 'accepted';
  };

  it('SG-10: it opens only its rows to its own offering and changes no other answer', () => {
    const before = base();
    const after = extendVendorRules(before, extension({}));
    expect(after.version).to.equal(`${before.version}+x1`);
    const changed = after.rules.filter((rule, i) => JSON.stringify(rule) !== JSON.stringify(before.rules[i]));
    expect(changed.map((rule) => rule.id)).to.deep.equal(['anthropic-api-key-org']);
    const candidate: AccessCandidate = {
      provider: 'anthropic',
      accountKind: 'org_api_key',
      route: 'gateway',
      engine: 'loop',
    };
    expect(decideAccess(after, candidate, 'managed').id).to.equal('anthropic-api-key-org');
    expect(() => decideAccess(before, candidate, 'managed')).to.throw(ModelAccessRefusal);
  });

  it('SG-11: an extension that could change a core answer is malformed', () => {
    const rules = base();
    for (const text of [
      'not json',
      extension({ widen: true }),
      extension({ schema: 'something/2' }),
      extension({ version: '' }),
      extension({ offering: 'oss' }),
      extension({ offering: 'never_declared' }),
      extension({ rules: [] }),
      extension({ rules: ['anthropic-api-key-org', 'anthropic-api-key-org'] }),
      extension({ rules: ['a-row-the-release-lacks'] }),
    ]) {
      expect(refusal(rules, text), text).to.equal('malformed');
    }
    const once = extendVendorRules(rules, extension({}));
    expect(refusal(once, extension({ rules: ['openai-api-key-org'] }))).to.equal('malformed');
  });

  it('SG-12: the release refuses an extension that is not signed by a release key', () => {
    const { privateKey, raw } = keypair();
    const text = extension({ offering: 'oss' });
    expect(() =>
      shippedVendorRules({ ...RELEASE_KEYS, [KEY_ID]: raw }, [{ text, signature: null }]),
    ).to.throw(ModelAccessRefusal, /not signed/);
    expect(() =>
      shippedVendorRules(RELEASE_KEYS, [{ text, signature: signed(privateKey, text) }]),
    ).to.throw(ModelAccessRefusal, /does not ship/);
    expect(() =>
      shippedVendorRules({ ...RELEASE_KEYS, [KEY_ID]: raw }, [{ text, signature: signed(privateKey, text) }]),
    ).to.throw(ModelAccessRefusal, /names an unknown offering or one the rules decide/);
  });
});
