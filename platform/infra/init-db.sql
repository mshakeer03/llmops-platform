-- =============================================================================
--  PostgreSQL initialisation — runs once when the data volume is first created
--  Creates two databases on the same instance:
--    llmops   → LLMOps platform API (FastAPI / Alembic)
--    litellm  → LiteLLM proxy backend (Prisma-managed, powers the LiteLLM UI)
-- =============================================================================

-- llmops is already created by POSTGRES_DB env var; create litellm alongside it
SELECT 'CREATE DATABASE litellm OWNER llmops'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'litellm')\gexec
