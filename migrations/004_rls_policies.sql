ALTER TABLE assets ENABLE ROW LEVEL SECURITY;
ALTER TABLE jobs ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS assets_select_public_or_owner ON assets;
CREATE POLICY assets_select_public_or_owner
ON assets
FOR SELECT
USING (user_id IS NULL OR user_id = auth.uid());

DROP POLICY IF EXISTS assets_insert_public_or_owner ON assets;
CREATE POLICY assets_insert_public_or_owner
ON assets
FOR INSERT
WITH CHECK (user_id IS NULL OR user_id = auth.uid());

DROP POLICY IF EXISTS assets_update_public_or_owner ON assets;
CREATE POLICY assets_update_public_or_owner
ON assets
FOR UPDATE
USING (user_id IS NULL OR user_id = auth.uid())
WITH CHECK (user_id IS NULL OR user_id = auth.uid());

DROP POLICY IF EXISTS assets_delete_public_or_owner ON assets;
CREATE POLICY assets_delete_public_or_owner
ON assets
FOR DELETE
USING (user_id IS NULL OR user_id = auth.uid());

DROP POLICY IF EXISTS jobs_select_related_assets ON jobs;
CREATE POLICY jobs_select_related_assets
ON jobs
FOR SELECT
USING (
	EXISTS (
		SELECT 1
		FROM assets
		WHERE assets.id = jobs.asset_id
			AND (assets.user_id IS NULL OR assets.user_id = auth.uid())
	)
);

DROP POLICY IF EXISTS jobs_insert_related_assets ON jobs;
CREATE POLICY jobs_insert_related_assets
ON jobs
FOR INSERT
WITH CHECK (
	EXISTS (
		SELECT 1
		FROM assets
		WHERE assets.id = jobs.asset_id
			AND (assets.user_id IS NULL OR assets.user_id = auth.uid())
	)
);

DROP POLICY IF EXISTS jobs_update_related_assets ON jobs;
CREATE POLICY jobs_update_related_assets
ON jobs
FOR UPDATE
USING (
	EXISTS (
		SELECT 1
		FROM assets
		WHERE assets.id = jobs.asset_id
			AND (assets.user_id IS NULL OR assets.user_id = auth.uid())
	)
)
WITH CHECK (
	EXISTS (
		SELECT 1
		FROM assets
		WHERE assets.id = jobs.asset_id
			AND (assets.user_id IS NULL OR assets.user_id = auth.uid())
	)
);

DROP POLICY IF EXISTS jobs_delete_related_assets ON jobs;
CREATE POLICY jobs_delete_related_assets
ON jobs
FOR DELETE
USING (
	EXISTS (
		SELECT 1
		FROM assets
		WHERE assets.id = jobs.asset_id
			AND (assets.user_id IS NULL OR assets.user_id = auth.uid())
	)
);
