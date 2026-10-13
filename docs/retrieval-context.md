# Retrieval context: from search hits to what the model reads

The agent's knowledge search (`knowledgegraph__search`), the first-turn
prefetch in internal-search mode, and the legacy
`retrieval__search_internal_knowledge` tool all turn search hits into
`<record>` text through one pipeline in
`backend/python/app/modules/retrieval/context/`. The legacy tool is a thin
wrapper over the search tool's `execute_search`.

## Steps

1. **Units.** `get_flattened_results` resolves hits into units: a block, a
   list or section group with its children, a table with its matched rows,
   or a record summary. Hits whose record did not load are dropped.
2. **Rank and cut.** `rank_units` orders units by retrieval score. Ties and
   unscored units keep the order retrieval returned them in, and a table
   ranks by its best row. The search tool keeps the best N units (50 for one
   source; a per-source fan-out already gave each source its own limit).
3. **Neighbours.** `expand_neighbours` adds the text block before and after
   each kept text, code, image or table unit, never a block already shown.
   Under a cap, neighbours only fill the slots the hits left, best hit
   first, so they never take a slot from a hit.
4. **Records.** Only the records of kept units are kept. Prefetch adds the
   DDL of tables related by foreign key, then graph context (parents,
   relations) is added for those records in batched graph calls.
5. **Fit and render.** `KnowledgeRenderer` renders the units. The search tool
   first sizes the rest of its result (header, notes, fetch candidates,
   keyword-match hint) and gives the records the room left under the
   64,000-character tool-result cap. If they do not fit, records are kept in
   rank order, whole when they fit, else cut to their most relevant units;
   a record with nothing that fits is left out, and the header says how many
   were. Only the most relevant unit is kept whatever its size. Sizing renders
   on copies of the request's citation-ref mapper, record-id shortener and
   image admission, so the sizes are those of the real render, and refs,
   short ids and image slots are assigned only to what is shown.
6. **State.** `final_results`, `virtual_record_id_to_result`, `tool_records`,
   the known record ids and the opt-in retrieval trace receive only the
   units and records the model was shown.

Identical inputs render byte-identical text: every ordering is by score and
retrieval position, never by set or hash order.

## Reading order

By default, records appear in record-id order with each record's blocks in
document order, as they always have. `RELEVANCE_ORDERED_CONTEXT=true` shows
the most relevant record first instead, and the search result's header says
so. Unset or blank follows `HIGH_ACCURACY_MODE`, like the other
high-accuracy switches in `backend/env.template`. Which records are shown,
and which are left out to fit, is decided by relevance either way.
