-- 0002: Row-level security on every tenant table, and the application role's grants.
--
-- Each tenant table gets two policies with the same predicate, organization_id = baec.current_organization():
--   tenant_isolation  PERMISSIVE  the only rows any role may see, insert, update, or delete;
--   tenant_boundary   RESTRICTIVE ANDed with every permissive policy, so a permissive policy added later can never
--                     widen access beyond the current organization.
-- With no organization set, current_organization() is NULL, the predicate is never true, and nothing is visible or
-- writable (fail closed). FORCE applies the policies to the table owner (the migration role) as well.
--
-- The application role baec_app (created by the DBA bootstrap, never by a migration) gets SELECT and INSERT, and
-- UPDATE only where a store updates rows. It gets no DELETE, TRUNCATE, REFERENCES, or TRIGGER privilege, owns
-- nothing, and has no access to schema_migrations writes or operator_log.

DO $$
DECLARE
    t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['organizations', 'users', 'identity_audit', 'account_ownership', 'account_meta',
                             'interaction_meta', 'condition_drafts', 'draft_resolutions', 'preparation_requests',
                             'preparations', 'preparation_edits', 'preparation_reviews', 'preparation_provenance',
                             'preparation_blocks', 'events', 'attributions', 'crm_contacts', 'crm_tasks',
                             'crm_interaction_links', 'crm_activities'] LOOP
        EXECUTE format('ALTER TABLE baec.%I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('ALTER TABLE baec.%I FORCE ROW LEVEL SECURITY', t);
        EXECUTE format('CREATE POLICY tenant_isolation ON baec.%I AS PERMISSIVE FOR ALL '
                       'USING (organization_id = baec.current_organization()) '
                       'WITH CHECK (organization_id = baec.current_organization())', t);
        EXECUTE format('CREATE POLICY tenant_boundary ON baec.%I AS RESTRICTIVE FOR ALL '
                       'USING (organization_id = baec.current_organization()) '
                       'WITH CHECK (organization_id = baec.current_organization())', t);
        EXECUTE format('GRANT SELECT, INSERT ON baec.%I TO baec_app', t);
    END LOOP;
END $$;

GRANT USAGE ON SCHEMA baec TO baec_app;
GRANT USAGE ON SCHEMA baec_migrations TO baec_app;
GRANT SELECT ON baec_migrations.schema_migrations TO baec_app;  -- the production startup gate reads the version
GRANT EXECUTE ON FUNCTION baec.current_organization() TO baec_app;
GRANT UPDATE (display_name, active) ON baec.users TO baec_app;
GRANT UPDATE (status, completed_at, completed_by) ON baec.crm_tasks TO baec_app;
GRANT UPDATE (engine1_interaction_id) ON baec.crm_activities TO baec_app;
REVOKE ALL ON FUNCTION baec.refuse_change(), baec.users_guard(), baec.crm_tasks_guard(), baec.crm_activities_guard()
    FROM PUBLIC;
