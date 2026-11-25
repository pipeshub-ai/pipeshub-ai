---
name: action-items
description: List the action items in this conversation or in a source you name, with owners and due dates.
metadata:
  agent-loop:
    version: 1.0.0
    category: writing
    tags: [command, action-items]
    status: active
    source: builtin
    pack_name: action-items
    pack_version: 1.0.0
    invocation: {user: true, model: false}
    arguments:
      - {name: source, description: "A meeting, document or thread; leave empty for this conversation"}
---

List the action items as a table with the columns Task, Owner and Due. Use "Unassigned" or "No date" where the source does not say. If a source follows, read it from my knowledge sources and cite it; otherwise use this conversation. Do not invent owners or dates.
