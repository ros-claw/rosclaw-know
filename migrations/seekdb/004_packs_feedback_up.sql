CREATE TABLE IF NOT EXISTS know_reference_pack (
    reference_pack_id VARCHAR(240) PRIMARY KEY, query_hash CHAR(64) NOT NULL,
    context_hash CHAR(64) NOT NULL, index_version VARCHAR(240) NOT NULL,
    payload_json JSON NOT NULL, created_at TIMESTAMP NOT NULL,
    expires_at TIMESTAMP, stale BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE TABLE IF NOT EXISTS know_reference_pack_item (
    reference_pack_id VARCHAR(240) NOT NULL, rank BIGINT NOT NULL,
    knowledge_unit_id VARCHAR(240) NOT NULL, project_id VARCHAR(240),
    score DOUBLE NOT NULL, score_breakdown_json JSON,
    PRIMARY KEY (reference_pack_id, rank)
);
CREATE TABLE IF NOT EXISTS know_usage_feedback (
    feedback_id VARCHAR(240) PRIMARY KEY, reference_pack_id VARCHAR(240) NOT NULL,
    advice_id VARCHAR(240), knowledge_unit_id VARCHAR(240) NOT NULL,
    verdict VARCHAR(32) NOT NULL, reason TEXT, origin VARCHAR(32) NOT NULL,
    context_hash CHAR(64) NOT NULL, receipt_ref VARCHAR(1024), practice_ref VARCHAR(1024),
    created_at TIMESTAMP NOT NULL
);
