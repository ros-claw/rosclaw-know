CREATE TABLE IF NOT EXISTS know_source (
    source_id VARCHAR(240) PRIMARY KEY,
    canonical_url VARCHAR(4096) NOT NULL,
    source_type VARCHAR(64) NOT NULL,
    title VARCHAR(1024) NOT NULL,
    publisher VARCHAR(512), repository_owner VARCHAR(255), repository_name VARCHAR(255),
    license VARCHAR(255), trust_tier VARCHAR(32) NOT NULL, authority_score DOUBLE,
    first_discovered_at TIMESTAMP NOT NULL, last_checked_at TIMESTAMP,
    latest_snapshot_id VARCHAR(240), status VARCHAR(32) NOT NULL, metadata_json JSON
);
CREATE TABLE IF NOT EXISTS know_source_snapshot (
    snapshot_id VARCHAR(240) PRIMARY KEY, source_id VARCHAR(240) NOT NULL,
    version_kind VARCHAR(32) NOT NULL, version_value VARCHAR(512) NOT NULL,
    commit_sha VARCHAR(64), tag VARCHAR(255), published_at TIMESTAMP,
    fetched_at TIMESTAMP NOT NULL, content_hash CHAR(64) NOT NULL,
    parent_snapshot_id VARCHAR(240), supersedes_snapshot_id VARCHAR(240),
    immutable BOOLEAN NOT NULL DEFAULT TRUE, metadata_json JSON,
    UNIQUE KEY uq_know_snapshot_version (source_id, version_kind, version_value)
);
