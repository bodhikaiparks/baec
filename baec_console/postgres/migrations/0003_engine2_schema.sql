-- 0003: Engine 2 (monitoring and correspondence) persistence. Runs as the migration role, which owns every object here.
--
-- The released Engine 2 code (baec_app/engine2/persistence.py) is unchanged. It reaches these tables through
-- baec_console/postgres/engine2.py, a sqlite3-compatible connection over one tenant transaction, so every Engine 2
-- rule (domain validation on write and on load, duplicate refusal, the outcome snapshot recomputed on every load,
-- one write = one savepoint with its audit event) runs exactly as on SQLite. This schema reproduces the SQLite
-- schema's invariants; it is not a syntax translation:
--
--   SQLite (engine2.sqlite3)                       PostgreSQL (schema baec_engine2)
--   one file per workspace; local/ shared by       organization_id + workspace on every tenant row, filled from the
--     every organization                             tenant transaction's settings; keys and FKs include both
--   STRICT column types                            native column types (text, bigint)
--   json_valid(details_json)                       details_json IS JSON
--   <t>_no_update / <t>_no_delete (RAISE ABORT)    the same triggers, same message, ERRCODE restrict_violation
--   <t>_no_replace (refuses INSERT OR REPLACE)     the same trigger, same message, ERRCODE unique_violation;
--                                                    PostgreSQL has no REPLACE, and ON CONFLICT DO UPDATE is
--                                                    refused by <t>_no_update
--   observations/source_refusals_needs_activation  the same triggers and message, ERRCODE check_violation
--   ORDER BY rowid (insertion order)               seq, an identity column (the adapter maps rowid to seq)
--   audit_events.event_id INTEGER PRIMARY KEY      assigned max + 1 within (organization, workspace) under an
--     (max(rowid) + 1 per file)                      advisory lock, so ids are exactly what one SQLite file gives
--   PRAGMA user_version = 1, engine2_meta          engine2_meta (same two rows); the version is this migration
--   PRAGMA foreign_keys = ON per connection         foreign keys are always enforced
--   (no deletes: there is no truncate)             TRUNCATE is refused by trigger
--
-- Workspace scope. A third, restrictive policy limits every tenant row to baec.current_workspace(), so the unmodified
-- Engine 2 SQL, which has no organization or workspace filter, sees exactly one SQLite file's worth of rows. This is
-- dataset scope (implementation), enforced in the database; the security boundary is the organization policy.

CREATE SCHEMA baec_engine2;

-- The workspace the current transaction acts in ('local' or 'synthetic'), or NULL when none is set. Set only with
-- set_config('baec.workspace', <workspace>, true) (transaction-local; baec_console/postgres/database.py).
CREATE FUNCTION baec.current_workspace() RETURNS text
    LANGUAGE sql STABLE
    AS $$ SELECT nullif(pg_catalog.current_setting('baec.workspace', true), '') $$;

CREATE FUNCTION baec_engine2.refuse_change() RETURNS trigger
    LANGUAGE plpgsql
    AS $$ BEGIN RAISE EXCEPTION '% is append-only', TG_TABLE_NAME USING ERRCODE = 'restrict_violation'; END $$;

-- BEFORE INSERT: refuse a row whose key already exists in this organization's workspace. TG_ARGV names the key.
CREATE FUNCTION baec_engine2.refuse_replace() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
    condition text := 'organization_id = ($1).organization_id AND workspace = ($1).workspace';
    found boolean;
    i int;
BEGIN
    FOR i IN 0 .. TG_NARGS - 1 LOOP
        condition := condition || format(' AND %I = ($1).%I', TG_ARGV[i], TG_ARGV[i]);
    END LOOP;
    EXECUTE format('SELECT EXISTS (SELECT 1 FROM baec_engine2.%I WHERE %s)', TG_TABLE_NAME, condition)
        INTO found USING NEW;
    IF found THEN
        RAISE EXCEPTION '% rows cannot be replaced', TG_TABLE_NAME USING ERRCODE = 'unique_violation';
    END IF;
    RETURN NEW;
END $$;

-- BEFORE INSERT on observations and source_refusals: evidence and refusals exist only under an ACTIVATED plan.
CREATE FUNCTION baec_engine2.require_activation() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    IF COALESCE((SELECT a.result FROM baec_engine2.plan_activations a
                 WHERE a.organization_id = NEW.organization_id AND a.workspace = NEW.workspace
                   AND a.activation_id = NEW.activation_id), '') <> 'ACTIVATED' THEN
        RAISE EXCEPTION '% requires an ACTIVATED Monitoring Plan', TG_TABLE_NAME USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;

-- BEFORE INSERT on audit_events: SQLite's INTEGER PRIMARY KEY gives max(event_id) + 1 within one file. The advisory
-- lock serializes writers of one workspace, as SQLite's single writer does.
CREATE FUNCTION baec_engine2.assign_event_id() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    IF NEW.event_id IS NULL THEN
        PERFORM pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(
            'baec_engine2.audit_events/' || NEW.organization_id || '/' || NEW.workspace, 0));
        SELECT COALESCE(max(e.event_id), 0) + 1 INTO NEW.event_id FROM baec_engine2.audit_events e
        WHERE e.organization_id = NEW.organization_id AND e.workspace = NEW.workspace;
    END IF;
    RETURN NEW;
END $$;

-- Store metadata, not tenant data: the same two rows engine2.sqlite3 carries. Read-only for the application.
CREATE TABLE baec_engine2.engine2_meta (
    key text PRIMARY KEY,
    value text NOT NULL
);
INSERT INTO baec_engine2.engine2_meta (key, value) VALUES ('engine', 'BAEC_ENGINE_2'), ('schema_version', '1');

-- Every tenant table starts with these columns. The defaults come from the tenant transaction; NULL (no tenant set)
-- violates NOT NULL, so nothing can be written outside one.
--   organization_id text NOT NULL DEFAULT baec.current_organization() REFERENCES baec.organizations
--   workspace text NOT NULL DEFAULT baec.current_workspace() CHECK (workspace IN ('local', 'synthetic'))

