CREATE TABLE IF NOT EXISTS know_document (
    document_id VARCHAR(240) PRIMARY KEY, snapshot_id VARCHAR(240) NOT NULL,
    document_type VARCHAR(64) NOT NULL, path VARCHAR(4096) NOT NULL,
    title VARCHAR(1024) NOT NULL, language VARCHAR(64), content LONGTEXT NOT NULL,
    content_hash CHAR(64) NOT NULL, mime_type VARCHAR(255), size_bytes BIGINT NOT NULL,
    metadata_json JSON, created_at TIMESTAMP NOT NULL
);
CREATE TABLE IF NOT EXISTS know_wiki_page (
    page_id VARCHAR(240) PRIMARY KEY, snapshot_id VARCHAR(240) NOT NULL,
    project_id VARCHAR(240) NOT NULL, parent_page_id VARCHAR(240),
    page_type VARCHAR(64), title VARCHAR(1024), slug VARCHAR(1024), summary TEXT,
    content LONGTEXT NOT NULL, outline_order BIGINT, content_hash CHAR(64) NOT NULL,
    evidence_json JSON, created_at TIMESTAMP NOT NULL
);
CREATE TABLE IF NOT EXISTS know_project_component (
    component_id VARCHAR(240) PRIMARY KEY, project_id VARCHAR(240) NOT NULL,
    snapshot_id VARCHAR(240) NOT NULL, parent_component_id VARCHAR(240),
    component_type VARCHAR(64), path VARCHAR(4096), language VARCHAR(64),
    responsibility TEXT, public_symbols_json JSON, dependencies_json JSON,
    entrypoints_json JSON, content_hash CHAR(64) NOT NULL
);
