-- Append-only audit log storage for FONDACO_AUDIT_BACKEND=postgres
-- (boundary/audit.py, PostgresBackend).
--
-- Lives in its own schema, never in `public`: the data adapter reads `public`
-- only, so the audit table is invisible to the planner and unreachable by any
-- plan, and fondaco_ro (the data role) gets no grant on it at all.
--
-- Append-only is enforced twice, by the database itself:
--   1. privileges — the writer role is granted SELECT and INSERT, nothing else;
--   2. triggers   — UPDATE, DELETE and TRUNCATE raise, whoever runs them, so a
--      role that was granted too much still cannot rewrite history. Only the
--      table owner can disable a trigger; that is the "attacker with database
--      administrator rights" case, which the HMAC seal and external anchoring
--      cover (design/threat-model.md, 13b).
--
-- Demo-grade password, like fondaco_ro; see .env.example.

CREATE SCHEMA fondaco_audit;

CREATE TABLE fondaco_audit.fondaco_audit_log (
    seq bigint PRIMARY KEY CHECK (seq >= 0),
    mac char(64) NOT NULL,
    entry text NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT now()
);

CREATE FUNCTION fondaco_audit.refuse_rewrite() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'fondaco_audit_log is append-only (% refused)', TG_OP
        USING ERRCODE = 'insufficient_privilege';
END
$$;

CREATE TRIGGER fondaco_audit_log_no_update_delete
    BEFORE UPDATE OR DELETE ON fondaco_audit.fondaco_audit_log
    FOR EACH ROW EXECUTE FUNCTION fondaco_audit.refuse_rewrite();

CREATE TRIGGER fondaco_audit_log_no_truncate
    BEFORE TRUNCATE ON fondaco_audit.fondaco_audit_log
    FOR EACH STATEMENT EXECUTE FUNCTION fondaco_audit.refuse_rewrite();

CREATE ROLE fondaco_audit LOGIN PASSWORD 'fondaco-audit-dev';
REVOKE ALL ON SCHEMA fondaco_audit FROM PUBLIC;
GRANT USAGE ON SCHEMA fondaco_audit TO fondaco_audit;
GRANT SELECT, INSERT ON fondaco_audit.fondaco_audit_log TO fondaco_audit;