CREATE TABLE baec_engine2.authorized_sources (
    organization_id text NOT NULL DEFAULT baec.current_organization() REFERENCES baec.organizations (organization_id),
    workspace text NOT NULL DEFAULT baec.current_workspace() CHECK (workspace IN ('local', 'synthetic')),
    source_id text NOT NULL,
    source_type text NOT NULL, owner_or_publisher text NOT NULL, legitimate_basis text NOT NULL,
    access_method text NOT NULL, permitted_capture text NOT NULL, authorized_by text NOT NULL,
    authorized_at text NOT NULL, known_limitations text,
    recorded_at text NOT NULL, recorded_by text NOT NULL,
    seq bigint GENERATED ALWAYS AS IDENTITY UNIQUE,
    PRIMARY KEY (organization_id, workspace, source_id)
);

CREATE TABLE baec_engine2.monitoring_plans (
    organization_id text NOT NULL DEFAULT baec.current_organization() REFERENCES baec.organizations (organization_id),
    workspace text NOT NULL DEFAULT baec.current_workspace() CHECK (workspace IN ('local', 'synthetic')),
    monitoring_plan_id text NOT NULL,
    plan_version bigint NOT NULL CHECK (plan_version >= 1),
    baec_id text NOT NULL, condition_reference text NOT NULL,
    engine1_currentness_reference text NOT NULL,
    authorized_by text NOT NULL, authorization_reference text NOT NULL, authorized_at text NOT NULL,
    threshold_comparator text CHECK (threshold_comparator IS NULL OR threshold_comparator IN (
        'GREATER_THAN', 'AT_LEAST', 'LESS_THAN', 'AT_MOST', 'EXACTLY', 'APPROXIMATELY', 'QUALITATIVE_ONLY',
        'NONE_STATED')),
    threshold_value text, threshold_unit text,
    timing_context text,
    recorded_at text NOT NULL, recorded_by text NOT NULL,
    seq bigint GENERATED ALWAYS AS IDENTITY UNIQUE,
    PRIMARY KEY (organization_id, workspace, monitoring_plan_id, plan_version),
    CHECK ((threshold_comparator IS NULL) = (threshold_value IS NULL)
           AND (threshold_value IS NULL) = (threshold_unit IS NULL))
);

CREATE TABLE baec_engine2.plan_target_entities (
    organization_id text NOT NULL DEFAULT baec.current_organization() REFERENCES baec.organizations (organization_id),
    workspace text NOT NULL DEFAULT baec.current_workspace() CHECK (workspace IN ('local', 'synthetic')),
    monitoring_plan_id text NOT NULL, plan_version bigint NOT NULL CHECK (plan_version >= 1),
    position bigint NOT NULL CHECK (position >= 0),
    entity text NOT NULL,
    seq bigint GENERATED ALWAYS AS IDENTITY UNIQUE,
    PRIMARY KEY (organization_id, workspace, monitoring_plan_id, plan_version, position),
    UNIQUE (organization_id, workspace, monitoring_plan_id, plan_version, entity),
    FOREIGN KEY (organization_id, workspace, monitoring_plan_id, plan_version)
        REFERENCES baec_engine2.monitoring_plans (organization_id, workspace, monitoring_plan_id, plan_version)
);

CREATE TABLE baec_engine2.plan_dimensions (
    organization_id text NOT NULL DEFAULT baec.current_organization() REFERENCES baec.organizations (organization_id),
    workspace text NOT NULL DEFAULT baec.current_workspace() CHECK (workspace IN ('local', 'synthetic')),
    monitoring_plan_id text NOT NULL, plan_version bigint NOT NULL CHECK (plan_version >= 1),
    dimension text NOT NULL CHECK (dimension IN (
        'ENTITY_MATCH', 'RELATIONSHIP_MATCH', 'SOURCE_RELEVANCE', 'EVENT_OR_CONDITION_TYPE', 'THRESHOLD_MATCH',
        'COMPARATOR_MATCH', 'NUMERIC_MAGNITUDE', 'UNIT_MATCH', 'TIMING_MATCH', 'RENEWAL_OR_EFFECTIVE_DATE_MATCH',
        'CAUSAL_OR_CONTEXTUAL_RELEVANCE')),
    requirement text NOT NULL CHECK (requirement IN ('REQUIRED', 'NOT_APPLICABLE')),
    seq bigint GENERATED ALWAYS AS IDENTITY UNIQUE,
    PRIMARY KEY (organization_id, workspace, monitoring_plan_id, plan_version, dimension),
    FOREIGN KEY (organization_id, workspace, monitoring_plan_id, plan_version)
        REFERENCES baec_engine2.monitoring_plans (organization_id, workspace, monitoring_plan_id, plan_version)
);

CREATE TABLE baec_engine2.plan_references (
    organization_id text NOT NULL DEFAULT baec.current_organization() REFERENCES baec.organizations (organization_id),
    workspace text NOT NULL DEFAULT baec.current_workspace() CHECK (workspace IN ('local', 'synthetic')),
    monitoring_plan_id text NOT NULL, plan_version bigint NOT NULL CHECK (plan_version >= 1),
    reference_id text NOT NULL,
    kind text NOT NULL CHECK (kind IN ('FACT', 'RULE')),
    seq bigint GENERATED ALWAYS AS IDENTITY UNIQUE,
    PRIMARY KEY (organization_id, workspace, monitoring_plan_id, plan_version, reference_id),
    FOREIGN KEY (organization_id, workspace, monitoring_plan_id, plan_version)
        REFERENCES baec_engine2.monitoring_plans (organization_id, workspace, monitoring_plan_id, plan_version)
);

