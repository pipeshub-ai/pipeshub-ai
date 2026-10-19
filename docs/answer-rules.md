# Answer rules

Three deployment switches add prompt rules for knowledge answers. Each is off
by default and follows `HIGH_ACCURACY_MODE` when unset or blank; set to `true`
or `false`, it decides for its own rules alone. The deployment sets them, not
the request, so the prompt prefix a provider caches stays the same across
questions. With all three off, the prompt is unchanged.

| Switch | Reaches | What changes | Cost |
|---|---|---|---|
| `ANSWER_COMPOSITION_RULES` | Chat in enterprise-search mode | The first sentence answers and says what it is about, in the question's words. The prose names who said or decided it, where and when. Values keep their qualifiers ("about", "at most"). A procedure or policy comes from the record that defines it for the scope asked, not a blend. The substance comes first and its status after it; done, merged and approved are reported as the records state them, delivered only where a source says so. Conflicting sources are settled in order: for what a system does now (not what was decided or standardized), an implemented change over a proposal for it; then an explicit update or replacement, then the narrower scope, then what most sources say, then the most recent. A settled conflict is answered, and a rule tied to a tier, plan, region or size keeps its condition. A record's indexed "Summary:" line never settles a fact. | Prompt text only (about 1,000 tokens more in the cached prefix than with the switch off). |
| `THOROUGH_RETRIEVAL` | Chat in enterprise-search mode | Before answering, the model fetches the records its answer rests on in full (usually one to three, and at least one for each part of the question, each checked against that part's conditions, without disputing a condition a record states in other words), searches again in a document's terms for any part the context does not state, lists a folder (or an entity's records, when the entity tools are on) for "all/every" questions, and looks for other sources on a question about a conflict. The full-record fetch policy asks for the same. | More fetch and search turns, and more input tokens per question, most on long documents. |
| `MULTI_STEP_ANSWER_RULES` | Every agent and chat that can search knowledge, including the exploration sub-agent | Rules for questions that chain lookups: confirm each stated constraint, search with the entity a step resolved, try other readings of a relative date before saying nothing was found, and prefer an entity's own record over passing mentions. | About 200 more tokens in the cached prefix. |

Enterprise-search mode is the chat assistant's default search mode (and the
legacy chat modes that map to it). Agent Builder agents and the universal
agent get only `MULTI_STEP_ANSWER_RULES`. The no-tools path for models
without tool calling is not affected.

Changing a switch changes the system prompt once, so each organization's
cached prefix is rebuilt on its next request.
