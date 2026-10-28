-- 0005: External sign-in identities (V0.4 Phase 4). Runs as the migration role, which owns every object here.
--
-- One row links one OpenID Connect identity (issuer + subject: OIDC Core 1.0 says a subject is never reassigned
-- within its issuer) to one pre-provisioned BAEC user of one organization. Sign-in never creates a user, an
-- organization, or a link; an operator links an existing active HUMAN user with the provisioning tool
-- (baec_console/postgres/provision.py). Nothing else is stored here: no email, no name, no token, no password, no
-- CRM or account data.
--
-- Rows are append-only except one change: revocation (revoked_at, revoked_by), once, from unrevoked to revoked.
-- A revoked link is never restored; a person whose link was revoked is linked again only through a new subject.
--
-- The schema is outside tenant row-level security on purpose: at sign-in the organization is not known yet. It is
-- read by two roles the DBA bootstrap creates (never a migration), and by no other:
--
--   baec_auth_lookup  the runtime sign-in lookup. SELECT on five columns only, and only the one row whose issuer and
--                     subject equal the transaction's baec.auth_issuer and baec.auth_subject settings (policies
--                     subject_lookup and subject_boundary). With no setting it sees nothing; it can never list rows,
--                     organizations, or users, and it has no privilege on any other BAEC schema.
--   baec_provisioner  the operator's provisioning tool. Reads and links only inside one organization's tenant
--                     transaction (policies provisioning and provisioning_boundary), and may write only
--                     organizations, users, their audit, and links. Never used by the running application.
--
-- The application role (baec_app) has no privilege here at all. No function is SECURITY DEFINER.

CREATE SCHEMA baec_auth;

-- The issuer and subject the current transaction looks up, or NULL when none is set. Set only with
-- set_config(..., true) (transaction-local; baec_console/postgres/auth_lookup.py).
CREATE FUNCTION baec_auth.current_issuer() RETURNS text
    LANGUAGE sql STABLE
    AS $$ SELECT nullif(pg_catalog.current_setting('baec.auth_issuer', true), '') $$;

CREATE FUNCTION baec_auth.current_subject() RETURNS text
    LANGUAGE sql STABLE
    AS $$ SELECT nullif(pg_catalog.current_setting('baec.auth_subject', true), '') $$;

CREATE TABLE baec_auth.auth_identities (
    issuer text NOT NULL CHECK (issuer ~ '^https://[!-~]+$' AND char_length(issuer) <= 2048),
    subject text NOT NULL CHECK (subject ~ '^[!-~]+$' AND char_length(subject) <= 255),
    organization_id text NOT NULL,
    user_id text NOT NULL,
    linked_at timestamptz NOT NULL,
    linked_by text NOT NULL CHECK (btrim(linked_by) <> ''),
    revoked_at timestamptz,
    revoked_by text CHECK (btrim(revoked_by) <> ''),
    seq bigint GENERATED ALWAYS AS IDENTITY UNIQUE,
    PRIMARY KEY (issuer, subject),
    CHECK ((revoked_at IS NULL) = (revoked_by IS NULL)),
    CHECK (revoked_at IS NULL OR revoked_at >= linked_at),
    FOREIGN KEY (organization_id, user_id) REFERENCES baec.users (organization_id, user_id)
);

-- A user has at most one unrevoked sign-in link.
CREATE UNIQUE INDEX auth_identities_one_active_link_per_user ON baec_auth.auth_identities (user_id)
    WHERE revoked_at IS NULL;

