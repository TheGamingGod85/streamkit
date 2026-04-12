CREATE TABLE workspace_bootstrap_sessions (
  session_id TEXT PRIMARY KEY,
  display_name TEXT,
  email TEXT,
  org_id UUID REFERENCES organizations(id),
  workspace_id UUID REFERENCES workspaces(id),
  api_key_id UUID REFERENCES api_keys(id),
  last_seen_at TIMESTAMPTZ DEFAULT NOW(),
  created_at TIMESTAMPTZ DEFAULT NOW()
);
