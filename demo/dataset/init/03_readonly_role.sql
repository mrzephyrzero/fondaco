-- Read-only role for the boundary: write denial enforced at the DB layer
-- (adapter-contract.md s1: defense in depth, not only in code).
-- Demo-grade password; see .env.example. Production hardening is out of V1 scope.

CREATE ROLE fondaco_ro LOGIN PASSWORD 'fondaco-ro-dev';

REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO fondaco_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO fondaco_ro;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO fondaco_ro;
ALTER ROLE fondaco_ro SET default_transaction_read_only = on;
