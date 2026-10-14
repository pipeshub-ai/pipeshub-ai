# Search record cut (`FUSION_RECORD_CUT`)

A knowledge search returns a fixed number of passages (`limit`). Every vector
store PipesHub supports (Qdrant, OpenSearch, Redis) fuses its dense and
keyword legs by passage and sizes each leg from that limit, so the cut falls on
passages: one long document can take most of the slots, and a record that only
the keyword leg found lands just past the cut.

With the record cut on, each search:

1. asks the vector store for `SEARCH_FUSION_DEPTH` times the limit (3 by
   default), never more than 300 unless the request itself asks for more;
2. keeps the best half of the limit with at most
   `max(SEARCH_PER_RECORD_FLOOR, limit / SEARCH_PER_RECORD_DIVISOR)` passages
   from any one record (8 at a limit of 50);
3. gives the other half (`SEARCH_NEW_RECORD_SHARE`) to the best passage of each
   record not shown yet;
4. fills anything left in rank order, so a search that only one record matches
   still returns the full limit.

The kept passages stay in rank order. A container-scoped search, which checks
permissions after the vector search, fetches each query at the fused depth and
cuts once, after the check, so records the user cannot read never take a slot.
Searches confined to a single record, such as picking passages inside a
fetched record, keep the plain depth. Permissions are unchanged: the vector
filter and the per-record checks run exactly as before.

## Switch

`FUSION_RECORD_CUT` follows `HIGH_ACCURACY_MODE` when unset or blank; `true` or
`false` decides for this feature alone. It is read on every search. The four
tuning knobs are read at startup and clamped to safe ranges; a malformed value
keeps the default. Off, a search asks the vector store for the limit and keeps
the top passages, exactly as before.

## Cost and what to watch

The vector store reads up to three times as many points per query, and
container-scoped searches also verify permissions on more records. There is no
extra model call. On Redis, where every candidate's payload is read, or a small
OpenSearch cluster, consider `SEARCH_FUSION_DEPTH=2`. Watch vector-search
latency and, in chat, how often the agent fetches a whole record: a rising
fetch rate on long single-document questions means the per-record cap is too
low for that corpus, and `SEARCH_PER_RECORD_FLOOR` can be raised.

With `LOG_LEVEL=debug` each search logs the points fused, the points kept, and
the records a plain cut would have shown against the records the record cut
shows. The switch state is logged at `info` on the first search.
