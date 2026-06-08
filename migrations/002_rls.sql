-- Migration 002: Row-Level Security (RLS)
-- Every table must be tenant-isolated.
-- Supabase auth JWT carries tenant_id in app_metadata.

-- ── Helper: extract tenant_id from JWT ────────────────
-- Assuming tenant_id is set in raw_app_meta_data during Supabase Auth signup
-- or injected via custom claims.
CREATE OR REPLACE FUNCTION get_tenant_id()
RETURNS UUID AS $$
BEGIN
    RETURN (auth.jwt() -> 'app_metadata' ->> 'tenant_id')::UUID;
END;
$$ LANGUAGE plpgsql STABLE SECURITY DEFINER;

-- ── Enable RLS on all tenant-scoped tables ────────────
ALTER TABLE tenants ENABLE ROW LEVEL SECURITY;
ALTER TABLE users ENABLE ROW LEVEL SECURITY;
ALTER TABLE playbooks ENABLE ROW LEVEL SECURITY;
ALTER TABLE playbook_chunks ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounts ENABLE ROW LEVEL SECURITY;
ALTER TABLE contacts ENABLE ROW LEVEL SECURITY;
ALTER TABLE campaigns ENABLE ROW LEVEL SECURITY;
ALTER TABLE sequences ENABLE ROW LEVEL SECURITY;
ALTER TABLE messages ENABLE ROW LEVEL SECURITY;
ALTER TABLE replies ENABLE ROW LEVEL SECURITY;
ALTER TABLE deals ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_runs ENABLE ROW LEVEL SECURITY;

-- ── tenants: users see only their own tenant ──────────
CREATE POLICY tenants_isolation ON tenants
    FOR ALL USING (id = get_tenant_id());

-- ── users: see users in same tenant ───────────────────
CREATE POLICY users_isolation ON users
    FOR ALL USING (tenant_id = get_tenant_id());

-- ── playbooks ─────────────────────────────────────────
CREATE POLICY playbooks_isolation ON playbooks
    FOR ALL USING (tenant_id = get_tenant_id());

-- ── playbook_chunks (via playbook_id) ─────────────────
CREATE POLICY playbook_chunks_isolation ON playbook_chunks
    FOR ALL USING (
        playbook_id IN (SELECT id FROM playbooks WHERE tenant_id = get_tenant_id())
    );

-- ── accounts ──────────────────────────────────────────
CREATE POLICY accounts_isolation ON accounts
    FOR ALL USING (tenant_id = get_tenant_id());

-- ── contacts (via tenant_id) ──────────────────────────
CREATE POLICY contacts_isolation ON contacts
    FOR ALL USING (tenant_id = get_tenant_id());

-- ── campaigns ─────────────────────────────────────────
CREATE POLICY campaigns_isolation ON campaigns
    FOR ALL USING (tenant_id = get_tenant_id());

-- ── sequences (via campaign) ──────────────────────────
CREATE POLICY sequences_isolation ON sequences
    FOR ALL USING (
        campaign_id IN (SELECT id FROM campaigns WHERE tenant_id = get_tenant_id())
    );

-- ── messages (via contact) ────────────────────────────
CREATE POLICY messages_isolation ON messages
    FOR ALL USING (
        contact_id IN (SELECT id FROM contacts WHERE tenant_id = get_tenant_id())
    );

-- ── replies (via contact) ─────────────────────────────
CREATE POLICY replies_isolation ON replies
    FOR ALL USING (
        contact_id IN (SELECT id FROM contacts WHERE tenant_id = get_tenant_id())
    );

-- ── deals ─────────────────────────────────────────────
CREATE POLICY deals_isolation ON deals
    FOR ALL USING (tenant_id = get_tenant_id());

-- ── agent_runs ────────────────────────────────────────
CREATE POLICY agent_runs_isolation ON agent_runs
    FOR ALL USING (tenant_id = get_tenant_id());