CREATE TABLE baec_engine2.plan_authorized_sources (
    organization_id text NOT NULL DEFAULT baec.current_organization() REFERENCES baec.organizations (organization_id),
    workspace text NOT NULL DEFAULT baec.current_workspace() CHECK (workspace IN ('local', 'synthetic')),
    monitoring_plan_id text NOT NULL, plan_version bigint NOT NULL CHECK (plan_version >= 1),
    source_id text NOT NULL,
    seq bigint GENERATED ALWAYS AS IDENTITY UNIQUE,
    PRIMARY KEY (organization_id, workspace, monitoring_plan_id, plan_version, source_id),
    FOREIGN KEY (organization_id, workspace, source_id)
        REFERENCES baec_engine2.authorized_sources (organization_id, workspace, source_id),
    FOREIGN KEY (organization_id, workspace, monitoring_plan_id, plan_version)
        REFERENCES baec_engine2.monitoring_plans (organization_id, workspace, monitoring_plan_id, plan_version)
);

CREATE TABLE baec_engine2.plan_activations (
    organization_id text NOT NULL DEFAULT baec.current_organization() REFERENCES baec.organizations (organization_id),
    workspace text NOT NULL DEFAULT baec.current_workspace() CHECK (workspace IN ('local', 'synthetic')),
    activation_id text NOT NULL,
    monitoring_plan_id text NOT NULL, plan_version bigint NOT NULL CHECK (plan_version >= 1),
    result text NOT NULL CHECK (result IN ('ACTIVATED', 'REFUSED')),
    reading_present bigint NOT NULL CHECK (reading_present IN (0, 1)),
    reading_baec_id text,
    reading_staleness_status text CHECK (reading_staleness_status IS NULL OR reading_staleness_status IN (
        'CURRENT', 'REVIEW_DUE', 'STALE', 'RETIRED')),
    reading_record_reference text,
    refusal_reason text,
    recorded_at text NOT NULL, recorded_by text NOT NULL,
    seq bigint GENERATED ALWAYS AS IDENTITY UNIQUE,
    PRIMARY KEY (organization_id, workspace, activation_id),
    CHECK ((result = 'ACTIVATED') = (refusal_reason IS NULL)),
    CHECK (reading_present = 1 OR (reading_baec_id IS NULL AND reading_staleness_status IS NULL
                                   AND reading_record_reference IS NULL)),
    UNIQUE (organization_id, workspace, activation_id, monitoring_plan_id, plan_version),
    FOREIGN KEY (organization_id, workspace, monitoring_plan_id, plan_version)
        REFERENCES baec_engine2.monitoring_plans (organization_id, workspace, monitoring_plan_id, plan_version)
);

CREATE TABLE baec_engine2.observations (
    organization_id text NOT NULL DEFAULT baec.current_organization() REFERENCES baec.organizations (organization_id),
    workspace text NOT NULL DEFAULT baec.current_workspace() CHECK (workspace IN ('local', 'synthetic')),
    observation_id text NOT NULL,
    monitoring_plan_id text NOT NULL, plan_version bigint NOT NULL CHECK (plan_version >= 1),
    activation_id text NOT NULL,
    source_id text NOT NULL,
    source_type text NOT NULL,
    observed_at text NOT NULL, published_at text, effective_at text,
    exact_evidence_content text NOT NULL,
    source_locator text NOT NULL,
    provenance text NOT NULL CHECK (provenance IN ('BUYER_FACT', 'SELLER_OBSERVATION', 'EXTERNAL_EVIDENCE')),
    content_sha256 text NOT NULL CHECK (length(content_sha256) = 64),
    acquisition_method text NOT NULL,
    authorization_reference text NOT NULL,
    evidence_event_id text NOT NULL,
    recorded_at text NOT NULL, recorded_by text NOT NULL,
    seq bigint GENERATED ALWAYS AS IDENTITY UNIQUE,
    PRIMARY KEY (organization_id, workspace, observation_id),
    UNIQUE (organization_id, workspace, observation_id, monitoring_plan_id, plan_version),
    FOREIGN KEY (organization_id, workspace, activation_id, monitoring_plan_id, plan_version)
        REFERENCES baec_engine2.plan_activations (organization_id, workspace, activation_id, monitoring_plan_id,
                                                  plan_version),
    FOREIGN KEY (organization_id, workspace, monitoring_plan_id, plan_version, source_id)
        REFERENCES baec_engine2.plan_authorized_sources (organization_id, workspace, monitoring_plan_id, plan_version,
                                                         source_id)
);

CREATE TABLE baec_engine2.source_refusals (
    organization_id text NOT NULL DEFAULT baec.current_organization() REFERENCES baec.organizations (organization_id),
    workspace text NOT NULL DEFAULT baec.current_workspace() CHECK (workspace IN ('local', 'synthetic')),
    monitoring_plan_id text NOT NULL, plan_version bigint NOT NULL CHECK (plan_version >= 1),
    item_id text NOT NULL,
    activation_id text NOT NULL,
    source_reference text,
    reason text NOT NULL,
    recorded_at text NOT NULL, recorded_by text NOT NULL,
    seq bigint GENERATED ALWAYS AS IDENTITY UNIQUE,
    PRIMARY KEY (organization_id, workspace, monitoring_plan_id, plan_version, item_id),
    FOREIGN KEY (organization_id, workspace, activation_id, monitoring_plan_id, plan_version)
        REFERENCES baec_engine2.plan_activations (organization_id, workspace, activation_id, monitoring_plan_id,
                                                  plan_version)
);

CREATE TABLE baec_engine2.supersessions (
    organization_id text NOT NULL DEFAULT baec.current_organization() REFERENCES baec.organizations (organization_id),
    workspace text NOT NULL DEFAULT baec.current_workspace() CHECK (workspace IN ('local', 'synthetic')),
    superseded_observation_id text NOT NULL,
    superseding_observation_id text NOT NULL,
    monitoring_plan_id text NOT NULL, plan_version bigint NOT NULL CHECK (plan_version >= 1),
    basis text NOT NULL CHECK (basis IN (
        'explicit correction or retraction language', 'version or revision metadata', 'contractual amendment hierarchy',
        'effective-date logic', 'Monitoring Plan source authority rule')),
    retraction bigint NOT NULL CHECK (retraction IN (0, 1)),
    recorded_at text NOT NULL, recorded_by text NOT NULL,
    seq bigint GENERATED ALWAYS AS IDENTITY UNIQUE,
    PRIMARY KEY (organization_id, workspace, superseded_observation_id),
    CHECK (superseded_observation_id <> superseding_observation_id),
    UNIQUE (organization_id, workspace, superseded_observation_id, monitoring_plan_id, plan_version),
    FOREIGN KEY (organization_id, workspace, superseded_observation_id, monitoring_plan_id, plan_version)
        REFERENCES baec_engine2.observations (organization_id, workspace, observation_id, monitoring_plan_id,
                                              plan_version),
    FOREIGN KEY (organization_id, workspace, superseding_observation_id, monitoring_plan_id, plan_version)
        REFERENCES baec_engine2.observations (organization_id, workspace, observation_id, monitoring_plan_id,
                                              plan_version)
);

