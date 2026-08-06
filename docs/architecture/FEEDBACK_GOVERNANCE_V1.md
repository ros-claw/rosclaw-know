# Know v2 feedback governance

KnowledgeUsageFeedbackV1 is an observation, not permission to mutate truth.
Every accepted feedback record deterministically creates one
FeedbackGovernanceRecordV1 in the canonical Know store.

| Verdict | Queue | Consequence |
| --- | --- | --- |
| useful | usage_signals | record usage only |
| irrelevant | query_ranking_signals | record a query-family ranking signal |
| stale | source_refresh | create a source-refresh review candidate |
| incompatible | compatibility_review | create a constraints/compatibility-unit review candidate |
| misleading | ranking_review | create a downweight review candidate |
| unknown | manual_review | request manual triage |

Every governance record has automatic_mutation_allowed=false. In particular,
feedback never automatically deletes or rewrites knowledge, promotes draft
knowledge, marks physical success, or overrides official constraints.

POST /know/v2/feedback returns the resulting governance record.
GET /know/v2/feedback/governance lists the queue and supports queue, status
and bounded limit filters. Writes are idempotent by feedback_id; reusing an
ID with a different payload is rejected.
