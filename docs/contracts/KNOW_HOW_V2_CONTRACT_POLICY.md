# Know/How v2 contract policy

The models exported from `rosclaw_know.contracts` are the authoritative v2
wire contracts. They are strict Pydantic models and reject unknown fields.

Services advertise the exact schema versions they support. Peers select the
highest exact match; they do not coerce between major versions. A client that
needs forward compatibility must explicitly project a newer payload onto a
known schema and record that downgrade in its response warnings.

All timestamps include a timezone. Evidence always names an immutable source
snapshot and a bounded excerpt. Knowledge, reference packs and advice cannot
contain runtime actions; How recommendation `action_type` values describe
advisory cognitive work only.

Run `python scripts/export_contract_schemas.py` to regenerate the checked-in
JSON Schema bundle.