CREATE TABLE baec_engine2.derived_measurements (
    organization_id text NOT NULL DEFAULT baec.current_organization() REFERENCES baec.organizations (organization_id),
    workspace text NOT NULL DEFAULT baec.current_workspace() CHECK (workspace IN ('local', 'synthetic')),
    measurement_id text NOT NULL,
    monitoring_plan_id text NOT NULL, plan_version bigint NOT NULL CHECK (plan_version >= 1),
    transformation text NOT NULL CHECK (transformation IN (
        'corpus.percent_change_from_two_prices', 'corpus.basis_points_to_percent')),
    transformation_version text NOT NULL,
    formula text NOT NULL,
    rounding_rule text NOT NULL,
    output_value text NOT NULL,
    output_unit text NOT NULL,
    calculated_at text NOT NULL,
    recorded_at text NOT NULL, recorded_by text NOT NULL,
    seq bigint GENERATED ALWAYS AS IDENTITY UNIQUE,
    PRIMARY KEY (organization_id, workspace, measurement_id),
    UNIQUE (organization_id, workspace, measurement_id, monitoring_plan_id, plan_version),
    FOREIGN KEY (organization_id, workspace, monitoring_plan_id, plan_version)
        REFERENCES baec_engine2.monitoring_plans (organization_id, workspace, monitoring_plan_id, plan_version)
);

CREATE TABLE baec_engine2.measurement_inputs (
    organization_id text NOT NULL DEFAULT baec.current_organization() REFERENCES baec.organizations (organization_id),
    workspace text NOT NULL DEFAULT baec.current_workspace() CHECK (workspace IN ('local', 'synthetic')),
    measurement_id text NOT NULL,
    monitoring_plan_id text NOT NULL, plan_version bigint NOT NULL CHECK (plan_version >= 1),
    position bigint NOT NULL CHECK (position >= 0),
    observation_id text NOT NULL,
    quantity text NOT NULL, value text NOT NULL, unit text NOT NULL,
    seq bigint GENERATED ALWAYS AS IDENTITY UNIQUE,
    PRIMARY KEY (organization_id, workspace, measurement_id, position),
    FOREIGN KEY (organization_id, workspace, measurement_id, monitoring_plan_id, plan_version)
        REFERENCES baec_engine2.derived_measurements (organization_id, workspace, measurement_id, monitoring_plan_id,
                                                      plan_version),
    FOREIGN KEY (organization_id, workspace, observation_id, monitoring_plan_id, plan_version)
        REFERENCES baec_engine2.observations (organization_id, workspace, observation_id, monitoring_plan_id,
                                              plan_version)
);

CREATE TABLE baec_engine2.signal_candidates (
    organization_id text NOT NULL DEFAULT baec.current_organization() REFERENCES baec.organizations (organization_id),
    workspace text NOT NULL DEFAULT baec.current_workspace() CHECK (workspace IN ('local', 'synthetic')),
    signal_candidate_id text NOT NULL,
    monitoring_plan_id text NOT NULL, plan_version bigint NOT NULL CHECK (plan_version >= 1),
    selected_by text NOT NULL,
    recorded_at text NOT NULL, recorded_by text NOT NULL,
    seq bigint GENERATED ALWAYS AS IDENTITY UNIQUE,
    PRIMARY KEY (organization_id, workspace, signal_candidate_id),
    UNIQUE (organization_id, workspace, signal_candidate_id, monitoring_plan_id, plan_version),
    FOREIGN KEY (organization_id, workspace, monitoring_plan_id, plan_version)
        REFERENCES baec_engine2.monitoring_plans (organization_id, workspace, monitoring_plan_id, plan_version)
);

CREATE TABLE baec_engine2.signal_candidate_observations (
    organization_id text NOT NULL DEFAULT baec.current_organization() REFERENCES baec.organizations (organization_id),
    workspace text NOT NULL DEFAULT baec.current_workspace() CHECK (workspace IN ('local', 'synthetic')),
    signal_candidate_id text NOT NULL,
    monitoring_plan_id text NOT NULL, plan_version bigint NOT NULL CHECK (plan_version >= 1),
    position bigint NOT NULL CHECK (position >= 0),
    observation_id text NOT NULL,
    seq bigint GENERATED ALWAYS AS IDENTITY UNIQUE,
    PRIMARY KEY (organization_id, workspace, signal_candidate_id, position),
    UNIQUE (organization_id, workspace, signal_candidate_id, observation_id),
    FOREIGN KEY (organization_id, workspace, signal_candidate_id, monitoring_plan_id, plan_version)
        REFERENCES baec_engine2.signal_candidates (organization_id, workspace, signal_candidate_id, monitoring_plan_id,
                                                   plan_version),
    FOREIGN KEY (organization_id, workspace, observation_id, monitoring_plan_id, plan_version)
        REFERENCES baec_engine2.observations (organization_id, workspace, observation_id, monitoring_plan_id,
                                              plan_version)
);

