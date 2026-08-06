CREATE INDEX idx_know_snapshot_source ON know_source_snapshot (source_id);
CREATE INDEX idx_know_document_snapshot ON know_document (snapshot_id);
CREATE INDEX idx_know_unit_type_status ON know_unit (unit_type, status);
CREATE INDEX idx_know_relation_from ON know_relation (from_id, relation_type);
CREATE INDEX idx_know_relation_to ON know_relation (to_id, relation_type);
CREATE INDEX idx_know_feedback_unit ON know_usage_feedback (knowledge_unit_id, verdict);
