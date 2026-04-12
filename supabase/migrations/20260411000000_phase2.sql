
CREATE TABLE organizations (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), name TEXT, owner_user_id UUID, created_at TIMESTAMPTZ DEFAULT NOW());
CREATE TABLE workspaces (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), org_id UUID REFERENCES organizations(id), name TEXT, slug TEXT, r2_prefix TEXT, created_at TIMESTAMPTZ DEFAULT NOW());
CREATE TABLE workspace_members (workspace_id UUID REFERENCES workspaces(id), user_id UUID, role TEXT);
CREATE TABLE api_keys (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), workspace_id UUID REFERENCES workspaces(id), key_hash TEXT, name TEXT, scopes TEXT[], last_used_at TIMESTAMPTZ);
CREATE TABLE origins (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), workspace_id UUID REFERENCES workspaces(id), name TEXT, type TEXT, config JSONB);
CREATE TABLE media_events (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), workspace_id UUID, asset_id UUID, origin_id UUID, event_type TEXT, format_served TEXT, bytes_saved BIGINT, response_time_ms INT, user_agent TEXT, country_code TEXT, created_at TIMESTAMPTZ DEFAULT NOW());
CREATE TABLE presets (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), workspace_id UUID REFERENCES workspaces(id), name TEXT, params JSONB);
CREATE TABLE webhooks (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), workspace_id UUID REFERENCES workspaces(id), url TEXT, secret TEXT, events TEXT[], is_active BOOLEAN);