CREATE TABLE baec_engine2.signal_candidate_dimensions (
    organization_id text NOT NULL DEFAULT baec.current_organization() REFERENCES baec.organizations (organization_id),
    workspace text NOT NULL DEFAULT baec.current_workspace() CHECK (workspace IN ('local', 'synthetic')),
    signal_candidate_id text NOT NULL,
    dimension text NOT NULL CHECK (dimension IN (
        'ENTITY_MATCH', 'RELATIONSHIP_MATCH', 'SOURCE_RELEVANCE', 'EVENT_OR_CONDITION_TYPE', 'THRESHOLD_MATCH',
        'COMPARATOR_MATCH', 'NUMERIC_MAGNITUDE', 'UNIT_MATCH', 'TIMING_MATCH', 'RENEWAL_OR_EFFECTIVE_DATE_MATCH',
        'CAUSAL_OR_CONTEXTUAL_RELEVANCE')),
    seq bigint GENERATED ALWAYS AS IDENTITY UNIQUE,
    PRIMARY KEY (organization_id, workspace, signal_candidate_id, dimension),
    FOREIGN KEY (organization_id, workspace, signal_candidate_id)
        REFERENCES baec_engine2.signal_candidates (organization_id, workspace, signal_candidate_id)
);

CREATE TABLE baec_engine2.correspondence_candidates (
    organization_id text NOT NULL DEFAULT baec.current_organization() REFERENCES baec.organizations (organization_id),
    workspace text NOT NULL DEFAULT baec.current_workspace() CHECK (workspace IN ('local', 'synthetic')),
    candidate_id text NOT NULL,
    baec_id text NOT NULL,
    monitoring_plan_id text NOT NULL, plan_version bigint NOT NULL CHECK (plan_version >= 1),
    recorded_at text NOT NULL, recorded_by text NOT NULL,
    seq bigint GENERATED ALWAYS AS IDENTITY UNIQUE,
    PRIMARY KEY (organization_id, workspace, candidate_id),
    UNIQUE (organization_id, workspace, candidate_id, monitoring_plan_id, plan_version),
    FOREIGN KEY (organization_id, workspace, monitoring_plan_id, plan_version)
        REFERENCES baec_engine2.monitoring_plans (organization_id, workspace, monitoring_plan_id, plan_version)
);

CREATE TABLE baec_engine2.candidate_signals (
    organization_id text NOT NULL DEFAULT baec.current_organization() REFERENCES baec.organizations (organization_id),
    workspace text NOT NULL DEFAULT baec.current_workspace() CHECK (workspace IN ('local', 'synthetic')),
    candidate_id text NOT NULL,
    monitoring_plan_id text NOT NULL, plan_version bigint NOT NULL CHECK (plan_version >= 1),
    position bigint NOT NULL CHECK (position >= 0),
    signal_candidate_id text NOT NULL,
    seq bigint GENERATED ALWAYS AS IDENTITY UNIQUE,
    PRIMARY KEY (organization_id, workspace, candidate_id, position),
    UNIQUE (organization_id, workspace, candidate_id, signal_candidate_id),
    FOREIGN KEY (organization_id, workspace, candidate_id, monitoring_plan_id, plan_version)
        REFERENCES baec_engine2.correspondence_candidates (organization_id, workspace, candidate_id,
                                                           monitoring_plan_id, plan_version),
    FOREIGN KEY (organization_id, workspace, signal_candidate_id, monitoring_plan_id, plan_version)
        REFERENCES baec_engine2.signal_candidates (organization_id, workspace, signal_candidate_id, monitoring_plan_id,
                                                   plan_version)
);

CREATE TABLE baec_engine2.reviews (
    organization_id text NOT NULL DEFAULT baec.current_organization() REFERENCES baec.organizations (organization_id),
    workspace text NOT NULL DEFAULT baec.current_workspace() CHECK (workspace IN ('local', 'synthetic')),
    review_id text NOT NULL,
    candidate_id text NOT NULL,
    monitoring_plan_id text NOT NULL, plan_version bigint NOT NULL CHECK (plan_version >= 1),
    activation_id text NOT NULL,
    sufficiency text NOT NULL CHECK (sufficiency IN ('YES', 'NO', 'UNKNOWN')),
    reviewer text NOT NULL,
    reviewed_at text NOT NULL,
    baec_revalidation_required bigint NOT NULL CHECK (baec_revalidation_required IN (0, 1)),
    outcome_snapshot text NOT NULL CHECK (outcome_snapshot IN (
        'NO_CORRESPONDENCE', 'POSSIBLE_CORRESPONDENCE', 'HUMAN_VERIFIED_CORRESPONDENCE')),
    recorded_at text NOT NULL, recorded_by text NOT NULL,
    seq bigint GENERATED ALWAYS AS IDENTITY UNIQUE,
    PRIMARY KEY (organization_id, workspace, review_id),
    UNIQUE (organization_id, workspace, review_id, monitoring_plan_id, plan_version),
    FOREIGN KEY (organization_id, workspace, candidate_id, monitoring_plan_id, plan_version)
        REFERENCES baec_engine2.correspondence_candidates (organization_id, workspace, candidate_id,
                                                           monitoring_plan_id, plan_version),
    FOREIGN KEY (organization_id, workspace, activation_id, monitoring_plan_id, plan_version)
        REFERENCES baec_engine2.plan_activations (organization_id, workspace, activation_id, monitoring_plan_id,
                                                  plan_version)
);

CREATE TABLE baec_engine2.review_ledger_observations (
    organization_id text NOT NULL DEFAULT baec.current_organization() REFERENCES baec.organizations (organization_id),
    workspace text NOT NULL DEFAULT baec.current_workspace() CHECK (workspace IN ('local', 'synthetic')),
    review_id text NOT NULL, monitoring_plan_id text NOT NULL, plan_version bigint NOT NULL CHECK (plan_version >= 1),
    position bigint NOT NULL CHECK (position >= 0), observation_id text NOT NULL,
    seq bigint GENERATED ALWAYS AS IDENTITY UNIQUE,
    PRIMARY KEY (organization_id, workspace, review_id, position),
    UNIQUE (organization_id, workspace, review_id, observation_id),
    FOREIGN KEY (organization_id, workspace, review_id, monitoring_plan_id, plan_version)
        REFERENCES baec_engine2.reviews (organization_id, workspace, review_id, monitoring_plan_id, plan_version),
    FOREIGN KEY (organization_id, workspace, observation_id, monitoring_plan_id, plan_version)
        REFERENCES baec_engine2.observations (organization_id, workspace, observation_id, monitoring_plan_id,
                                              plan_version)
);

