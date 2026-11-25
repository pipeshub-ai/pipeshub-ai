/**
 * A card's "Don't ask again" saves through the deployment's writer; an edition that binds a writer over it decides
 * where its own targets go and passes the rest on.
 */
import { expect } from 'chai';
import { Container } from 'inversify';
import { toolRulesOf } from '../../../src/modules/agent-harness/binders/session-views';
import { AGENT_HARNESS_TOOL_RULE_WRITER } from '../../../src/modules/agent-harness/binders/tokens';
import type {
  RuleGrantTarget,
  ToolRuleWriter,
  ToolRuleWriterDecorator,
} from '../../../src/modules/agent-harness/ports/tool-rules';

class Recorded implements ToolRuleWriter {
  readonly saved: string[] = [];
  constructor(private readonly name: string) {}
  async remember(_authorization: string, target: RuleGrantTarget, rule: 'allow' | 'block'): Promise<boolean> {
    this.saved.push(`${this.name}:${target.instanceId}/${target.toolName}=${rule}`);
    return true;
  }
}

describe('tool rule writer seam', () => {
  it('uses the deployment writer when no edition binds one', async () => {
    const base = new Recorded('base');
    const { toolRules } = toolRulesOf(new Container(), base);
    expect(toolRules).to.equal(base);
    expect(toolRulesOf(new Container(), undefined)).to.deep.equal({});
  });

  it('lets an edition wrap the deployment writer', async () => {
    const base = new Recorded('base');
    const own = new Recorded('edition');
    const decorate: ToolRuleWriterDecorator = (inner) => ({
      remember: (authorization, target, rule) =>
        target.instanceId === 'own'
          ? own.remember(authorization, target, rule)
          : (inner?.remember(authorization, target, rule) ?? Promise.resolve(false)),
    });
    const container = new Container();
    container.bind<ToolRuleWriterDecorator>(AGENT_HARNESS_TOOL_RULE_WRITER).toConstantValue(decorate);
    const { toolRules } = toolRulesOf(container, base);
    expect(await toolRules?.remember('Bearer t', { instanceId: 'own', toolName: 'a' }, 'allow')).to.equal(true);
    expect(await toolRules?.remember('Bearer t', { instanceId: 'mcp-1', toolName: 'b' }, 'block')).to.equal(true);
    expect(own.saved).to.deep.equal(['edition:own/a=allow']);
    expect(base.saved).to.deep.equal(['base:mcp-1/b=block']);
  });
});
