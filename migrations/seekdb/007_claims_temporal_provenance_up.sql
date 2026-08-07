CREATE TABLE IF NOT EXISTS know_claim (
    claim_id VARCHAR(240) PRIMARY KEY,
    knowledge_unit_id VARCHAR(240),
    subject TEXT NOT NULL,
    predicate VARCHAR(500) NOT NULL,
    object_text LONGTEXT NOT NULL,
    claim_type VARCHAR(64) NOT NULL,
    status VARCHAR(32) NOT NULL,
    truth_quality DOUBLE NOT NULL,
    source_authority CHAR(1) NOT NULL,
    utility_score DOUBLE NOT NULL,
    compatibility_score DOUBLE NOT NULL,
    retrieval_score DOUBLE NOT NULL,
    compatibility_status VARCHAR(32) NOT NULL,
    compatibility_scope_json JSON,
    source_snapshot_ids_json JSON NOT NULL,
    evidence_json JSON NOT NULL,
    valid_from TIMESTAMP NULL,
    valid_to TIMESTAMP NULL,
    observed_at TIMESTAMP NOT NULL,
    superseded_by_json JSON,
    contradicts_json JSON,
    provenance_json JSON NOT NULL,
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL
);
CREATE INDEX idx_know_claim_unit_status ON know_claim (knowledge_unit_id, status);
CREATE INDEX idx_know_claim_type_status ON know_claim (claim_type, status);
CREATE TABLE IF NOT EXISTS know_source_disagreement (
    disagreement_id VARCHAR(240) PRIMARY KEY,
    subject TEXT NOT NULL,
    claim_ids_json JSON NOT NULL,
    source_snapshot_ids_json JSON NOT NULL,
    rationale LONGTEXT NOT NULL,
    status VARCHAR(32) NOT NULL,
    resolution LONGTEXT,
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL
);
CREATE INDEX idx_know_disagreement_status ON know_source_disagreement (status, updated_at);