CREATE TABLE baec_engine2.review_ledger_supersessions (
    organization_id text NOT NULL DEFAULT baec.current_organization() REFERENCES baec.organizations (organization_id),
    workspace text NOT NULL DEFAULT baec.current_workspace() CHECK (workspace IN ('local', 'synthetic')),
    review_id text NOT NULL, monitoring_plan_id text NOT NULL, plan_version bigint NOT NULL CHECK (plan_version >= 1),
    position bigint NOT NULL CHECK (position >= 0),
    superseded_observation_id text NOT NULL,
    seq bigint GENERATED ALWAYS AS IDENTITY UNIQUE,
    PRIMARY KEY (organization_id, workspace, review_id, position),
    UNIQUE (organization_id, workspace, review_id, superseded_observation_id),
    FOREIGN KEY (organization_id, workspace, review_id, monitoring_plan_id, plan_version)
        REFERENCES baec_engine2.reviews (organization_id, workspace, review_id, monitoring_plan_id, plan_version),
    FOREIGN KEY (organization_id, workspace, superseded_observation_id, monitoring_plan_id, plan_version)
        REFERENCES baec_engine2.supersessions (organization_id, workspace, superseded_observation_id,
                                               monitoring_plan_id, plan_version)
);

CREATE TABLE baec_engine2.review_ledger_measurements (
    organization_id text NOT NULL DEFAULT baec.current_organization() REFERENCES baec.organizations (organization_id),
    workspace text NOT NULL DEFAULT baec.current_workspace() CHECK (workspace IN ('local', 'synthetic')),
    review_id text NOT NULL, monitoring_plan_id text NOT NULL, plan_version bigint NOT NULL CHECK (plan_version >= 1),
    position bigint NOT NULL CHECK (position >= 0), measurement_id text NOT NULL,
    seq bigint GENERATED ALWAYS AS IDENTITY UNIQUE,
    PRIMARY KEY (organization_id, workspace, review_id, position),
    UNIQUE (organization_id, workspace, review_id, measurement_id),
    FOREIGN KEY (organization_id, workspace, review_id, monitoring_plan_id, plan_version)
        REFERENCES baec_engine2.reviews (organization_id, workspace, review_id, monitoring_plan_id, plan_version),
    FOREIGN KEY (organization_id, workspace, measurement_id, monitoring_plan_id, plan_version)
        REFERENCES baec_engine2.derived_measurements (organization_id, workspace, measurement_id, monitoring_plan_id,
                                                      plan_version)
);

CREATE TABLE baec_engine2.review_ledger_refusals (
    organization_id text NOT NULL DEFAULT baec.current_organization() REFERENCES baec.organizations (organization_id),
    workspace text NOT NULL DEFAULT baec.current_workspace() CHECK (workspace IN ('local', 'synthetic')),
    review_id text NOT NULL, monitoring_plan_id text NOT NULL, plan_version bigint NOT NULL CHECK (plan_version >= 1),
    position bigint NOT NULL CHECK (position >= 0), item_id text NOT NULL,
    seq bigint GENERATED ALWAYS AS IDENTITY UNIQUE,
    PRIMARY KEY (organization_id, workspace, review_id, position),
    UNIQUE (organization_id, workspace, review_id, item_id),
    FOREIGN KEY (organization_id, workspace, review_id, monitoring_plan_id, plan_version)
        REFERENCES baec_engine2.reviews (organization_id, workspace, review_id, monitoring_plan_id, plan_version),
    FOREIGN KEY (organization_id, workspace, monitoring_plan_id, plan_version, item_id)
        REFERENCES baec_engine2.source_refusals (organization_id, workspace, monitoring_plan_id, plan_version, item_id)
);

CREATE TABLE baec_engine2.review_findings (
    organization_id text NOT NULL DEFAULT baec.current_organization() REFERENCES baec.organizations (organization_id),
    workspace text NOT NULL DEFAULT baec.current_workspace() CHECK (workspace IN ('local', 'synthetic')),
    review_id text NOT NULL,
    position bigint NOT NULL CHECK (position >= 0),
    dimension text NOT NULL CHECK (dimension IN (
        'ENTITY_MATCH', 'RELATIONSHIP_MATCH', 'SOURCE_RELEVANCE', 'EVENT_OR_CONDITION_TYPE', 'THRESHOLD_MATCH',
        'COMPARATOR_MATCH', 'NUMERIC_MAGNITUDE', 'UNIT_MATCH', 'TIMING_MATCH', 'RENEWAL_OR_EFFECTIVE_DATE_MATCH',
        'CAUSAL_OR_CONTEXTUAL_RELEVANCE')),
    finding text NOT NULL CHECK (finding IN ('SUPPORTED', 'CONTRADICTED', 'UNRESOLVED', 'NOT_APPLICABLE')),
    reason text NOT NULL,
    seq bigint GENERATED ALWAYS AS IDENTITY UNIQUE,
    PRIMARY KEY (organization_id, workspace, review_id, dimension),
    UNIQUE (organization_id, workspace, review_id, position),
    FOREIGN KEY (organization_id, workspace, review_id)
        REFERENCES baec_engine2.reviews (organization_id, workspace, review_id)
);

CREATE TABLE baec_engine2.review_finding_evidence (
    organization_id text NOT NULL DEFAULT baec.current_organization() REFERENCES baec.organizations (organization_id),
    workspace text NOT NULL DEFAULT baec.current_workspace() CHECK (workspace IN ('local', 'synthetic')),
    review_id text NOT NULL, dimension text NOT NULL,
    position bigint NOT NULL CHECK (position >= 0), evidence_ref text NOT NULL,
    seq bigint GENERATED ALWAYS AS IDENTITY UNIQUE,
    PRIMARY KEY (organization_id, workspace, review_id, dimension, position),
    UNIQUE (organization_id, workspace, review_id, dimension, evidence_ref),
    FOREIGN KEY (organization_id, workspace, review_id, dimension)
        REFERENCES baec_engine2.review_findings (organization_id, workspace, review_id, dimension)
);

