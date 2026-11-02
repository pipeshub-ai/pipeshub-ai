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

## Reading a whole record

`knowledgegraph__fetch_record` reads records end to end, sized by characters:
a call gets a quarter of the model's context window (four characters per
token), at most 140,000 characters, and never more than that share, so a
small model gets a read in proportion to its window. The fetch result has its
own tool-result cap equal to that size, so it is never cut in the middle.

- The records of one call share the room fairly; a record that fits is read
  whole, however many blocks it has.
- A record that does not fit its share is shown by its opening and its most
  relevant blocks, with each gap named; `start_block` continues from a gap or
  a stop, and `start_block=0` then reads it in order.
- The model's `max_blocks` is honoured only when it continues a record
  (`start_block` > 0). A first read ignores it: models that send it on every
  call would otherwise turn one read into several.
- 5,000 blocks per record bounds the walk; it is a safety limit, not a size.

`FETCH_SIZE_BY_CHARS=false` turns this off: each result then stays under the
shared 64,000-character cap (58,000 for the records) and the server's default
of 200 blocks (`PIPESHUB_FULL_RECORD_MAX_BLOCKS`) applies to records that do
not fit. It is on by default and does not follow `HIGH_ACCURACY_MODE`.

## Finding with less, then reading the right part (`TARGETED_RETRIEVAL`)

Search results are most of what a knowledge question writes into the prompt:
with `TARGETED_RETRIEVAL` off, each search fills the 64,000-character
tool-result cap, and a question usually runs about three. Yet when an answer misses a fact, the record that
holds it was usually found; the fact sat in a part of it the passages did not
show. `TARGETED_RETRIEVAL` (on by default, and not tied to
`HIGH_ACCURACY_MODE`) moves characters from breadth to depth:

- **Smaller searches.** A search result is fitted to
  `TARGETED_SEARCH_MAX_CHARS` (32,000) instead of 64,000.
- **A few passages per record.** After step 3 above, each record keeps at
  most `TARGETED_SEARCH_PASSAGES_PER_RECORD` passages; unset, the count the
  search API keeps per record (8 at the assistant's limit of 50). A block is
  one passage, and so is each child of a section, list or table: a match in a
  long Slack thread or pull request no longer brings the whole section, but
  the replies the search matched, best first, then those nearest them.
  Neighbours stay only beside a passage that stayed. A line at the top of the
  result says records were trimmed, and the end lists them with how many
  matching passages were not shown.
- **Nothing sent twice.** Passages an earlier search of the same request
  showed (or the first-turn context did) go after new ones in a record's
  share, and a record with nothing new moves behind the others, so it is the
  first to go when the result is short of room. The result says how many it
  did not repeat. When there is room they are shown again, so a result leans
  on an earlier one only when it had to leave something out.
- **Reading the right part.** `knowledgegraph__fetch_record` takes a `query`.
  With one, each record is read within `FOCUSED_READ_MAX_CHARS` (16,000) as
  its blocks most relevant to the query (ranked by `select_relevant_blocks`
  against that query rather than the question), with the gaps marked; a
  record that fits is read whole. Without a query, a fetch reads as before.
  The prompt's record-reading rules add when to use it: a specific detail the
  passages point to but do not state. Under `THOROUGH_RETRIEVAL`, which reads
  those records in full, none of this is offered: the tool has no `query`
  parameter and refuses one as an unknown argument, and the notes on trimmed
  records ask for the record read whole. A continuation (`start_block`) with
  a `query` reads on in order within the same focused size.
- The first-turn context in internal-search chats is fitted the same way. A
  turn with no tools (a model that cannot call them) gets no note asking it
  to read a record.

`TARGETED_RETRIEVAL=false` (or `0`, `no`, `off`) turns it off. Off, none of
this runs: results, the fetch tool's schema and the prompt are as they were
before the feature. `tests/perf/bench_targeted_retrieval.py` replays a mix of
searches through this code with both settings and prints the characters each
sends.
