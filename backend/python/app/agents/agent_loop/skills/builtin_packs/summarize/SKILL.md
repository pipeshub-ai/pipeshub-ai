---
name: summarize
description: Summarize this conversation, or a topic you name, in a few short bullet points.
metadata:
  agent-loop:
    version: 1.0.0
    category: writing
    tags: [command, summary]
    status: active
    source: builtin
    pack_name: summarize
    pack_version: 1.0.0
    invocation: {user: true, model: false}
    arguments:
      - {name: topic, description: "What to summarize; leave empty for this conversation"}
---

Summarize in at most five short bullet points, most important first. If a topic follows, summarize what you can find about it in my knowledge sources and cite them; otherwise summarize this conversation so far. End with one line on anything still open.