CREATE TABLE baec_engine2.review_checks (
    organization_id text NOT NULL DEFAULT baec.current_organization() REFERENCES baec.organizations (organization_id),
    workspace text NOT NULL DEFAULT baec.current_workspace() CHECK (workspace IN ('local', 'synthetic')),
    review_id text NOT NULL,
    position bigint NOT NULL CHECK (position >= 0),
    check_name text NOT NULL CHECK (check_name IN (
        'FRESHNESS', 'CONTRADICTORY_EVIDENCE', 'MISSING_EVIDENCE', 'PROVENANCE_QUALITY')),
    note text NOT NULL,
    seq bigint GENERATED ALWAYS AS IDENTITY UNIQUE,
    PRIMARY KEY (organization_id, workspace, review_id, check_name),
    UNIQUE (organization_id, workspace, review_id, position),
    FOREIGN KEY (organization_id, workspace, review_id)
        REFERENCES baec_engine2.reviews (organization_id, workspace, review_id)
);

CREATE TABLE baec_engine2.review_check_evidence (
    organization_id text NOT NULL DEFAULT baec.current_organization() REFERENCES baec.organizations (organization_id),
    workspace text NOT NULL DEFAULT baec.current_workspace() CHECK (workspace IN ('local', 'synthetic')),
    review_id text NOT NULL, check_name text NOT NULL,
    position bigint NOT NULL CHECK (position >= 0), evidence_ref text NOT NULL,
    seq bigint GENERATED ALWAYS AS IDENTITY UNIQUE,
    PRIMARY KEY (organization_id, workspace, review_id, check_name, position),
    UNIQUE (organization_id, workspace, review_id, check_name, evidence_ref),
    FOREIGN KEY (organization_id, workspace, review_id, check_name)
        REFERENCES baec_engine2.review_checks (organization_id, workspace, review_id, check_name)
);

CREATE TABLE baec_engine2.review_check_dimensions (
    organization_id text NOT NULL DEFAULT baec.current_organization() REFERENCES baec.organizations (organization_id),
    workspace text NOT NULL DEFAULT baec.current_workspace() CHECK (workspace IN ('local', 'synthetic')),
    review_id text NOT NULL, check_name text NOT NULL,
    dimension text NOT NULL CHECK (dimension IN (
        'ENTITY_MATCH', 'RELATIONSHIP_MATCH', 'SOURCE_RELEVANCE', 'EVENT_OR_CONDITION_TYPE', 'THRESHOLD_MATCH',
        'COMPARATOR_MATCH', 'NUMERIC_MAGNITUDE', 'UNIT_MATCH', 'TIMING_MATCH', 'RENEWAL_OR_EFFECTIVE_DATE_MATCH',
        'CAUSAL_OR_CONTEXTUAL_RELEVANCE')),
    seq bigint GENERATED ALWAYS AS IDENTITY UNIQUE,
    PRIMARY KEY (organization_id, workspace, review_id, check_name, dimension),
    FOREIGN KEY (organization_id, workspace, review_id, check_name)
        REFERENCES baec_engine2.review_checks (organization_id, workspace, review_id, check_name)
);

CREATE TABLE baec_engine2.review_revalidation_triggers (
    organization_id text NOT NULL DEFAULT baec.current_organization() REFERENCES baec.organizations (organization_id),
    workspace text NOT NULL DEFAULT baec.current_workspace() CHECK (workspace IN ('local', 'synthetic')),
    review_id text NOT NULL,
    position bigint NOT NULL CHECK (position >= 0),
    trigger_text text NOT NULL,
    seq bigint GENERATED ALWAYS AS IDENTITY UNIQUE,
    PRIMARY KEY (organization_id, workspace, review_id, position),
    UNIQUE (organization_id, workspace, review_id, trigger_text),
    FOREIGN KEY (organization_id, workspace, review_id)
        REFERENCES baec_engine2.reviews (organization_id, workspace, review_id)
);

CREATE TABLE baec_engine2.audit_events (
    organization_id text NOT NULL DEFAULT baec.current_organization() REFERENCES baec.organizations (organization_id),
    workspace text NOT NULL DEFAULT baec.current_workspace() CHECK (workspace IN ('local', 'synthetic')),
    event_id bigint NOT NULL,
    event_type text NOT NULL CHECK (event_type IN (
        'AUTHORIZED_SOURCE_RECORDED', 'MONITORING_PLAN_RECORDED', 'PLAN_ACTIVATION_RECORDED',
        'PLAN_ACTIVATION_REFUSED', 'SOURCE_ITEM_REFUSED', 'OBSERVATION_RECORDED', 'SUPERSESSION_RECORDED',
        'DERIVED_MEASUREMENT_RECORDED', 'SIGNAL_CANDIDATE_RECORDED', 'CORRESPONDENCE_CANDIDATE_RECORDED',
        'HUMAN_REVIEW_RECORDED')),
    entity_type text NOT NULL,
    entity_id text NOT NULL,
    recorded_at text NOT NULL,
    actor text NOT NULL,
    details_json text NOT NULL CHECK (details_json IS JSON),
    seq bigint GENERATED ALWAYS AS IDENTITY UNIQUE,
    PRIMARY KEY (organization_id, workspace, event_id)
);

CREATE TRIGGER audit_events_assign_event_id BEFORE INSERT ON baec_engine2.audit_events
    FOR EACH ROW EXECUTE FUNCTION baec_engine2.assign_event_id();

