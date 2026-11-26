/** The tool a person's own approval rule is keyed by: an MCP server instance and the tool's name there. */
export interface RuleGrantTarget {
  instanceId: string;
  toolName: string;
}

/**
 * Main's "Don't ask again" (BUGS-B): saves a person's own rule for one of
 * their MCP tools through main's rules route, asked with that person's own
 * credential. True when the rule was saved.
 */
export interface ToolRuleWriter {
  remember(
    authorization: string,
    target: RuleGrantTarget,
    rule: 'allow' | 'block',
  ): Promise<boolean>;
}

/** An edition's writer for the rules it offers, wrapping the deployment's (absent: none here). */
export type ToolRuleWriterDecorator = (
  base: ToolRuleWriter | undefined,
) => ToolRuleWriter;
