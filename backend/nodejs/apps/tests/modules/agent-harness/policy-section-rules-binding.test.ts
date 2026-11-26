/**
 * The policy sections an edition offers are bound to `CONTROL_PLANE_POLICY_SECTION_RULES`, one list per binder:
 * the control plane reads every list bound, so two features that each offer a section compose, and a section two
 * of them offer is refused at boot rather than checked by whichever was bound last.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import { Container } from 'inversify';
import type { PolicySectionRule } from '../../../src/modules/control-plane';
import { policySectionRulesOf } from '../../../src/modules/agent-harness/binders/control-plane-admin';
import { CONTROL_PLANE_POLICY_SECTION_RULES } from '../../../src/modules/agent-harness/binders/tokens';

const rule = (section: string): PolicySectionRule => ({
  section,
  context: () => ({ ceilings: {}, locked: [] }),
  check: () => undefined,
});

describe('policy section rules bound by several binders', () => {
  it('none bound: no rule', () => {
    expect(policySectionRulesOf(new Container())).to.deep.equal([]);
  });

  it('each binder binds its own list; every list is read, in binding order', () => {
    const container = new Container();
    const first = rule('editionLimits');
    const second = rule('editionRules');
    container.bind<readonly PolicySectionRule[]>(CONTROL_PLANE_POLICY_SECTION_RULES).toConstantValue([first]);
    container.bind<readonly PolicySectionRule[]>(CONTROL_PLANE_POLICY_SECTION_RULES).toConstantValue([second]);
    expect(policySectionRulesOf(container)).to.deep.equal([first, second]);
  });

  it('a section two rules offer is refused', () => {
    const container = new Container();
    container.bind<readonly PolicySectionRule[]>(CONTROL_PLANE_POLICY_SECTION_RULES).toConstantValue([rule('editionLimits')]);
    container.bind<readonly PolicySectionRule[]>(CONTROL_PLANE_POLICY_SECTION_RULES).toConstantValue([rule('editionLimits')]);
    expect(() => policySectionRulesOf(container)).to.throw(/editionLimits/);
  });
});
