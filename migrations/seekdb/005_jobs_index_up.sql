CREATE TABLE IF NOT EXISTS know_ingestion_job (
    job_id VARCHAR(240) PRIMARY KEY, request_json JSON NOT NULL,
    status VARCHAR(32) NOT NULL, stage VARCHAR(64), source_count BIGINT NOT NULL DEFAULT 0,
    snapshot_count BIGINT NOT NULL DEFAULT 0, wiki_page_count BIGINT NOT NULL DEFAULT 0,
    unit_count BIGINT NOT NULL DEFAULT 0, error LONGTEXT,
    created_at TIMESTAMP NOT NULL, updated_at TIMESTAMP NOT NULL
);
CREATE TABLE IF NOT EXISTS know_index_version (
    index_version VARCHAR(240) PRIMARY KEY, embedding_model VARCHAR(512) NOT NULL,
    embedding_dimension BIGINT NOT NULL, reranker_model VARCHAR(512),
    schema_version VARCHAR(240) NOT NULL, source_snapshot_hash CHAR(64) NOT NULL,
    created_at TIMESTAMP NOT NULL
);
