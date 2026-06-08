-- Migration 001: Core schema
-- Vertical: saas_fr (SaaS B2B France outbound)
-- Supabase region: EU (Frankfurt/Paris)

-- ── Extensions ────────────────────────────────────────
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ── Tenants ───────────────────────────────────────────
CREATE TABLE tenants (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name TEXT NOT NULL,
    vertical TEXT NOT NULL DEFAULT 'saas_fr',
    plan TEXT NOT NULL DEFAULT 'agency',
    stripe_customer_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ── Users (within a tenant) ──────────────────────────
CREATE TABLE users (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    email TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'admin',         -- admin | op | read
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX idx_users_tenant_email ON users(tenant_id, email);

-- ── Playbooks (versioned per tenant per vertical) ─────
CREATE TABLE playbooks (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    vertical TEXT NOT NULL DEFAULT 'saas_fr',
    version INTEGER NOT NULL DEFAULT 1,
    config JSONB NOT NULL DEFAULT '{}',         -- scoring thresholds, persona, channels
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ── Playbook chunks (RAG via pgvector) ────────────────
CREATE TABLE playbook_chunks (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    playbook_id UUID NOT NULL REFERENCES playbooks(id) ON DELETE CASCADE,
    chunk_type TEXT NOT NULL,                   -- objection | sequence | pricing | proof | persona
    content TEXT NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}',       -- tags, vertical, priority
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- embedding column added in 003_pgvector.sql

-- ── Accounts (target companies) ───────────────────────
CREATE TABLE accounts (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    domain TEXT,
    firmographics JSONB NOT NULL DEFAULT '{}',  -- size, industry, region, tech_stack, funding
    icp_score FLOAT,
    status TEXT NOT NULL DEFAULT 'new',         -- new | active | nurture | rejected | customer
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ── Contacts (people at accounts) ─────────────────────
CREATE TABLE contacts (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    account_id UUID NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    first_name TEXT,
    last_name TEXT,
    title TEXT,
    email TEXT,
    linkedin_url TEXT,
    gdpr_basis TEXT DEFAULT 'legitimate_interest',  -- legitimate_interest | consent | contract
    gdpr_consent_at TIMESTAMPTZ,
    status TEXT NOT NULL DEFAULT 'new',          -- new | active | unsubscribed | bounced
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX idx_contacts_email ON contacts(tenant_id, email) WHERE email IS NOT NULL;

-- ── Campaigns ─────────────────────────────────────────
CREATE TABLE campaigns (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    playbook_id UUID NOT NULL REFERENCES playbooks(id),
    name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft',        -- draft | running | paused | completed
    config JSONB NOT NULL DEFAULT '{}',
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ── Sequences (multi-step, per campaign) ──────────────
CREATE TABLE sequences (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    campaign_id UUID NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    contact_id UUID NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
    steps JSONB NOT NULL DEFAULT '[]',          -- [{step:1, channel, template, delay_days, status}]
    status TEXT NOT NULL DEFAULT 'pending',      -- pending | active | completed | opted_out
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ── Messages (individual outreach touches) ────────────
CREATE TABLE messages (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    contact_id UUID NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
    sequence_id UUID REFERENCES sequences(id) ON DELETE SET NULL,
    channel TEXT NOT NULL,                       -- email | linkedin
    step_number INTEGER NOT NULL DEFAULT 1,
    subject TEXT,
    body TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft',        -- draft | scheduled | sent | delivered | bounced | failed
    dry_run BOOLEAN NOT NULL DEFAULT true,       -- true = no real send (dev/safety default)
    external_id TEXT,                            -- Smartlead/LinkedIn message ID
    sent_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ── Replies (inbound responses) ───────────────────────
CREATE TABLE replies (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    message_id UUID REFERENCES messages(id) ON DELETE SET NULL,
    contact_id UUID NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
    channel TEXT NOT NULL,                       -- email | linkedin
    intent TEXT,                                 -- interested | objection | not_now | not_a_fit | out_of_office | unknown
    confidence FLOAT,
    raw_body TEXT NOT NULL,
    agent_response TEXT,                         -- drafted reply (if not escalated)
    routed_to TEXT,                              -- null = auto-handled, 'closer' = escalated to human
    handled_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ── Deals (pipeline) ──────────────────────────────────
CREATE TABLE deals (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    account_id UUID NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    stage TEXT NOT NULL DEFAULT 'discovery',     -- discovery | qualified | proposal | negotiation | closed_won | closed_lost
    value_eur FLOAT,
    closed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ── Agent runs (cost tracking + observability) ────────
CREATE TABLE agent_runs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    campaign_id UUID REFERENCES campaigns(id) ON DELETE SET NULL,
    agent_type TEXT NOT NULL,                    -- sourcing | enrichment | icp_scoring | sequence_writer | reply_classifier | reply_drafter
    model TEXT NOT NULL,                         -- deepseek-v4-flash | kimi-k2.6 | claude-sonnet
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cost_eur FLOAT NOT NULL DEFAULT 0.0,
    latency_ms INTEGER NOT NULL DEFAULT 0,
    metadata JSONB NOT NULL DEFAULT '{}',        -- task-specific context
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_agent_runs_tenant ON agent_runs(tenant_id, created_at DESC);
CREATE INDEX idx_agent_runs_campaign ON agent_runs(campaign_id);
CREATE INDEX idx_agent_runs_type ON agent_runs(agent_type);