-- Runs as the inserting role, so its read of baec.users is itself subject to tenant row-level security.
CREATE FUNCTION baec_auth.auth_identities_guard() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'sign-in identities are never deleted; revoke them' USING ERRCODE = 'restrict_violation';
    END IF;
    IF TG_OP = 'UPDATE' THEN
        IF OLD.revoked_at IS NOT NULL THEN
            RAISE EXCEPTION 'a revoked sign-in identity never changes' USING ERRCODE = 'restrict_violation';
        END IF;
        IF NEW.revoked_at IS NULL
           OR (NEW.issuer, NEW.subject, NEW.organization_id, NEW.user_id, NEW.linked_at, NEW.linked_by, NEW.seq)
              IS DISTINCT FROM
              (OLD.issuer, OLD.subject, OLD.organization_id, OLD.user_id, OLD.linked_at, OLD.linked_by, OLD.seq) THEN
            RAISE EXCEPTION 'a sign-in identity can only be revoked' USING ERRCODE = 'restrict_violation';
        END IF;
        RETURN NEW;
    END IF;
    IF NEW.organization_id IS DISTINCT FROM baec.current_organization() THEN
        RAISE EXCEPTION 'a sign-in identity is linked only inside its organization''s tenant transaction'
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.revoked_at IS NOT NULL OR NEW.revoked_by IS NOT NULL THEN
        RAISE EXCEPTION 'a sign-in identity is linked unrevoked' USING ERRCODE = 'check_violation';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM baec.users u WHERE u.organization_id = NEW.organization_id
                   AND u.user_id = NEW.user_id AND u.kind = 'HUMAN' AND u.active) THEN
        RAISE EXCEPTION 'only an active HUMAN user can be linked to a sign-in identity'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER auth_identities_guard BEFORE INSERT OR UPDATE OR DELETE ON baec_auth.auth_identities
    FOR EACH ROW EXECUTE FUNCTION baec_auth.auth_identities_guard();
CREATE TRIGGER auth_identities_no_truncate BEFORE TRUNCATE ON baec_auth.auth_identities
    FOR EACH STATEMENT EXECUTE FUNCTION baec.refuse_change();

ALTER TABLE baec_auth.auth_identities ENABLE ROW LEVEL SECURITY;
ALTER TABLE baec_auth.auth_identities FORCE ROW LEVEL SECURITY;

CREATE POLICY subject_lookup ON baec_auth.auth_identities AS PERMISSIVE FOR SELECT TO baec_auth_lookup
    USING (issuer = baec_auth.current_issuer() AND subject = baec_auth.current_subject());
CREATE POLICY subject_boundary ON baec_auth.auth_identities AS RESTRICTIVE FOR SELECT TO baec_auth_lookup
    USING (issuer = baec_auth.current_issuer() AND subject = baec_auth.current_subject());
CREATE POLICY provisioning ON baec_auth.auth_identities AS PERMISSIVE FOR ALL TO baec_provisioner
    USING (organization_id = baec.current_organization())
    WITH CHECK (organization_id = baec.current_organization());
CREATE POLICY provisioning_boundary ON baec_auth.auth_identities AS RESTRICTIVE FOR ALL TO baec_provisioner
    USING (organization_id = baec.current_organization())
    WITH CHECK (organization_id = baec.current_organization());

REVOKE ALL ON FUNCTION baec_auth.current_issuer(), baec_auth.current_subject(), baec_auth.auth_identities_guard()
    FROM PUBLIC;

GRANT USAGE ON SCHEMA baec_auth TO baec_auth_lookup;
GRANT EXECUTE ON FUNCTION baec_auth.current_issuer(), baec_auth.current_subject() TO baec_auth_lookup;
GRANT SELECT (issuer, subject, organization_id, user_id, revoked_at) ON baec_auth.auth_identities
    TO baec_auth_lookup;

GRANT USAGE ON SCHEMA baec, baec_auth TO baec_provisioner;
GRANT SELECT, INSERT ON baec.organizations, baec.users, baec.identity_audit TO baec_provisioner;
GRANT UPDATE (active) ON baec.users TO baec_provisioner;
GRANT SELECT, INSERT ON baec_auth.auth_identities TO baec_provisioner;
GRANT UPDATE (revoked_at, revoked_by) ON baec_auth.auth_identities TO baec_provisioner;
