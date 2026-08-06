CREATE TABLE IF NOT EXISTS know_unit (
    knowledge_unit_id VARCHAR(240) PRIMARY KEY, unit_type VARCHAR(64) NOT NULL,
    title VARCHAR(1024) NOT NULL, problem LONGTEXT NOT NULL, mechanism LONGTEXT NOT NULL,
    implementation LONGTEXT NOT NULL, applicability JSON, limitations JSON,
    contraindications JSON, content LONGTEXT, status VARCHAR(32) NOT NULL,
    trust_tier VARCHAR(32), confidence DOUBLE, source_freshness DOUBLE,
    metadata_json JSON, content_hash CHAR(64) NOT NULL,
    created_at TIMESTAMP NOT NULL, updated_at TIMESTAMP NOT NULL
);
CREATE TABLE IF NOT EXISTS know_evidence (
    evidence_id VARCHAR(240) PRIMARY KEY, snapshot_id VARCHAR(240) NOT NULL,
    document_id VARCHAR(240) NOT NULL, path VARCHAR(4096) NOT NULL,
    start_line BIGINT, end_line BIGINT, section VARCHAR(1024), claim TEXT,
    excerpt VARCHAR(2000) NOT NULL, content_hash CHAR(64) NOT NULL
);
CREATE TABLE IF NOT EXISTS know_unit_source (
    knowledge_unit_id VARCHAR(240) NOT NULL, snapshot_id VARCHAR(240) NOT NULL,
    evidence_id VARCHAR(240) NOT NULL, relation_type VARCHAR(64) NOT NULL,
    PRIMARY KEY (knowledge_unit_id, snapshot_id, evidence_id, relation_type)
);
CREATE TABLE IF NOT EXISTS know_relation (
    relation_id VARCHAR(240) PRIMARY KEY, from_id VARCHAR(240) NOT NULL,
    from_type VARCHAR(64) NOT NULL, relation_type VARCHAR(64) NOT NULL,
    to_id VARCHAR(240) NOT NULL, to_type VARCHAR(64) NOT NULL,
    confidence DOUBLE NOT NULL, evidence_id VARCHAR(240) NOT NULL,
    created_at TIMESTAMP NOT NULL
);
CREATE TABLE IF NOT EXISTS know_unit_vector (
    knowledge_unit_id VARCHAR(240) NOT NULL, vector_kind VARCHAR(32) NOT NULL,
    embedding_model VARCHAR(512) NOT NULL, embedding_dimension BIGINT NOT NULL,
    content_hash CHAR(64) NOT NULL, embedding_json JSON NOT NULL,
    PRIMARY KEY (knowledge_unit_id, vector_kind, embedding_model)
);