-- The 92 SQLite triggers: <t>_no_update, <t>_no_delete, and <t>_no_replace on all 30 tables (the key is the SQLite
-- table's own key; organization_id and workspace are always added), and two needs_activation triggers. Every table
-- also refuses TRUNCATE, which SQLite does not have.
DO $$
DECLARE
    keys jsonb := '{
        "engine2_meta": ["key"],
        "authorized_sources": ["source_id"],
        "monitoring_plans": ["monitoring_plan_id", "plan_version"],
        "plan_target_entities": ["monitoring_plan_id", "plan_version", "position"],
        "plan_dimensions": ["monitoring_plan_id", "plan_version", "dimension"],
        "plan_references": ["monitoring_plan_id", "plan_version", "reference_id"],
        "plan_authorized_sources": ["monitoring_plan_id", "plan_version", "source_id"],
        "plan_activations": ["activation_id"],
        "observations": ["observation_id"],
        "source_refusals": ["monitoring_plan_id", "plan_version", "item_id"],
        "supersessions": ["superseded_observation_id"],
        "derived_measurements": ["measurement_id"],
        "measurement_inputs": ["measurement_id", "position"],
        "signal_candidates": ["signal_candidate_id"],
        "signal_candidate_observations": ["signal_candidate_id", "position"],
        "signal_candidate_dimensions": ["signal_candidate_id", "dimension"],
        "correspondence_candidates": ["candidate_id"],
        "candidate_signals": ["candidate_id", "position"],
        "reviews": ["review_id"],
        "review_ledger_observations": ["review_id", "position"],
        "review_ledger_supersessions": ["review_id", "position"],
        "review_ledger_measurements": ["review_id", "position"],
        "review_ledger_refusals": ["review_id", "position"],
        "review_findings": ["review_id", "dimension"],
        "review_finding_evidence": ["review_id", "dimension", "position"],
        "review_checks": ["review_id", "check_name"],
        "review_check_evidence": ["review_id", "check_name", "position"],
        "review_check_dimensions": ["review_id", "check_name", "dimension"],
        "review_revalidation_triggers": ["review_id", "position"],
        "audit_events": ["event_id"]
    }';
    t text;
    args text;
BEGIN
    FOR t IN SELECT jsonb_object_keys(keys) LOOP
        EXECUTE format('CREATE TRIGGER %I BEFORE UPDATE ON baec_engine2.%I '
                       'FOR EACH ROW EXECUTE FUNCTION baec_engine2.refuse_change()', t || '_no_update', t);
        EXECUTE format('CREATE TRIGGER %I BEFORE DELETE ON baec_engine2.%I '
                       'FOR EACH ROW EXECUTE FUNCTION baec_engine2.refuse_change()', t || '_no_delete', t);
        EXECUTE format('CREATE TRIGGER %I BEFORE TRUNCATE ON baec_engine2.%I '
                       'FOR EACH STATEMENT EXECUTE FUNCTION baec_engine2.refuse_change()', t || '_no_truncate', t);
        IF t <> 'engine2_meta' THEN
            SELECT string_agg(quote_literal(k), ', ') INTO args FROM jsonb_array_elements_text(keys -> t) AS k;
            EXECUTE format('CREATE TRIGGER %I BEFORE INSERT ON baec_engine2.%I '
                           'FOR EACH ROW EXECUTE FUNCTION baec_engine2.refuse_replace(%s)', t || '_no_replace', t, args);
        END IF;
    END LOOP;
END $$;

-- engine2_meta has no tenant columns; its no_replace compares the key alone.
CREATE FUNCTION baec_engine2.refuse_meta_replace() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    IF EXISTS (SELECT 1 FROM baec_engine2.engine2_meta m WHERE m.key = NEW.key) THEN
        RAISE EXCEPTION '% rows cannot be replaced', TG_TABLE_NAME USING ERRCODE = 'unique_violation';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER engine2_meta_no_replace BEFORE INSERT ON baec_engine2.engine2_meta
    FOR EACH ROW EXECUTE FUNCTION baec_engine2.refuse_meta_replace();

CREATE TRIGGER observations_needs_activation BEFORE INSERT ON baec_engine2.observations
    FOR EACH ROW EXECUTE FUNCTION baec_engine2.require_activation();
CREATE TRIGGER source_refusals_needs_activation BEFORE INSERT ON baec_engine2.source_refusals
    FOR EACH ROW EXECUTE FUNCTION baec_engine2.require_activation();

-- Row-level security and grants, as 0002 does for product tables, plus the workspace scope.
DO $$
DECLARE
    t text;
BEGIN
    FOR t IN SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
             WHERE n.nspname = 'baec_engine2' AND c.relkind = 'r' AND c.relname <> 'engine2_meta' LOOP
        EXECUTE format('ALTER TABLE baec_engine2.%I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('ALTER TABLE baec_engine2.%I FORCE ROW LEVEL SECURITY', t);
        EXECUTE format('CREATE POLICY tenant_isolation ON baec_engine2.%I AS PERMISSIVE FOR ALL '
                       'USING (organization_id = baec.current_organization()) '
                       'WITH CHECK (organization_id = baec.current_organization())', t);
        EXECUTE format('CREATE POLICY tenant_boundary ON baec_engine2.%I AS RESTRICTIVE FOR ALL '
                       'USING (organization_id = baec.current_organization()) '
                       'WITH CHECK (organization_id = baec.current_organization())', t);
        EXECUTE format('CREATE POLICY workspace_scope ON baec_engine2.%I AS RESTRICTIVE FOR ALL '
                       'USING (workspace = baec.current_workspace()) '
                       'WITH CHECK (workspace = baec.current_workspace())', t);
        EXECUTE format('GRANT SELECT, INSERT ON baec_engine2.%I TO baec_app', t);
    END LOOP;
END $$;

GRANT USAGE ON SCHEMA baec_engine2 TO baec_app;
GRANT SELECT ON baec_engine2.engine2_meta TO baec_app;
GRANT EXECUTE ON FUNCTION baec.current_workspace() TO baec_app;
REVOKE ALL ON FUNCTION baec_engine2.refuse_change(), baec_engine2.refuse_replace(), baec_engine2.require_activation(),
    baec_engine2.assign_event_id(), baec_engine2.refuse_meta_replace() FROM PUBLIC;
