-- 0004: Engine 1 (BAEC records, account states, AI provenance, proposal bridge) persistence. Runs as the migration
-- role, which owns every object here.
--
-- The released Engine 1 repository code (baec_app/data/repository.py) runs unchanged in its decisions. It reaches
-- these tables through baec_console/postgres/engine1.py, a narrow sqlite3-style connection over one tenant
-- transaction. This schema reproduces the SQLite schema (baec_app/data/database.py, version 7: 25 tables, 92
-- triggers) invariant by invariant; it is not a syntax translation:
--
--   SQLite (engine1.sqlite3)                       PostgreSQL (schema baec_engine1)
--   one file per workspace                         organization_id + workspace on every row, filled from the tenant
--                                                    transaction; every key and foreign key includes both
--   STRICT column types                            native column types (text, bigint)
--   INTEGER PRIMARY KEY (rowid alias), read back   assigned coalesce(max, 0) + 1 within (organization, workspace)
--     by lastrowid: max(rowid) + 1 per file          under the workspace lock (f9_<table>_assign_id), read back with
--                                                    RETURNING; inside the BEFORE INSERT guards an unassigned id
--                                                    reads as -1, exactly as NEW.<rowid> does in SQLite
--   ORDER BY rowid (insertion order)               seq, an identity column (the adapter maps rowid to seq)
--   BEFORE triggers fire newest first              the same triggers, one PostgreSQL trigger per SQLite trigger, same
--     (measured on 3.50.4)                           message; BEFORE INSERT triggers are named f<k>_<sqlite name>,
--                                                    k = SQLite's firing position, so PostgreSQL's alphabetical order
--                                                    is SQLite's order and the first violated rule's message wins
--   RAISE(ABORT, '<message>')                      RAISE EXCEPTION '<message>' with a class-23 SQLSTATE
--                                                    (restrict_violation, unique_violation, check_violation)
--   a IS b / a IS NOT b                            IS NOT DISTINCT FROM / IS DISTINCT FROM
--   instr, trim(x, chars), GLOB, json_*            strpos, btrim, left/length/regex by code point, IS JSON / json_*
--   julianday(text) in the grant TTL CHECK and     baec_engine1.julianday(text): SQLite's own algorithm (millisecond
--     the consumption window                         resolution, same double arithmetic) for ISO 8601 text; NULL for
--                                                    any other text, which the CHECK and the window then refuse
--   BINARY text ordering                           COLLATE "C" wherever order is compared
--   UNIQUE on free text (two keys)                 a unique index on the text's SHA-256; the no_replace guard still
--                                                    compares the full text (btree entries are limited to ~2.7 kB)
--   BEGIN IMMEDIATE (one writer per file)          baec_engine1.lock_workspace(): a transaction advisory lock per
--                                                    (organization, workspace), taken by the adapter and by every
--                                                    guard that reads other rows
--   (no TRUNCATE)                                  <table>_no_truncate refuses TRUNCATE on every table
--
-- Workspace scope. As for Engine 2 (0003), a restrictive policy limits every row to baec.current_workspace(), so the
-- repository's SQL, which has no organization or workspace filter, sees exactly one SQLite file's worth of rows. Every
-- guard also filters on NEW.organization_id and NEW.workspace explicitly, so another tenant's row is never consulted
-- even by a role that is not subject to the policies.

CREATE SCHEMA baec_engine1;

-- One writer per (organization, workspace): what BEGIN IMMEDIATE is for one SQLite file. Re-entrant within a
-- transaction; released at commit or rollback.
CREATE FUNCTION baec_engine1.lock_workspace(text, text) RETURNS void
    LANGUAGE sql
    AS $$ SELECT pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended('baec_engine1/' || $1 || '/' || $2, 0)) $$;

-- SQLite's julianday() for ISO 8601 text, as SQLite 3.50 computes it (date.c: parseYyyyMmDd, parseHhMmSs,
-- parseTimezone, computeJD): YYYY-MM-DD, then any run of whitespace or T, then optionally HH:MM[:SS[.digits]] with
-- an optional Z or +-HH:MM. Ranges as SQLite's getDigits (month 1..12, day 1..31 without a per-month check, hour
-- 0..24, minute and second 0..59, zone 00:00..14:59). The fraction is accumulated digit by digit, clamped at .999,
-- and the time rounded to the millisecond. Returns the same double SQLite returns (iJD / 86400000.0), or NULL for
-- text outside this form (SQLite accepts further forms the application never writes; see the Phase 3C report).
CREATE FUNCTION baec_engine1.julianday(text) RETURNS double precision
    LANGUAGE plpgsql IMMUTABLE
    AS $$
DECLARE
    m text[];
    y int; mo int; d int; h int := 0; mi int := 0; s double precision := 0;
    frac double precision := 0; scale double precision := 1; i int;
    a int; b int; x1 int; x2 int; tz int := 0;
    jd bigint;
BEGIN
    m := pg_catalog.regexp_match($1, '^([0-9]{4})-([0-9]{2})-([0-9]{2})[[:space:]T]*(?:([0-9]{2}):([0-9]{2})(?::([0-9]{2})(?:\.([0-9]+))?)?[[:space:]]*(?:([Zz])|([+-])([0-9]{2}):([0-9]{2}))?[[:space:]]*)?$');
    IF m IS NULL THEN
        RETURN NULL;
    END IF;
    y := m[1]::int; mo := m[2]::int; d := m[3]::int;
    IF mo < 1 OR mo > 12 OR d < 1 OR d > 31 THEN
        RETURN NULL;
    END IF;
    IF m[4] IS NOT NULL THEN
        h := m[4]::int; mi := m[5]::int;
        IF h > 24 OR mi > 59 THEN
            RETURN NULL;
        END IF;
        IF m[6] IS NOT NULL THEN
            s := m[6]::int;
            IF s > 59 THEN
                RETURN NULL;
            END IF;
            IF m[7] IS NOT NULL THEN
                FOR i IN 1 .. pg_catalog.length(m[7]) LOOP
                    frac := frac * 10 + pg_catalog.substr(m[7], i, 1)::int;
                    scale := scale * 10;
                END LOOP;
                frac := frac / scale;
                IF frac > 0.999 THEN
                    frac := 0.999;
                END IF;
                s := s + frac;
            END IF;
        END IF;
    END IF;
    IF m[9] IS NOT NULL THEN
        IF m[10]::int > 14 OR m[11]::int > 59 THEN
            RETURN NULL;
        END IF;
        tz := m[10]::int * 60 + m[11]::int;
        IF m[9] = '-' THEN
            tz := -tz;
        END IF;
    END IF;
    IF mo <= 2 THEN
        y := y - 1;
        mo := mo + 12;
    END IF;
    a := (y + 4800) / 100;
    b := 38 - a + (a / 4);
    x1 := 36525 * (y + 4716) / 100;
    x2 := 306001 * (mo + 1) / 10000;
    jd := pg_catalog.trunc((x1 + x2 + d + b - 1524.5::double precision) * 86400000)::bigint;
    IF m[4] IS NOT NULL THEN
        jd := jd + h * 3600000 + mi * 60000 + pg_catalog.trunc(s * 1000 + 0.5)::bigint - tz * 60000;
    END IF;
    RETURN jd / 86400000.0::double precision;
END $$;

-- The digest that stands in for free text in a unique index: SHA-256 of the text's UTF-8 bytes. IMMUTABLE because a
-- database's encoding never changes.
CREATE FUNCTION baec_engine1.text_key(text) RETURNS bytea
    LANGUAGE sql IMMUTABLE
    AS $$ SELECT pg_catalog.sha256(pg_catalog.convert_to($1, 'UTF8')) $$;

-- G1 (append-only) and TRUNCATE: '<table> is append-only'.
CREATE FUNCTION baec_engine1.refuse_change() RETURNS trigger
    LANGUAGE plpgsql
    AS $$ BEGIN RAISE EXCEPTION '% is append-only', TG_TABLE_NAME USING ERRCODE = 'restrict_violation'; END $$;

-- G2 (no replacement): refuse an insert that collides with an existing row of this organization's workspace on any
-- of the table's UNIQUE keys. Each argument is one key, its columns separated by commas; a column marked # is a
-- SQLite rowid alias, which reads as -1 while unassigned (NEW.<rowid> in a SQLite BEFORE INSERT trigger).
CREATE FUNCTION baec_engine1.refuse_replace() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
    collisions text := '';
    condition text;
    key_column text;
    found boolean;
    i int;
BEGIN
    PERFORM baec_engine1.lock_workspace(NEW.organization_id, NEW.workspace);
    FOR i IN 0 .. TG_NARGS - 1 LOOP
        condition := 'organization_id = ($1).organization_id AND workspace = ($1).workspace';
        FOREACH key_column IN ARRAY pg_catalog.string_to_array(TG_ARGV[i], ',') LOOP
            IF pg_catalog.left(key_column, 1) = '#' THEN
                condition := condition || pg_catalog.format(' AND %I = COALESCE(($1).%I, -1)',
                                                            pg_catalog.substr(key_column, 2),
                                                            pg_catalog.substr(key_column, 2));
            ELSE
                condition := condition || pg_catalog.format(' AND %I = ($1).%I', key_column, key_column);
            END IF;
        END LOOP;
        collisions := collisions || CASE WHEN i = 0 THEN '' ELSE ' OR ' END
            || pg_catalog.format('EXISTS (SELECT 1 FROM baec_engine1.%I WHERE %s)', TG_TABLE_NAME, condition);
    END LOOP;
    EXECUTE 'SELECT ' || collisions INTO found USING NEW;
    IF found THEN
        RAISE EXCEPTION '% rows cannot be replaced', TG_TABLE_NAME USING ERRCODE = 'unique_violation';
    END IF;
    RETURN NEW;
END $$;

-- SQLite's INTEGER PRIMARY KEY: an omitted id is max(id) + 1 in the file, or 1 when it is empty. TG_ARGV[0] names
-- the column. Fires after every guard (f9_), so the guards see the id unassigned, as in SQLite.
CREATE FUNCTION baec_engine1.assign_rowid() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
    next_id bigint;
BEGIN
    IF pg_catalog.to_jsonb(NEW) -> TG_ARGV[0] = 'null'::jsonb THEN
        PERFORM baec_engine1.lock_workspace(NEW.organization_id, NEW.workspace);
        EXECUTE pg_catalog.format('SELECT COALESCE(max(%I), 0) + 1 FROM baec_engine1.%I '
                                  'WHERE organization_id = $1 AND workspace = $2', TG_ARGV[0], TG_TABLE_NAME)
            INTO next_id USING NEW.organization_id, NEW.workspace;
        NEW := pg_catalog.jsonb_populate_record(NEW, pg_catalog.jsonb_build_object(TG_ARGV[0], next_id));
    END IF;
    RETURN NEW;
END $$;

-- Every table starts with these columns. The defaults come from the tenant transaction; NULL (no tenant set) violates
-- NOT NULL, so nothing can be written outside one.
--   organization_id text NOT NULL DEFAULT baec.current_organization() REFERENCES baec.organizations
--   workspace text NOT NULL DEFAULT baec.current_workspace() CHECK (workspace IN ('local', 'synthetic'))

CREATE TABLE baec_engine1.schema_meta (
    organization_id text NOT NULL DEFAULT baec.current_organization() REFERENCES baec.organizations (organization_id),
    workspace text NOT NULL DEFAULT baec.current_workspace() CHECK (workspace IN ('local', 'synthetic')),
    key text NOT NULL,
    value text NOT NULL,
    seq bigint GENERATED ALWAYS AS IDENTITY UNIQUE,
    PRIMARY KEY (organization_id, workspace, key)
);

CREATE TABLE baec_engine1.accounts (
    organization_id text NOT NULL DEFAULT baec.current_organization() REFERENCES baec.organizations (organization_id),
    workspace text NOT NULL DEFAULT baec.current_workspace() CHECK (workspace IN ('local', 'synthetic')),
    account_id text NOT NULL,
    name text NOT NULL,
    state text CHECK (state IN ('ACTIVE_OPPORTUNITY', 'CONDITIONALLY_DORMANT', 'NO_PLAUSIBLE_PATH')),
    seq bigint GENERATED ALWAYS AS IDENTITY UNIQUE,
    PRIMARY KEY (organization_id, workspace, account_id)
);

CREATE TABLE baec_engine1.interactions (
    organization_id text NOT NULL DEFAULT baec.current_organization() REFERENCES baec.organizations (organization_id),
    workspace text NOT NULL DEFAULT baec.current_workspace() CHECK (workspace IN ('local', 'synthetic')),
    interaction_id text NOT NULL,
    account_id text NOT NULL,
    occurred_at text NOT NULL,
    text text NOT NULL,
    seq bigint GENERATED ALWAYS AS IDENTITY UNIQUE,
    PRIMARY KEY (organization_id, workspace, interaction_id),
    UNIQUE (organization_id, workspace, interaction_id, account_id),
    FOREIGN KEY (organization_id, workspace, account_id)
        REFERENCES baec_engine1.accounts (organization_id, workspace, account_id) ON DELETE RESTRICT
);

CREATE TABLE baec_engine1.interaction_evidence (
    organization_id text NOT NULL DEFAULT baec.current_organization() REFERENCES baec.organizations (organization_id),
    workspace text NOT NULL DEFAULT baec.current_workspace() CHECK (workspace IN ('local', 'synthetic')),
    evidence_id bigint NOT NULL,
    interaction_id text NOT NULL,
    provenance text NOT NULL CHECK (provenance IN ('BUYER_FACT', 'SELLER_OBSERVATION')),
    text text NOT NULL,
    seq bigint GENERATED ALWAYS AS IDENTITY UNIQUE,
    PRIMARY KEY (organization_id, workspace, evidence_id),
    UNIQUE (organization_id, workspace, evidence_id, interaction_id),
    FOREIGN KEY (organization_id, workspace, interaction_id)
        REFERENCES baec_engine1.interactions (organization_id, workspace, interaction_id) ON DELETE RESTRICT
);
-- UNIQUE (interaction_id, provenance, text), on the text's digest (see the header).
CREATE UNIQUE INDEX interaction_evidence_text_key ON baec_engine1.interaction_evidence
    (organization_id, workspace, interaction_id, provenance, baec_engine1.text_key(text));

CREATE TABLE baec_engine1.human_authorizations (
    organization_id text NOT NULL DEFAULT baec.current_organization() REFERENCES baec.organizations (organization_id),
    workspace text NOT NULL DEFAULT baec.current_workspace() CHECK (workspace IN ('local', 'synthetic')),
    authorization_id bigint NOT NULL,
    authorized_by text NOT NULL,
    authorized_at text NOT NULL,
    action text NOT NULL CHECK (action IN ('CONFIRM_BAEC', 'RECORD_DORMANCY_JUDGMENT', 'CHANGE_ACCOUNT_STATE')),
    subject_id text NOT NULL,
    target_state text CHECK (target_state IN ('ACTIVE_OPPORTUNITY', 'CONDITIONALLY_DORMANT', 'NO_PLAUSIBLE_PATH')),
    seq bigint GENERATED ALWAYS AS IDENTITY UNIQUE,
    PRIMARY KEY (organization_id, workspace, authorization_id),
    CHECK ((action = 'CHANGE_ACCOUNT_STATE') = (target_state IS NOT NULL))
);

CREATE TABLE baec_engine1.baec_records (
    organization_id text NOT NULL DEFAULT baec.current_organization() REFERENCES baec.organizations (organization_id),
    workspace text NOT NULL DEFAULT baec.current_workspace() CHECK (workspace IN ('local', 'synthetic')),
    baec_id text NOT NULL,
    account_id text NOT NULL,
    source_interaction_id text NOT NULL,
    source_excerpt_id bigint NOT NULL,
    captured_at text NOT NULL,
    buyer_role text,
    buyer_exact_statement text,
    articulation_origin text NOT NULL CHECK (articulation_origin IN ('BUYER_GENERATED', 'SELLER_SEEDED', 'UNCERTAIN')),
    elicitation_mode text NOT NULL CHECK (elicitation_mode IN ('SPONTANEOUS', 'CEE_ELICITED', 'UNKNOWN')),
    classification text NOT NULL CHECK (classification IN ('CONFIRMED_BAEC', 'NOT_BAEC', 'INSUFFICIENT_EVIDENCE')),
    classification_reason text,
    staleness_status text CHECK (staleness_status IN ('CURRENT', 'REVIEW_DUE', 'STALE', 'RETIRED')),
    confirmation_authorization_id bigint,
    normalized_text text,
    normalized_generated_at text,
    normalized_model text,
    seq bigint GENERATED ALWAYS AS IDENTITY UNIQUE,
    PRIMARY KEY (organization_id, workspace, baec_id),
    UNIQUE (organization_id, workspace, confirmation_authorization_id),
    UNIQUE (organization_id, workspace, baec_id, source_interaction_id),
    UNIQUE (organization_id, workspace, baec_id, account_id),
    FOREIGN KEY (organization_id, workspace, confirmation_authorization_id)
        REFERENCES baec_engine1.human_authorizations (organization_id, workspace, authorization_id) ON DELETE RESTRICT,
    FOREIGN KEY (organization_id, workspace, source_interaction_id, account_id)
        REFERENCES baec_engine1.interactions (organization_id, workspace, interaction_id, account_id)
        ON DELETE RESTRICT,
    FOREIGN KEY (organization_id, workspace, source_excerpt_id, source_interaction_id)
        REFERENCES baec_engine1.interaction_evidence (organization_id, workspace, evidence_id, interaction_id)
        ON DELETE RESTRICT,
    CHECK (
        (normalized_text IS NULL AND normalized_generated_at IS NULL AND normalized_model IS NULL)
        OR (normalized_text IS NOT NULL AND normalized_generated_at IS NOT NULL)
    )
);

CREATE TABLE baec_engine1.criterion_assessments (
    organization_id text NOT NULL DEFAULT baec.current_organization() REFERENCES baec.organizations (organization_id),
    workspace text NOT NULL DEFAULT baec.current_workspace() CHECK (workspace IN ('local', 'synthetic')),
    baec_id text NOT NULL,
    criterion text NOT NULL CHECK (criterion IN ('PRESENT_NON_EVALUATION', 'PROSPECTIVE_CONDITION',
                                                 'BUYER_ARTICULATION', 'EVALUATION_LINKAGE')),
    position bigint NOT NULL CHECK (position >= 0),
    finding text NOT NULL CHECK (finding IN ('MET', 'NOT_MET', 'UNKNOWN')),
    rationale text,
    seq bigint GENERATED ALWAYS AS IDENTITY UNIQUE,
    PRIMARY KEY (organization_id, workspace, baec_id, criterion),
    UNIQUE (organization_id, workspace, baec_id, position),
    FOREIGN KEY (organization_id, workspace, baec_id)
        REFERENCES baec_engine1.baec_records (organization_id, workspace, baec_id) ON DELETE RESTRICT
);

CREATE TABLE baec_engine1.criterion_evidence (
    organization_id text NOT NULL DEFAULT baec.current_organization() REFERENCES baec.organizations (organization_id),
    workspace text NOT NULL DEFAULT baec.current_workspace() CHECK (workspace IN ('local', 'synthetic')),
    baec_id text NOT NULL,
    criterion text NOT NULL,
    position bigint NOT NULL CHECK (position >= 0),
    evidence_id bigint NOT NULL,
    source_interaction_id text NOT NULL,
    seq bigint GENERATED ALWAYS AS IDENTITY UNIQUE,
    PRIMARY KEY (organization_id, workspace, baec_id, criterion, position),
    FOREIGN KEY (organization_id, workspace, baec_id, criterion)
        REFERENCES baec_engine1.criterion_assessments (organization_id, workspace, baec_id, criterion)
        ON DELETE RESTRICT,
    FOREIGN KEY (organization_id, workspace, baec_id, source_interaction_id)
        REFERENCES baec_engine1.baec_records (organization_id, workspace, baec_id, source_interaction_id)
        ON DELETE RESTRICT,
    FOREIGN KEY (organization_id, workspace, evidence_id, source_interaction_id)
        REFERENCES baec_engine1.interaction_evidence (organization_id, workspace, evidence_id, interaction_id)
        ON DELETE RESTRICT
);

CREATE TABLE baec_engine1.stringency_expressions (
    organization_id text NOT NULL DEFAULT baec.current_organization() REFERENCES baec.organizations (organization_id),
    workspace text NOT NULL DEFAULT baec.current_workspace() CHECK (workspace IN ('local', 'synthetic')),
    baec_id text NOT NULL,
    verbatim_text text NOT NULL,
    comparator text CHECK (comparator IN ('GREATER_THAN', 'AT_LEAST', 'LESS_THAN', 'AT_MOST', 'EXACTLY',
                                          'APPROXIMATELY', 'QUALITATIVE_ONLY', 'NONE_STATED')),
    numeric_value text,
    unit text,
    qualitative_term text,
    recurrence_text text,
    timing_text text,
    seq bigint GENERATED ALWAYS AS IDENTITY UNIQUE,
    PRIMARY KEY (organization_id, workspace, baec_id),
    FOREIGN KEY (organization_id, workspace, baec_id)
        REFERENCES baec_engine1.baec_records (organization_id, workspace, baec_id) ON DELETE RESTRICT
);

CREATE TABLE baec_engine1.dormancy_judgments (
    organization_id text NOT NULL DEFAULT baec.current_organization() REFERENCES baec.organizations (organization_id),
    workspace text NOT NULL DEFAULT baec.current_workspace() CHECK (workspace IN ('local', 'synthetic')),
    judgment_id bigint NOT NULL,
    baec_id text NOT NULL,
    plausibility text NOT NULL CHECK (plausibility IN ('YES', 'NO', 'UNKNOWN', 'NOT_YET')),
    addressability text NOT NULL CHECK (addressability IN ('YES', 'NO', 'UNKNOWN', 'NOT_YET')),
    notes text,
    authorization_id bigint NOT NULL,
    seq bigint GENERATED ALWAYS AS IDENTITY UNIQUE,
    PRIMARY KEY (organization_id, workspace, judgment_id),
    UNIQUE (organization_id, workspace, authorization_id),
    UNIQUE (organization_id, workspace, judgment_id, baec_id),
    FOREIGN KEY (organization_id, workspace, baec_id)
        REFERENCES baec_engine1.baec_records (organization_id, workspace, baec_id) ON DELETE RESTRICT,
    FOREIGN KEY (organization_id, workspace, authorization_id)
        REFERENCES baec_engine1.human_authorizations (organization_id, workspace, authorization_id) ON DELETE RESTRICT
);

CREATE TABLE baec_engine1.evaluation_evidence (
    organization_id text NOT NULL DEFAULT baec.current_organization() REFERENCES baec.organizations (organization_id),
    workspace text NOT NULL DEFAULT baec.current_workspace() CHECK (workspace IN ('local', 'synthetic')),
    evaluation_evidence_id bigint NOT NULL,
    account_id text NOT NULL,
    interaction_id text NOT NULL,
    evidence_id bigint NOT NULL,
    observed_at text NOT NULL,
    seq bigint GENERATED ALWAYS AS IDENTITY UNIQUE,
    PRIMARY KEY (organization_id, workspace, evaluation_evidence_id),
    FOREIGN KEY (organization_id, workspace, interaction_id, account_id)
        REFERENCES baec_engine1.interactions (organization_id, workspace, interaction_id, account_id)
        ON DELETE RESTRICT,
    FOREIGN KEY (organization_id, workspace, evidence_id, interaction_id)
        REFERENCES baec_engine1.interaction_evidence (organization_id, workspace, evidence_id, interaction_id)
        ON DELETE RESTRICT
);

CREATE TABLE baec_engine1.non_evaluation_evidence (
    organization_id text NOT NULL DEFAULT baec.current_organization() REFERENCES baec.organizations (organization_id),
    workspace text NOT NULL DEFAULT baec.current_workspace() CHECK (workspace IN ('local', 'synthetic')),
    non_evaluation_evidence_id bigint NOT NULL,
    account_id text NOT NULL,
    interaction_id text NOT NULL,
    evidence_id bigint NOT NULL,
    observed_at text NOT NULL,
    seq bigint GENERATED ALWAYS AS IDENTITY UNIQUE,
    PRIMARY KEY (organization_id, workspace, non_evaluation_evidence_id),
    FOREIGN KEY (organization_id, workspace, interaction_id, account_id)
        REFERENCES baec_engine1.interactions (organization_id, workspace, interaction_id, account_id)
        ON DELETE RESTRICT,
    FOREIGN KEY (organization_id, workspace, evidence_id, interaction_id)
        REFERENCES baec_engine1.interaction_evidence (organization_id, workspace, evidence_id, interaction_id)
        ON DELETE RESTRICT
);

CREATE TABLE baec_engine1.account_state_transitions (
    organization_id text NOT NULL DEFAULT baec.current_organization() REFERENCES baec.organizations (organization_id),
    workspace text NOT NULL DEFAULT baec.current_workspace() CHECK (workspace IN ('local', 'synthetic')),
    transition_id bigint NOT NULL,
    account_id text NOT NULL,
    from_state text CHECK (from_state IN ('ACTIVE_OPPORTUNITY', 'CONDITIONALLY_DORMANT', 'NO_PLAUSIBLE_PATH')),
    to_state text NOT NULL CHECK (to_state IN ('ACTIVE_OPPORTUNITY', 'CONDITIONALLY_DORMANT', 'NO_PLAUSIBLE_PATH')),
    authorization_id bigint NOT NULL,
    baec_id text,
    judgment_id bigint,
    evaluation_evidence_id bigint,
    non_evaluation_evidence_id bigint,
    ground text CHECK (ground IN ('NO_PLAUSIBLE_BAEC', 'CONDITION_TOO_IMPLAUSIBLE', 'CLEARLY_SELLER_UNADDRESSABLE',
                                  'OTHER')),
    reason text,
    basis_interaction_id text,
    unresolved text NOT NULL,
    recorded_at text NOT NULL,
    seq bigint GENERATED ALWAYS AS IDENTITY UNIQUE,
    PRIMARY KEY (organization_id, workspace, transition_id),
    UNIQUE (organization_id, workspace, authorization_id),
    FOREIGN KEY (organization_id, workspace, account_id)
        REFERENCES baec_engine1.accounts (organization_id, workspace, account_id) ON DELETE RESTRICT,
    FOREIGN KEY (organization_id, workspace, authorization_id)
        REFERENCES baec_engine1.human_authorizations (organization_id, workspace, authorization_id) ON DELETE RESTRICT,
    FOREIGN KEY (organization_id, workspace, baec_id)
        REFERENCES baec_engine1.baec_records (organization_id, workspace, baec_id) ON DELETE RESTRICT,
    FOREIGN KEY (organization_id, workspace, judgment_id)
        REFERENCES baec_engine1.dormancy_judgments (organization_id, workspace, judgment_id) ON DELETE RESTRICT,
    FOREIGN KEY (organization_id, workspace, evaluation_evidence_id)
        REFERENCES baec_engine1.evaluation_evidence (organization_id, workspace, evaluation_evidence_id)
        ON DELETE RESTRICT,
    FOREIGN KEY (organization_id, workspace, non_evaluation_evidence_id)
        REFERENCES baec_engine1.non_evaluation_evidence (organization_id, workspace, non_evaluation_evidence_id)
        ON DELETE RESTRICT,
    FOREIGN KEY (organization_id, workspace, basis_interaction_id, account_id)
        REFERENCES baec_engine1.interactions (organization_id, workspace, interaction_id, account_id)
        ON DELETE RESTRICT,
    -- A transition's BAEC must belong to the transition's account, and its judgment must be a judgment of that BAEC.
    FOREIGN KEY (organization_id, workspace, baec_id, account_id)
        REFERENCES baec_engine1.baec_records (organization_id, workspace, baec_id, account_id) ON DELETE RESTRICT,
    FOREIGN KEY (organization_id, workspace, judgment_id, baec_id)
        REFERENCES baec_engine1.dormancy_judgments (organization_id, workspace, judgment_id, baec_id)
        ON DELETE RESTRICT
);

-- Phase 6 AI provenance. AI interpretation, never buyer evidence.

CREATE TABLE baec_engine1.ai_runs (
    organization_id text NOT NULL DEFAULT baec.current_organization() REFERENCES baec.organizations (organization_id),
    workspace text NOT NULL DEFAULT baec.current_workspace() CHECK (workspace IN ('local', 'synthetic')),
    ai_run_id text NOT NULL CHECK (ai_run_id <> ''),
    provider text NOT NULL CHECK (provider = 'anthropic'),
    task_type text NOT NULL CHECK (task_type <> ''),
    task_version text NOT NULL CHECK (task_version <> ''),
    account_id text NOT NULL,
    interaction_id text NOT NULL,
    requested_model text NOT NULL CHECK (requested_model <> ''),
    sdk_name text NOT NULL CHECK (sdk_name <> ''),
    sdk_version text NOT NULL CHECK (sdk_version <> ''),
    prompt_version text NOT NULL CHECK (prompt_version <> ''),
    prompt_digest text NOT NULL CHECK (length(prompt_digest) = 64 AND prompt_digest !~ '[^0-9a-f]'),
    input_version text NOT NULL CHECK (input_version <> ''),
    input_digest text NOT NULL CHECK (length(input_digest) = 64 AND input_digest !~ '[^0-9a-f]'),
    output_schema_version text NOT NULL CHECK (output_schema_version <> ''),
    output_schema_digest text NOT NULL CHECK (length(output_schema_digest) = 64
                                              AND output_schema_digest !~ '[^0-9a-f]'),
    canonicalization_version text NOT NULL CHECK (canonicalization_version <> ''),
    request_spec_version text NOT NULL CHECK (request_spec_version <> ''),
    request_digest text NOT NULL CHECK (length(request_digest) = 64 AND request_digest !~ '[^0-9a-f]'),
    validation_version text NOT NULL CHECK (btrim(validation_version, E' \t\n\x0b\f\r') <> ''),
    requested_at text NOT NULL,
    retry_of_ai_run_id text,
    seq bigint GENERATED ALWAYS AS IDENTITY UNIQUE,
    PRIMARY KEY (organization_id, workspace, ai_run_id),
    CHECK (retry_of_ai_run_id IS NULL OR retry_of_ai_run_id <> ai_run_id),
    UNIQUE (organization_id, workspace, ai_run_id, account_id, interaction_id, task_type, task_version,
            output_schema_version),
    FOREIGN KEY (organization_id, workspace, retry_of_ai_run_id)
        REFERENCES baec_engine1.ai_runs (organization_id, workspace, ai_run_id) ON DELETE RESTRICT,
    FOREIGN KEY (organization_id, workspace, interaction_id, account_id)
        REFERENCES baec_engine1.interactions (organization_id, workspace, interaction_id, account_id)
        ON DELETE RESTRICT
);

CREATE TABLE baec_engine1.ai_run_results (
    organization_id text NOT NULL DEFAULT baec.current_organization() REFERENCES baec.organizations (organization_id),
    workspace text NOT NULL DEFAULT baec.current_workspace() CHECK (workspace IN ('local', 'synthetic')),
    ai_run_id text NOT NULL,
    status text NOT NULL CHECK (status IN ('success', 'refusal', 'max_tokens', 'unexpected_stop', 'api_error',
                                           'transport_failure', 'parse_failure', 'semantic_validation_failure',
                                           'model_mismatch', 'interrupted')),
    remote_outcome text NOT NULL CHECK (remote_outcome IN ('response_received', 'not_sent', 'unknown')),
    provider_message_id text CHECK (provider_message_id <> ''),
    response_model text CHECK (response_model <> ''),
    stop_reason text CHECK (stop_reason <> ''),
    provider_request_id text CHECK (provider_request_id <> ''),
    input_tokens bigint CHECK (input_tokens >= 0),
    output_tokens bigint CHECK (output_tokens >= 0),
    cache_creation_input_tokens bigint CHECK (cache_creation_input_tokens >= 0),
    cache_read_input_tokens bigint CHECK (cache_read_input_tokens >= 0),
    failure_category text,
    failure_codes text CHECK (failure_codes <> ''),
    output_digest text CHECK (output_digest IS NULL OR (length(output_digest) = 64 AND output_digest !~ '[^0-9a-f]')),
    completed_at text NOT NULL,
    seq bigint GENERATED ALWAYS AS IDENTITY UNIQUE,
    PRIMARY KEY (organization_id, workspace, ai_run_id),
    FOREIGN KEY (organization_id, workspace, ai_run_id)
        REFERENCES baec_engine1.ai_runs (organization_id, workspace, ai_run_id) ON DELETE RESTRICT,
    -- Delivery state follows from the status.
    CHECK (
        (status IN ('success', 'refusal', 'max_tokens', 'unexpected_stop', 'api_error', 'parse_failure',
                    'semantic_validation_failure', 'model_mismatch') AND remote_outcome = 'response_received')
        OR (status = 'transport_failure' AND remote_outcome IN ('not_sent', 'unknown'))
        OR (status = 'interrupted' AND remote_outcome = 'unknown')
    ),
    -- Returned model text exists only for statuses that can carry it, and must for some.
    CHECK (status IN ('success', 'refusal', 'max_tokens', 'unexpected_stop', 'parse_failure',
                      'semantic_validation_failure', 'model_mismatch') OR output_digest IS NULL),
    CHECK (status NOT IN ('success', 'semantic_validation_failure') OR output_digest IS NOT NULL),
    CHECK (status <> 'success' OR (
        provider_message_id IS NOT NULL AND response_model IS NOT NULL AND stop_reason IS NOT DISTINCT FROM 'end_turn'
    )),
    CHECK (status <> 'refusal' OR stop_reason IS NOT DISTINCT FROM 'refusal'),
    CHECK (status <> 'max_tokens' OR stop_reason IS NOT DISTINCT FROM 'max_tokens'),
    CHECK (status <> 'unexpected_stop' OR stop_reason NOT IN ('end_turn', 'refusal', 'max_tokens')),
    CHECK (status <> 'unexpected_stop' OR stop_reason IS NOT NULL),
    CHECK (status <> 'model_mismatch' OR response_model IS NOT NULL),
    -- No response: no response metadata.
    CHECK (status NOT IN ('transport_failure', 'interrupted') OR (
        provider_message_id IS NULL AND response_model IS NULL AND stop_reason IS NULL
        AND input_tokens IS NULL AND output_tokens IS NULL
        AND cache_creation_input_tokens IS NULL AND cache_read_input_tokens IS NULL
    )),
    -- Failure categories are a fixed vocabulary, used only where the design defines them.
    CHECK (
        (status = 'api_error' AND COALESCE(failure_category IN ('authentication', 'permission', 'rate_limited',
                                                                'overloaded', 'invalid_request', 'server_error',
                                                                'other'), false))
        OR (status = 'transport_failure' AND (
            (failure_category IS NOT DISTINCT FROM 'connection_not_established' AND remote_outcome = 'not_sent')
            OR (failure_category IS NOT DISTINCT FROM 'timeout_or_disconnect' AND remote_outcome = 'unknown')
        ))
        OR (status NOT IN ('api_error', 'transport_failure') AND failure_category IS NULL)
    ),
    -- Failure-code cardinality and vocabulary are also checked by f1_ai_run_results_failure_codes_closed.
    CHECK (status IN ('parse_failure', 'semantic_validation_failure') OR failure_codes IS NULL),
    CHECK (status <> 'semantic_validation_failure' OR failure_codes IS NOT NULL),
    CHECK (status <> 'parse_failure' OR failure_codes IS NOT NULL)
);

CREATE TABLE baec_engine1.ai_run_outputs (
    organization_id text NOT NULL DEFAULT baec.current_organization() REFERENCES baec.organizations (organization_id),
    workspace text NOT NULL DEFAULT baec.current_workspace() CHECK (workspace IN ('local', 'synthetic')),
    ai_run_id text NOT NULL,
    raw_output_text text NOT NULL,
    output_digest text NOT NULL CHECK (length(output_digest) = 64 AND output_digest !~ '[^0-9a-f]'),
    seq bigint GENERATED ALWAYS AS IDENTITY UNIQUE,
    PRIMARY KEY (organization_id, workspace, ai_run_id),
    FOREIGN KEY (organization_id, workspace, ai_run_id)
        REFERENCES baec_engine1.ai_run_results (organization_id, workspace, ai_run_id) ON DELETE RESTRICT
);

CREATE TABLE baec_engine1.ai_artifacts (
    organization_id text NOT NULL DEFAULT baec.current_organization() REFERENCES baec.organizations (organization_id),
    workspace text NOT NULL DEFAULT baec.current_workspace() CHECK (workspace IN ('local', 'synthetic')),
    artifact_id text NOT NULL CHECK (artifact_id <> ''),
    ai_run_id text NOT NULL,
    task_type text NOT NULL,
    task_version text NOT NULL,
    output_schema_version text NOT NULL,
    account_id text NOT NULL,
    interaction_id text NOT NULL,
    canonical_result text NOT NULL CHECK (canonical_result <> ''),
    artifact_digest text NOT NULL CHECK (length(artifact_digest) = 64 AND artifact_digest !~ '[^0-9a-f]'),
    created_at text NOT NULL,
    seq bigint GENERATED ALWAYS AS IDENTITY UNIQUE,
    PRIMARY KEY (organization_id, workspace, artifact_id),
    UNIQUE (organization_id, workspace, ai_run_id),
    UNIQUE (organization_id, workspace, artifact_id, interaction_id),
    FOREIGN KEY (organization_id, workspace, ai_run_id)
        REFERENCES baec_engine1.ai_run_results (organization_id, workspace, ai_run_id) ON DELETE RESTRICT,
    FOREIGN KEY (organization_id, workspace, ai_run_id, account_id, interaction_id, task_type, task_version,
                 output_schema_version)
        REFERENCES baec_engine1.ai_runs (organization_id, workspace, ai_run_id, account_id, interaction_id, task_type,
                                         task_version, output_schema_version)
        ON DELETE RESTRICT
);

CREATE TABLE baec_engine1.ai_artifact_excerpts (
    organization_id text NOT NULL DEFAULT baec.current_organization() REFERENCES baec.organizations (organization_id),
    workspace text NOT NULL DEFAULT baec.current_workspace() CHECK (workspace IN ('local', 'synthetic')),
    artifact_id text NOT NULL,
    excerpt_id text NOT NULL CHECK (excerpt_id <> ''),
    interaction_id text NOT NULL,
    text text NOT NULL,
    attributed_speaker text NOT NULL CHECK (attributed_speaker IN ('buyer', 'seller', 'unclear')),
    seq bigint GENERATED ALWAYS AS IDENTITY UNIQUE,
    PRIMARY KEY (organization_id, workspace, artifact_id, excerpt_id),
    FOREIGN KEY (organization_id, workspace, artifact_id, interaction_id)
        REFERENCES baec_engine1.ai_artifacts (organization_id, workspace, artifact_id, interaction_id)
        ON DELETE RESTRICT
);
-- UNIQUE (artifact_id, interaction_id, text), on the text's digest (see the header).
CREATE UNIQUE INDEX ai_artifact_excerpts_text_key ON baec_engine1.ai_artifact_excerpts
    (organization_id, workspace, artifact_id, interaction_id, baec_engine1.text_key(text));

-- Phase 7 AI proposal bridge. Every table is append-only; a grant's status is derived from the rows that reference
-- it; no trigger or index reads the current time.

CREATE TABLE baec_engine1.ai_proposals (
    organization_id text NOT NULL DEFAULT baec.current_organization() REFERENCES baec.organizations (organization_id),
    workspace text NOT NULL DEFAULT baec.current_workspace() CHECK (workspace IN ('local', 'synthetic')),
    proposal_id text NOT NULL CHECK (left(proposal_id, 7) = 'aiprop_' AND length(proposal_id) > 7),
    origin text NOT NULL CHECK (origin = 'AI_DRAFT'),
    mapping_version text NOT NULL CHECK (mapping_version = 'baec-ai-proposal-mapping/v1'),
    content_version text NOT NULL CHECK (content_version = 'baec-ai-proposal-content/v1'),
    artifact_id text NOT NULL,
    artifact_digest text NOT NULL CHECK (length(artifact_digest) = 64 AND artifact_digest !~ '[^0-9a-f]'),
    account_id text NOT NULL,
    interaction_id text NOT NULL,
    content text NOT NULL CHECK (content IS JSON OBJECT),
    proposal_digest text NOT NULL CHECK (length(proposal_digest) = 64 AND proposal_digest !~ '[^0-9a-f]'),
    created_by text NOT NULL CHECK (btrim(created_by, E' \t\n\x0b\f\r') <> ''),
    created_at text NOT NULL,
    seq bigint GENERATED ALWAYS AS IDENTITY UNIQUE,
    PRIMARY KEY (organization_id, workspace, proposal_id),
    UNIQUE (organization_id, workspace, artifact_id, mapping_version),
    UNIQUE (organization_id, workspace, proposal_id, account_id, interaction_id, artifact_id, proposal_digest),
    FOREIGN KEY (organization_id, workspace, artifact_id, interaction_id)
        REFERENCES baec_engine1.ai_artifacts (organization_id, workspace, artifact_id, interaction_id)
        ON DELETE RESTRICT,
    FOREIGN KEY (organization_id, workspace, interaction_id, account_id)
        REFERENCES baec_engine1.interactions (organization_id, workspace, interaction_id, account_id)
        ON DELETE RESTRICT
);
CREATE INDEX ai_proposals_account ON baec_engine1.ai_proposals (organization_id, workspace, account_id);

CREATE TABLE baec_engine1.ai_proposal_review_revisions (
    organization_id text NOT NULL DEFAULT baec.current_organization() REFERENCES baec.organizations (organization_id),
    workspace text NOT NULL DEFAULT baec.current_workspace() CHECK (workspace IN ('local', 'synthetic')),
    review_revision_id text NOT NULL CHECK (left(review_revision_id, 9) = 'aireview_'
                                            AND length(review_revision_id) > 9),
    proposal_id text NOT NULL,
    proposal_digest text NOT NULL,
    account_id text NOT NULL,
    interaction_id text NOT NULL,
    artifact_id text NOT NULL,
    revision_number bigint NOT NULL CHECK (revision_number >= 1),
    previous_revision_id text,
    review_content_version text NOT NULL CHECK (review_content_version = 'baec-ai-review-content/v1'),
    normalization_validation_version text NOT NULL
        CHECK (normalization_validation_version = 'baec-human-normalization-validation/v1'),
    content text NOT NULL CHECK (content IS JSON OBJECT),
    review_content_digest text NOT NULL CHECK (length(review_content_digest) = 64
                                               AND review_content_digest !~ '[^0-9a-f]'),
    actor_label text NOT NULL CHECK (btrim(actor_label, E' \t\n\x0b\f\r') <> ''),
    created_at text NOT NULL,
    seq bigint GENERATED ALWAYS AS IDENTITY UNIQUE,
    PRIMARY KEY (organization_id, workspace, review_revision_id),
    CHECK ((revision_number = 1) = (previous_revision_id IS NULL)),
    UNIQUE (organization_id, workspace, proposal_id, revision_number),
    UNIQUE (organization_id, workspace, review_revision_id, proposal_id, account_id, interaction_id, artifact_id,
            review_content_digest),
    FOREIGN KEY (organization_id, workspace, previous_revision_id)
        REFERENCES baec_engine1.ai_proposal_review_revisions (organization_id, workspace, review_revision_id)
        ON DELETE RESTRICT,
    FOREIGN KEY (organization_id, workspace, proposal_id, account_id, interaction_id, artifact_id, proposal_digest)
        REFERENCES baec_engine1.ai_proposals (organization_id, workspace, proposal_id, account_id, interaction_id,
                                              artifact_id, proposal_digest)
        ON DELETE RESTRICT
);

CREATE TABLE baec_engine1.ai_proposal_review_decisions (
    organization_id text NOT NULL DEFAULT baec.current_organization() REFERENCES baec.organizations (organization_id),
    workspace text NOT NULL DEFAULT baec.current_workspace() CHECK (workspace IN ('local', 'synthetic')),
    decision_id text NOT NULL CHECK (left(decision_id, 11) = 'aidecision_' AND length(decision_id) > 11),
    proposal_id text NOT NULL,
    review_revision_id text,
    account_id text NOT NULL,
    decision text NOT NULL CHECK (decision IN ('ACCEPTED', 'REJECTED')),
    actor_label text NOT NULL CHECK (btrim(actor_label, E' \t\n\x0b\f\r') <> ''),
    decided_at text NOT NULL,
    seq bigint GENERATED ALWAYS AS IDENTITY UNIQUE,
    PRIMARY KEY (organization_id, workspace, decision_id),
    -- Acceptance is per revision; a rejection is per proposal and may name no revision.
    CHECK (decision <> 'ACCEPTED' OR review_revision_id IS NOT NULL),
    UNIQUE (organization_id, workspace, review_revision_id),
    UNIQUE (organization_id, workspace, decision_id, review_revision_id),
    FOREIGN KEY (organization_id, workspace, proposal_id)
        REFERENCES baec_engine1.ai_proposals (organization_id, workspace, proposal_id) ON DELETE RESTRICT,
    FOREIGN KEY (organization_id, workspace, review_revision_id)
        REFERENCES baec_engine1.ai_proposal_review_revisions (organization_id, workspace, review_revision_id)
        ON DELETE RESTRICT
);
-- A rejection is terminal and happens at most once per proposal.
CREATE UNIQUE INDEX ai_proposal_review_decisions_one_rejection ON baec_engine1.ai_proposal_review_decisions
    (organization_id, workspace, proposal_id) WHERE decision = 'REJECTED';

CREATE TABLE baec_engine1.human_authorization_grants (
    organization_id text NOT NULL DEFAULT baec.current_organization() REFERENCES baec.organizations (organization_id),
    workspace text NOT NULL DEFAULT baec.current_workspace() CHECK (workspace IN ('local', 'synthetic')),
    grant_id text NOT NULL CHECK (left(grant_id, 6) = 'grant_' AND length(grant_id) = 70
                                  AND substr(grant_id, 7) !~ '[^0-9a-f]'),
    grant_format text NOT NULL CHECK (grant_format = 'baec-human-grant/v1'),
    action text NOT NULL CHECK (action = 'CONFIRM_BAEC'),
    issuing_surface text NOT NULL CHECK (issuing_surface = 'streamlit-review/v1'),
    account_id text NOT NULL,
    interaction_id text NOT NULL,
    artifact_id text NOT NULL,
    proposal_id text NOT NULL,
    proposal_digest text NOT NULL,
    review_revision_id text NOT NULL,
    review_content_digest text NOT NULL,
    decision_id text NOT NULL,
    issue_sequence bigint NOT NULL CHECK (issue_sequence >= 1),
    supersedes_grant_id text,
    baec_id text NOT NULL CHECK (left(baec_id, 5) = 'BAEC-' AND length(baec_id) > 5),
    actor_label text NOT NULL CHECK (btrim(actor_label, E' \t\n\x0b\f\r') <> ''),
    issued_at text NOT NULL,
    ttl_seconds bigint NOT NULL CHECK (ttl_seconds = 900),
    expires_at text NOT NULL,
    grant_digest text NOT NULL CHECK (length(grant_digest) = 64 AND grant_digest !~ '[^0-9a-f]'),
    seq bigint GENERATED ALWAYS AS IDENTITY UNIQUE,
    PRIMARY KEY (organization_id, workspace, grant_id),
    UNIQUE (organization_id, workspace, supersedes_grant_id),
    UNIQUE (organization_id, workspace, baec_id),
    CHECK ((issue_sequence = 1) = (supersedes_grant_id IS NULL)),
    -- Defense in depth; the application verifies the exact 15-minute equality. SQLite's julianday arithmetic.
    CHECK (COALESCE(abs((baec_engine1.julianday(expires_at) - baec_engine1.julianday(issued_at)) * 86400.0
                        - 900.0) < 0.001, false)),
    UNIQUE (organization_id, workspace, review_revision_id, action, issue_sequence),
    UNIQUE (organization_id, workspace, grant_id, baec_id, account_id, interaction_id, artifact_id, proposal_id,
            review_revision_id),
    FOREIGN KEY (organization_id, workspace, supersedes_grant_id)
        REFERENCES baec_engine1.human_authorization_grants (organization_id, workspace, grant_id) ON DELETE RESTRICT,
    FOREIGN KEY (organization_id, workspace, review_revision_id, proposal_id, account_id, interaction_id,
                 artifact_id, review_content_digest)
        REFERENCES baec_engine1.ai_proposal_review_revisions (organization_id, workspace, review_revision_id,
                                                              proposal_id, account_id, interaction_id, artifact_id,
                                                              review_content_digest)
        ON DELETE RESTRICT,
    FOREIGN KEY (organization_id, workspace, decision_id, review_revision_id)
        REFERENCES baec_engine1.ai_proposal_review_decisions (organization_id, workspace, decision_id,
                                                              review_revision_id)
        ON DELETE RESTRICT
);
CREATE INDEX human_authorization_grants_proposal ON baec_engine1.human_authorization_grants
    (organization_id, workspace, proposal_id);

CREATE TABLE baec_engine1.human_authorization_grant_supersessions (
    organization_id text NOT NULL DEFAULT baec.current_organization() REFERENCES baec.organizations (organization_id),
    workspace text NOT NULL DEFAULT baec.current_workspace() CHECK (workspace IN ('local', 'synthetic')),
    grant_id text NOT NULL,
    reason text NOT NULL CHECK (reason IN ('REVISION_SUPERSEDED', 'PROPOSAL_REJECTED')),
    superseding_revision_id text,
    rejection_decision_id text,
    actor_label text NOT NULL CHECK (btrim(actor_label, E' \t\n\x0b\f\r') <> ''),
    superseded_at text NOT NULL,
    seq bigint GENERATED ALWAYS AS IDENTITY UNIQUE,
    PRIMARY KEY (organization_id, workspace, grant_id),
    CHECK ((reason = 'REVISION_SUPERSEDED') = (superseding_revision_id IS NOT NULL)),
    CHECK ((reason = 'PROPOSAL_REJECTED') = (rejection_decision_id IS NOT NULL)),
    FOREIGN KEY (organization_id, workspace, grant_id)
        REFERENCES baec_engine1.human_authorization_grants (organization_id, workspace, grant_id) ON DELETE RESTRICT,
    FOREIGN KEY (organization_id, workspace, superseding_revision_id)
        REFERENCES baec_engine1.ai_proposal_review_revisions (organization_id, workspace, review_revision_id)
        ON DELETE RESTRICT,
    FOREIGN KEY (organization_id, workspace, rejection_decision_id)
        REFERENCES baec_engine1.ai_proposal_review_decisions (organization_id, workspace, decision_id)
        ON DELETE RESTRICT
);

CREATE TABLE baec_engine1.ai_proposal_confirmations (
    organization_id text NOT NULL DEFAULT baec.current_organization() REFERENCES baec.organizations (organization_id),
    workspace text NOT NULL DEFAULT baec.current_workspace() CHECK (workspace IN ('local', 'synthetic')),
    baec_id text NOT NULL,
    -- One confirmed BAEC per AI lineage (Phase 7 design D8).
    artifact_id text NOT NULL,
    proposal_id text NOT NULL,
    review_revision_id text NOT NULL,
    grant_id text NOT NULL,
    authorization_id bigint NOT NULL,
    account_id text NOT NULL,
    interaction_id text NOT NULL,
    seq bigint GENERATED ALWAYS AS IDENTITY UNIQUE,
    PRIMARY KEY (organization_id, workspace, baec_id),
    UNIQUE (organization_id, workspace, artifact_id),
    UNIQUE (organization_id, workspace, proposal_id),
    UNIQUE (organization_id, workspace, review_revision_id),
    UNIQUE (organization_id, workspace, grant_id),
    UNIQUE (organization_id, workspace, authorization_id),
    FOREIGN KEY (organization_id, workspace, authorization_id)
        REFERENCES baec_engine1.human_authorizations (organization_id, workspace, authorization_id) ON DELETE RESTRICT,
    FOREIGN KEY (organization_id, workspace, baec_id, account_id)
        REFERENCES baec_engine1.baec_records (organization_id, workspace, baec_id, account_id) ON DELETE RESTRICT,
    FOREIGN KEY (organization_id, workspace, baec_id, interaction_id)
        REFERENCES baec_engine1.baec_records (organization_id, workspace, baec_id, source_interaction_id)
        ON DELETE RESTRICT,
    FOREIGN KEY (organization_id, workspace, grant_id, baec_id, account_id, interaction_id, artifact_id, proposal_id,
                 review_revision_id)
        REFERENCES baec_engine1.human_authorization_grants (organization_id, workspace, grant_id, baec_id, account_id,
                                                            interaction_id, artifact_id, proposal_id,
                                                            review_revision_id)
        ON DELETE RESTRICT
);
CREATE INDEX ai_proposal_confirmations_account ON baec_engine1.ai_proposal_confirmations
    (organization_id, workspace, account_id);

CREATE TABLE baec_engine1.human_authorization_grant_consumptions (
    organization_id text NOT NULL DEFAULT baec.current_organization() REFERENCES baec.organizations (organization_id),
    workspace text NOT NULL DEFAULT baec.current_workspace() CHECK (workspace IN ('local', 'synthetic')),
    grant_id text NOT NULL,
    baec_id text NOT NULL,
    executor_surface text NOT NULL CHECK (executor_surface = 'mcp-write/confirm_baec/v1'),
    consumed_at text NOT NULL,
    seq bigint GENERATED ALWAYS AS IDENTITY UNIQUE,
    PRIMARY KEY (organization_id, workspace, grant_id),
    UNIQUE (organization_id, workspace, baec_id),
    FOREIGN KEY (organization_id, workspace, baec_id)
        REFERENCES baec_engine1.ai_proposal_confirmations (organization_id, workspace, baec_id) ON DELETE RESTRICT,
    FOREIGN KEY (organization_id, workspace, grant_id)
        REFERENCES baec_engine1.ai_proposal_confirmations (organization_id, workspace, grant_id) ON DELETE RESTRICT
);

-- --- Guards: one function per SQLite trigger that is not G1 or a plain G2 (same message, same predicate) ----------

-- G3. Fires when a protected column (or a tenancy column) is named in SET, as SQLite's BEFORE UPDATE OF does.
CREATE FUNCTION baec_engine1.baec_records_protected_no_update() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    RAISE EXCEPTION 'baec_records source and decision columns are immutable' USING ERRCODE = 'restrict_violation';
END $$;

CREATE FUNCTION baec_engine1.baec_records_no_delete() RETURNS trigger
    LANGUAGE plpgsql
    AS $$ BEGIN RAISE EXCEPTION 'baec_records rows cannot be deleted' USING ERRCODE = 'restrict_violation'; END $$;

-- G4. Evidence fidelity: non-blank, exact, case-sensitive substring of the cited interaction (missing: refused).
CREATE FUNCTION baec_engine1.interaction_evidence_verbatim() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    PERFORM baec_engine1.lock_workspace(NEW.organization_id, NEW.workspace);
    IF NEW.text = ''
        OR NOT COALESCE(
            pg_catalog.strpos((SELECT i.text FROM baec_engine1.interactions i
                               WHERE i.organization_id = NEW.organization_id AND i.workspace = NEW.workspace
                                 AND i.interaction_id = NEW.interaction_id), NEW.text) > 0,
            false) THEN
        RAISE EXCEPTION 'interaction_evidence text must occur verbatim in its interaction'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;

CREATE FUNCTION baec_engine1.ai_artifact_excerpts_verbatim() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    PERFORM baec_engine1.lock_workspace(NEW.organization_id, NEW.workspace);
    IF pg_catalog.btrim(NEW.text, E' \t\n\x0b\f\r') = ''
        OR NOT COALESCE(
            pg_catalog.strpos((SELECT i.text FROM baec_engine1.interactions i
                               WHERE i.organization_id = NEW.organization_id AND i.workspace = NEW.workspace
                                 AND i.interaction_id = NEW.interaction_id), NEW.text) > 0,
            false) THEN
        RAISE EXCEPTION 'ai_artifact_excerpts text must occur verbatim in its interaction'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;

-- G5. AI run provenance chain and closed vocabularies.
CREATE FUNCTION baec_engine1.ai_run_results_model_binding() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    PERFORM baec_engine1.lock_workspace(NEW.organization_id, NEW.workspace);
    IF (NEW.status = 'success'
        AND NEW.response_model IS DISTINCT FROM (SELECT r.requested_model FROM baec_engine1.ai_runs r
                                                 WHERE r.organization_id = NEW.organization_id
                                                   AND r.workspace = NEW.workspace AND r.ai_run_id = NEW.ai_run_id))
        OR (NEW.status = 'model_mismatch'
        AND NEW.response_model IS NOT DISTINCT FROM (SELECT r.requested_model FROM baec_engine1.ai_runs r
                                                     WHERE r.organization_id = NEW.organization_id
                                                       AND r.workspace = NEW.workspace
                                                       AND r.ai_run_id = NEW.ai_run_id)) THEN
        RAISE EXCEPTION 'ai_run_results response model disagrees with its status' USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;

-- Closed failure codes (Phase 6D §5.1): exactly one parse code under parse_failure; one or more codes from the run's
-- validator-version vocabulary under semantic_validation_failure, strictly increasing in binary order; NULL under
-- every other status. SQLite's CASE keeps its JSON functions away from text that is not a JSON array. In PL/pgSQL
-- that takes IF statements: NEW's fields are plan-time parameters, so a cast to json inside a CASE branch that is
-- never taken can still be evaluated (and fail) when the expression is planned. A JSON \u0000 escape is refused here
-- (it is in no vocabulary, so SQLite refuses it too); PostgreSQL could not decode it to text.
CREATE FUNCTION baec_engine1.ai_run_results_failure_codes_closed() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
    codes json;
    run_version text;
    ok boolean;
BEGIN
    PERFORM baec_engine1.lock_workspace(NEW.organization_id, NEW.workspace);
    IF NEW.status = 'parse_failure' THEN
        IF NEW.failure_codes IS JSON ARRAY AND pg_catalog.strpos(NEW.failure_codes, '\u0000') = 0 THEN
            codes := NEW.failure_codes::json;
            ok := pg_catalog.json_array_length(codes) = 1
                AND pg_catalog.json_typeof(codes -> 0) = 'string'
                AND (codes ->> 0) IN ('invalid_json', 'missing_stop_reason', 'missing_text_block',
                                      'multiple_text_blocks', 'structured_output_validation_failed');
        ELSE
            ok := false;
        END IF;
    ELSIF NEW.status = 'semantic_validation_failure' THEN
        IF NEW.failure_codes IS JSON ARRAY AND pg_catalog.strpos(NEW.failure_codes, '\u0000') = 0 THEN
            codes := NEW.failure_codes::json;
            run_version := (SELECT r.validation_version FROM baec_engine1.ai_runs r
                            WHERE r.organization_id = NEW.organization_id AND r.workspace = NEW.workspace
                              AND r.ai_run_id = NEW.ai_run_id);
            ok := pg_catalog.json_array_length(codes) >= 1
                AND NOT EXISTS (
                    SELECT 1 FROM pg_catalog.json_array_elements(codes) AS code (value)
                    WHERE pg_catalog.json_typeof(code.value) <> 'string' OR NOT COALESCE(
                        (run_version = 'baec-extraction-validation/v1' AND (code.value #>> '{}') IN (
                    'criterion_set_invalid', 'duplicate_excerpt_id', 'duplicate_excerpt_reference',
                    'duplicate_excerpt_text', 'excerpt_blank', 'excerpt_id_blank', 'excerpt_not_verbatim',
                    'explanation_blank', 'explanation_too_long', 'normalization_blank', 'normalization_too_long',
                    'possible_language_without_excerpt', 'source_interaction_mismatch', 'supported_without_excerpt',
                    'too_many_excerpt_references', 'too_many_excerpts', 'too_many_uncertainties', 'uncertainty_blank',
                    'uncertainty_too_long', 'unknown_excerpt_reference'))
                        OR (run_version = 'baec-extraction-validation/v2' AND (code.value #>> '{}') IN (
                    'criterion_set_invalid', 'duplicate_excerpt_id', 'duplicate_excerpt_reference',
                    'duplicate_excerpt_text', 'excerpt_blank', 'excerpt_id_blank', 'excerpt_not_verbatim',
                    'explanation_blank', 'explanation_comparator_changed', 'explanation_comparator_unresolved',
                    'explanation_compound_unsupported', 'explanation_number_unsupported',
                    'explanation_numeric_kind_changed', 'explanation_too_long', 'explanation_unit_changed',
                    'normalization_blank', 'normalization_comparator_changed', 'normalization_comparator_unresolved',
                    'normalization_compound_unsupported', 'normalization_number_unsupported',
                    'normalization_numeric_kind_changed', 'normalization_too_long', 'normalization_unit_changed',
                    'possible_language_without_excerpt', 'source_interaction_mismatch', 'supported_without_excerpt',
                    'too_many_excerpt_references', 'too_many_excerpts', 'too_many_uncertainties', 'uncertainty_blank',
                    'uncertainty_comparator_changed', 'uncertainty_comparator_unresolved',
                    'uncertainty_compound_unsupported', 'uncertainty_number_unsupported',
                    'uncertainty_numeric_kind_changed', 'uncertainty_too_long', 'uncertainty_unit_changed',
                    'unknown_excerpt_reference')), false))
                AND NOT EXISTS (
                    SELECT 1
                    FROM pg_catalog.json_array_elements(codes) WITH ORDINALITY AS earlier (value, key)
                    JOIN pg_catalog.json_array_elements(codes) WITH ORDINALITY AS later (value, key)
                        ON later.key = earlier.key + 1
                    WHERE NOT COALESCE((earlier.value #>> '{}') COLLATE "C" < (later.value #>> '{}') COLLATE "C",
                                       false));
        ELSE
            ok := false;
        END IF;
    ELSE
        ok := NEW.failure_codes IS NULL;
    END IF;
    IF NOT COALESCE(ok, false) THEN
        RAISE EXCEPTION 'ai_run_results failure_codes are outside the closed vocabulary for the status'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;

CREATE FUNCTION baec_engine1.ai_run_outputs_require_result() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    PERFORM baec_engine1.lock_workspace(NEW.organization_id, NEW.workspace);
    IF NOT EXISTS (
        SELECT 1 FROM baec_engine1.ai_run_results r
        WHERE r.organization_id = NEW.organization_id AND r.workspace = NEW.workspace
            AND r.ai_run_id = NEW.ai_run_id AND r.output_digest = NEW.output_digest
    ) THEN
        RAISE EXCEPTION 'ai_run_outputs requires a result that records the same output digest'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;

CREATE FUNCTION baec_engine1.ai_artifacts_require_success() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    PERFORM baec_engine1.lock_workspace(NEW.organization_id, NEW.workspace);
    IF NOT EXISTS (
        SELECT 1 FROM baec_engine1.ai_run_results AS result
        JOIN baec_engine1.ai_run_outputs AS output
            ON output.organization_id = result.organization_id AND output.workspace = result.workspace
            AND output.ai_run_id = result.ai_run_id
        WHERE result.organization_id = NEW.organization_id AND result.workspace = NEW.workspace
            AND result.ai_run_id = NEW.ai_run_id AND result.status = 'success'
            AND output.output_digest = result.output_digest
    ) THEN
        RAISE EXCEPTION 'ai_artifacts requires a successful run with its raw output' USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;

-- G6. Proposal lineage and the review state machine.
CREATE FUNCTION baec_engine1.ai_proposals_artifact_binding() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    PERFORM baec_engine1.lock_workspace(NEW.organization_id, NEW.workspace);
    IF NOT EXISTS (
        SELECT 1 FROM baec_engine1.ai_artifacts AS artifact
        JOIN baec_engine1.ai_run_results AS result
            ON result.organization_id = artifact.organization_id AND result.workspace = artifact.workspace
            AND result.ai_run_id = artifact.ai_run_id
        JOIN baec_engine1.ai_runs AS run
            ON run.organization_id = artifact.organization_id AND run.workspace = artifact.workspace
            AND run.ai_run_id = artifact.ai_run_id
        WHERE artifact.organization_id = NEW.organization_id AND artifact.workspace = NEW.workspace
            AND artifact.artifact_id = NEW.artifact_id
            AND artifact.artifact_digest = NEW.artifact_digest
            AND artifact.account_id = NEW.account_id
            AND artifact.interaction_id = NEW.interaction_id
            AND result.status = 'success'
            AND run.validation_version = 'baec-extraction-validation/v2'
    ) THEN
        RAISE EXCEPTION 'ai_proposals must snapshot a successful, eligible artifact of the same account and interaction'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;

CREATE FUNCTION baec_engine1.ai_proposal_review_revisions_sequence() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    PERFORM baec_engine1.lock_workspace(NEW.organization_id, NEW.workspace);
    IF NEW.revision_number IS DISTINCT FROM 1 + COALESCE(
            (SELECT MAX(r.revision_number) FROM baec_engine1.ai_proposal_review_revisions r
             WHERE r.organization_id = NEW.organization_id AND r.workspace = NEW.workspace
               AND r.proposal_id = NEW.proposal_id), 0)
        OR NEW.previous_revision_id IS DISTINCT FROM (
            SELECT r.review_revision_id FROM baec_engine1.ai_proposal_review_revisions r
            WHERE r.organization_id = NEW.organization_id AND r.workspace = NEW.workspace
              AND r.proposal_id = NEW.proposal_id ORDER BY r.revision_number DESC LIMIT 1) THEN
        RAISE EXCEPTION 'ai_proposal_review_revisions must extend the latest revision of the proposal'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;

CREATE FUNCTION baec_engine1.ai_proposal_review_revisions_open() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    PERFORM baec_engine1.lock_workspace(NEW.organization_id, NEW.workspace);
    IF EXISTS (SELECT 1 FROM baec_engine1.ai_proposal_review_decisions d
               WHERE d.organization_id = NEW.organization_id AND d.workspace = NEW.workspace
                 AND d.proposal_id = NEW.proposal_id AND d.decision = 'REJECTED')
        OR EXISTS (SELECT 1 FROM baec_engine1.ai_proposal_confirmations c
                   WHERE c.organization_id = NEW.organization_id AND c.workspace = NEW.workspace
                     AND c.proposal_id = NEW.proposal_id) THEN
        RAISE EXCEPTION 'ai_proposal_review_revisions cannot extend a rejected or confirmed proposal'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;

-- G7 writer. A new revision supersedes every unretired grant of the proposal, in the same statement.
CREATE FUNCTION baec_engine1.ai_proposal_review_revisions_supersede_grants() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    INSERT INTO baec_engine1.human_authorization_grant_supersessions
        (organization_id, workspace, grant_id, reason, superseding_revision_id, rejection_decision_id, actor_label,
         superseded_at)
    SELECT NEW.organization_id, NEW.workspace, grant_row.grant_id, 'REVISION_SUPERSEDED', NEW.review_revision_id, NULL,
           NEW.actor_label, NEW.created_at
    FROM baec_engine1.human_authorization_grants AS grant_row
    WHERE grant_row.organization_id = NEW.organization_id AND grant_row.workspace = NEW.workspace
        AND grant_row.proposal_id = NEW.proposal_id
        AND NOT EXISTS (SELECT 1 FROM baec_engine1.human_authorization_grant_consumptions c
                        WHERE c.organization_id = NEW.organization_id AND c.workspace = NEW.workspace
                          AND c.grant_id = grant_row.grant_id)
        AND NOT EXISTS (SELECT 1 FROM baec_engine1.human_authorization_grant_supersessions s
                        WHERE s.organization_id = NEW.organization_id AND s.workspace = NEW.workspace
                          AND s.grant_id = grant_row.grant_id)
        AND NOT EXISTS (SELECT 1 FROM baec_engine1.human_authorization_grants n
                        WHERE n.organization_id = NEW.organization_id AND n.workspace = NEW.workspace
                          AND n.supersedes_grant_id = grant_row.grant_id)
    ORDER BY grant_row.seq;
    RETURN NULL;
END $$;

CREATE FUNCTION baec_engine1.ai_proposal_review_decisions_binding() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    PERFORM baec_engine1.lock_workspace(NEW.organization_id, NEW.workspace);
    IF NOT EXISTS (SELECT 1 FROM baec_engine1.ai_proposals p
                   WHERE p.organization_id = NEW.organization_id AND p.workspace = NEW.workspace
                     AND p.proposal_id = NEW.proposal_id AND p.account_id = NEW.account_id)
        OR (NEW.review_revision_id IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM baec_engine1.ai_proposal_review_revisions r
            WHERE r.organization_id = NEW.organization_id AND r.workspace = NEW.workspace
                AND r.review_revision_id = NEW.review_revision_id
                AND r.proposal_id = NEW.proposal_id AND r.account_id = NEW.account_id)) THEN
        RAISE EXCEPTION 'ai_proposal_review_decisions must name its own proposal, revision, and account'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;

CREATE FUNCTION baec_engine1.ai_proposal_review_decisions_latest() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    PERFORM baec_engine1.lock_workspace(NEW.organization_id, NEW.workspace);
    IF NEW.decision = 'ACCEPTED' AND NEW.review_revision_id IS DISTINCT FROM (
            SELECT r.review_revision_id FROM baec_engine1.ai_proposal_review_revisions r
            WHERE r.organization_id = NEW.organization_id AND r.workspace = NEW.workspace
              AND r.proposal_id = NEW.proposal_id ORDER BY r.revision_number DESC LIMIT 1) THEN
        RAISE EXCEPTION 'ai_proposal_review_decisions can accept only the latest revision'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;

CREATE FUNCTION baec_engine1.ai_proposal_review_decisions_terminal() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    PERFORM baec_engine1.lock_workspace(NEW.organization_id, NEW.workspace);
    IF EXISTS (SELECT 1 FROM baec_engine1.ai_proposal_review_decisions d
               WHERE d.organization_id = NEW.organization_id AND d.workspace = NEW.workspace
                 AND d.proposal_id = NEW.proposal_id AND d.decision = 'REJECTED')
        OR EXISTS (SELECT 1 FROM baec_engine1.ai_proposal_confirmations c
                   WHERE c.organization_id = NEW.organization_id AND c.workspace = NEW.workspace
                     AND c.proposal_id = NEW.proposal_id) THEN
        RAISE EXCEPTION 'ai_proposal_review_decisions cannot follow a rejection or a confirmation'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;

-- G2 for decisions: every UNIQUE key, plus the partial index (a second rejection would replace the first).
CREATE FUNCTION baec_engine1.ai_proposal_review_decisions_no_replace() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    PERFORM baec_engine1.lock_workspace(NEW.organization_id, NEW.workspace);
    IF EXISTS (SELECT 1 FROM baec_engine1.ai_proposal_review_decisions d
               WHERE d.organization_id = NEW.organization_id AND d.workspace = NEW.workspace
                 AND d.decision_id = NEW.decision_id)
        OR EXISTS (SELECT 1 FROM baec_engine1.ai_proposal_review_decisions d
                   WHERE d.organization_id = NEW.organization_id AND d.workspace = NEW.workspace
                     AND d.review_revision_id = NEW.review_revision_id)
        OR (NEW.decision = 'REJECTED' AND EXISTS (SELECT 1 FROM baec_engine1.ai_proposal_review_decisions d
                                                  WHERE d.organization_id = NEW.organization_id
                                                    AND d.workspace = NEW.workspace
                                                    AND d.proposal_id = NEW.proposal_id
                                                    AND d.decision = 'REJECTED')) THEN
        RAISE EXCEPTION 'ai_proposal_review_decisions rows cannot be replaced' USING ERRCODE = 'unique_violation';
    END IF;
    RETURN NEW;
END $$;

-- G7 writer. A rejection supersedes every unretired grant of the proposal, in the same statement.
CREATE FUNCTION baec_engine1.ai_proposal_review_decisions_reject_supersedes() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    INSERT INTO baec_engine1.human_authorization_grant_supersessions
        (organization_id, workspace, grant_id, reason, superseding_revision_id, rejection_decision_id, actor_label,
         superseded_at)
    SELECT NEW.organization_id, NEW.workspace, grant_row.grant_id, 'PROPOSAL_REJECTED', NULL, NEW.decision_id,
           NEW.actor_label, NEW.decided_at
    FROM baec_engine1.human_authorization_grants AS grant_row
    WHERE grant_row.organization_id = NEW.organization_id AND grant_row.workspace = NEW.workspace
        AND grant_row.proposal_id = NEW.proposal_id
        AND NOT EXISTS (SELECT 1 FROM baec_engine1.human_authorization_grant_consumptions c
                        WHERE c.organization_id = NEW.organization_id AND c.workspace = NEW.workspace
                          AND c.grant_id = grant_row.grant_id)
        AND NOT EXISTS (SELECT 1 FROM baec_engine1.human_authorization_grant_supersessions s
                        WHERE s.organization_id = NEW.organization_id AND s.workspace = NEW.workspace
                          AND s.grant_id = grant_row.grant_id)
        AND NOT EXISTS (SELECT 1 FROM baec_engine1.human_authorization_grants n
                        WHERE n.organization_id = NEW.organization_id AND n.workspace = NEW.workspace
                          AND n.supersedes_grant_id = grant_row.grant_id)
    ORDER BY grant_row.seq;
    RETURN NULL;
END $$;

-- G7. Grant issuance.
CREATE FUNCTION baec_engine1.human_authorization_grants_accepted() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    PERFORM baec_engine1.lock_workspace(NEW.organization_id, NEW.workspace);
    IF NOT EXISTS (
            SELECT 1 FROM baec_engine1.ai_proposal_review_decisions d
            WHERE d.organization_id = NEW.organization_id AND d.workspace = NEW.workspace
                AND d.decision_id = NEW.decision_id AND d.review_revision_id = NEW.review_revision_id
                AND d.proposal_id = NEW.proposal_id AND d.decision = 'ACCEPTED')
        OR NEW.review_revision_id IS DISTINCT FROM (
            SELECT r.review_revision_id FROM baec_engine1.ai_proposal_review_revisions r
            WHERE r.organization_id = NEW.organization_id AND r.workspace = NEW.workspace
              AND r.proposal_id = NEW.proposal_id ORDER BY r.revision_number DESC LIMIT 1)
        OR EXISTS (SELECT 1 FROM baec_engine1.ai_proposal_review_decisions d
                   WHERE d.organization_id = NEW.organization_id AND d.workspace = NEW.workspace
                     AND d.proposal_id = NEW.proposal_id AND d.decision = 'REJECTED')
        OR EXISTS (SELECT 1 FROM baec_engine1.ai_proposal_confirmations c
                   WHERE c.organization_id = NEW.organization_id AND c.workspace = NEW.workspace
                     AND c.proposal_id = NEW.proposal_id) THEN
        RAISE EXCEPTION 'human_authorization_grants requires an accepted latest revision of an open proposal'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;

CREATE FUNCTION baec_engine1.human_authorization_grants_proposal_digest() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    PERFORM baec_engine1.lock_workspace(NEW.organization_id, NEW.workspace);
    IF NEW.proposal_digest IS DISTINCT FROM (SELECT p.proposal_digest FROM baec_engine1.ai_proposals p
                                             WHERE p.organization_id = NEW.organization_id
                                               AND p.workspace = NEW.workspace AND p.proposal_id = NEW.proposal_id)
    THEN
        RAISE EXCEPTION 'human_authorization_grants proposal_digest disagrees with its proposal'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;

-- At most one unretired grant per (revision, action); a re-issue names exactly its unretired predecessor.
CREATE FUNCTION baec_engine1.human_authorization_grants_one_unretired() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    PERFORM baec_engine1.lock_workspace(NEW.organization_id, NEW.workspace);
    IF EXISTS (
            SELECT 1 FROM baec_engine1.human_authorization_grants AS grant_row
            WHERE grant_row.organization_id = NEW.organization_id AND grant_row.workspace = NEW.workspace
                AND grant_row.review_revision_id = NEW.review_revision_id AND grant_row.action = NEW.action
                AND grant_row.grant_id IS DISTINCT FROM NEW.supersedes_grant_id
                AND NOT EXISTS (SELECT 1 FROM baec_engine1.human_authorization_grant_consumptions c
                                WHERE c.organization_id = NEW.organization_id AND c.workspace = NEW.workspace
                                  AND c.grant_id = grant_row.grant_id)
                AND NOT EXISTS (SELECT 1 FROM baec_engine1.human_authorization_grant_supersessions s
                                WHERE s.organization_id = NEW.organization_id AND s.workspace = NEW.workspace
                                  AND s.grant_id = grant_row.grant_id)
                AND NOT EXISTS (SELECT 1 FROM baec_engine1.human_authorization_grants n
                                WHERE n.organization_id = NEW.organization_id AND n.workspace = NEW.workspace
                                  AND n.supersedes_grant_id = grant_row.grant_id))
        OR (NEW.supersedes_grant_id IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM baec_engine1.human_authorization_grants AS grant_row
            WHERE grant_row.organization_id = NEW.organization_id AND grant_row.workspace = NEW.workspace
                AND grant_row.grant_id = NEW.supersedes_grant_id
                AND grant_row.review_revision_id = NEW.review_revision_id AND grant_row.action = NEW.action
                AND grant_row.issue_sequence = NEW.issue_sequence - 1
                AND NOT EXISTS (SELECT 1 FROM baec_engine1.human_authorization_grant_consumptions c
                                WHERE c.organization_id = NEW.organization_id AND c.workspace = NEW.workspace
                                  AND c.grant_id = grant_row.grant_id)
                AND NOT EXISTS (SELECT 1 FROM baec_engine1.human_authorization_grant_supersessions s
                                WHERE s.organization_id = NEW.organization_id AND s.workspace = NEW.workspace
                                  AND s.grant_id = grant_row.grant_id)
                AND NOT EXISTS (SELECT 1 FROM baec_engine1.human_authorization_grants n
                                WHERE n.organization_id = NEW.organization_id AND n.workspace = NEW.workspace
                                  AND n.supersedes_grant_id = grant_row.grant_id))) THEN
        RAISE EXCEPTION 'human_authorization_grants allows one unretired grant per revision and action'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;

CREATE FUNCTION baec_engine1.human_authorization_grant_supersessions_binding() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    PERFORM baec_engine1.lock_workspace(NEW.organization_id, NEW.workspace);
    IF NOT EXISTS (
        SELECT 1 FROM baec_engine1.human_authorization_grants AS grant_row
        WHERE grant_row.organization_id = NEW.organization_id AND grant_row.workspace = NEW.workspace
            AND grant_row.grant_id = NEW.grant_id
            AND NOT EXISTS (SELECT 1 FROM baec_engine1.human_authorization_grant_consumptions c
                            WHERE c.organization_id = NEW.organization_id AND c.workspace = NEW.workspace
                              AND c.grant_id = grant_row.grant_id)
            AND NOT EXISTS (SELECT 1 FROM baec_engine1.human_authorization_grant_supersessions s
                            WHERE s.organization_id = NEW.organization_id AND s.workspace = NEW.workspace
                              AND s.grant_id = grant_row.grant_id)
            AND NOT EXISTS (SELECT 1 FROM baec_engine1.human_authorization_grants n
                            WHERE n.organization_id = NEW.organization_id AND n.workspace = NEW.workspace
                              AND n.supersedes_grant_id = grant_row.grant_id)
            AND (
                (NEW.reason = 'REVISION_SUPERSEDED' AND EXISTS (
                    SELECT 1 FROM baec_engine1.ai_proposal_review_revisions AS later
                    JOIN baec_engine1.ai_proposal_review_revisions AS granted
                        ON granted.organization_id = later.organization_id AND granted.workspace = later.workspace
                        AND granted.review_revision_id = grant_row.review_revision_id
                    WHERE later.organization_id = NEW.organization_id AND later.workspace = NEW.workspace
                        AND later.review_revision_id = NEW.superseding_revision_id
                        AND later.proposal_id = grant_row.proposal_id
                        AND later.revision_number > granted.revision_number))
                OR (NEW.reason = 'PROPOSAL_REJECTED' AND EXISTS (
                    SELECT 1 FROM baec_engine1.ai_proposal_review_decisions d
                    WHERE d.organization_id = NEW.organization_id AND d.workspace = NEW.workspace
                        AND d.decision_id = NEW.rejection_decision_id
                        AND d.proposal_id = grant_row.proposal_id AND d.decision = 'REJECTED')))
    ) THEN
        RAISE EXCEPTION 'human_authorization_grant_supersessions must retire an unretired grant by a later revision or a rejection of its proposal'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;

-- G8. Confirmation and grant consumption (the human-authorization link). Text comparison of authorized_at with the
-- grant's issued_at, exactly as stored (two spellings of one instant differ, as in SQLite).
CREATE FUNCTION baec_engine1.ai_proposal_confirmations_authorization_binding() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    PERFORM baec_engine1.lock_workspace(NEW.organization_id, NEW.workspace);
    IF NOT EXISTS (
        SELECT 1 FROM baec_engine1.baec_records AS record
        JOIN baec_engine1.human_authorizations AS auth_row
            ON auth_row.organization_id = record.organization_id AND auth_row.workspace = record.workspace
            AND auth_row.authorization_id = record.confirmation_authorization_id
        JOIN baec_engine1.human_authorization_grants AS grant_row
            ON grant_row.organization_id = record.organization_id AND grant_row.workspace = record.workspace
            AND grant_row.grant_id = NEW.grant_id
        WHERE record.organization_id = NEW.organization_id AND record.workspace = NEW.workspace
            AND record.baec_id = NEW.baec_id
            AND auth_row.authorization_id = NEW.authorization_id
            AND auth_row.action = 'CONFIRM_BAEC'
            AND auth_row.subject_id = NEW.baec_id
            AND auth_row.target_state IS NULL
            AND auth_row.authorized_by = grant_row.actor_label
            AND auth_row.authorized_at = grant_row.issued_at
    ) THEN
        RAISE EXCEPTION 'ai_proposal_confirmations requires the BAEC confirmation authorization derived from its grant'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;

CREATE FUNCTION baec_engine1.ai_proposal_confirmations_unretired() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    PERFORM baec_engine1.lock_workspace(NEW.organization_id, NEW.workspace);
    IF NOT EXISTS (
            SELECT 1 FROM baec_engine1.human_authorization_grants AS grant_row
            WHERE grant_row.organization_id = NEW.organization_id AND grant_row.workspace = NEW.workspace
                AND grant_row.grant_id = NEW.grant_id
                AND NOT EXISTS (SELECT 1 FROM baec_engine1.human_authorization_grant_consumptions c
                                WHERE c.organization_id = NEW.organization_id AND c.workspace = NEW.workspace
                                  AND c.grant_id = grant_row.grant_id)
                AND NOT EXISTS (SELECT 1 FROM baec_engine1.human_authorization_grant_supersessions s
                                WHERE s.organization_id = NEW.organization_id AND s.workspace = NEW.workspace
                                  AND s.grant_id = grant_row.grant_id)
                AND NOT EXISTS (SELECT 1 FROM baec_engine1.human_authorization_grants n
                                WHERE n.organization_id = NEW.organization_id AND n.workspace = NEW.workspace
                                  AND n.supersedes_grant_id = grant_row.grant_id))
        OR NEW.review_revision_id IS DISTINCT FROM (
            SELECT r.review_revision_id FROM baec_engine1.ai_proposal_review_revisions r
            WHERE r.organization_id = NEW.organization_id AND r.workspace = NEW.workspace
              AND r.proposal_id = NEW.proposal_id ORDER BY r.revision_number DESC LIMIT 1)
        OR EXISTS (SELECT 1 FROM baec_engine1.ai_proposal_review_decisions d
                   WHERE d.organization_id = NEW.organization_id AND d.workspace = NEW.workspace
                     AND d.proposal_id = NEW.proposal_id AND d.decision = 'REJECTED') THEN
        RAISE EXCEPTION 'ai_proposal_confirmations requires an unretired grant on the latest revision of an open proposal'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;

CREATE FUNCTION baec_engine1.ai_proposal_confirmations_classification() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    PERFORM baec_engine1.lock_workspace(NEW.organization_id, NEW.workspace);
    IF NOT EXISTS (
        SELECT 1 FROM baec_engine1.baec_records b
        WHERE b.organization_id = NEW.organization_id AND b.workspace = NEW.workspace
            AND b.baec_id = NEW.baec_id AND b.classification = 'CONFIRMED_BAEC' AND b.staleness_status = 'CURRENT'
    ) THEN
        RAISE EXCEPTION 'ai_proposal_confirmations requires a CURRENT confirmed BAEC' USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;

-- Compares two stored values with SQLite's julianday arithmetic; never the current time.
CREATE FUNCTION baec_engine1.human_authorization_grant_consumptions_window() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    PERFORM baec_engine1.lock_workspace(NEW.organization_id, NEW.workspace);
    IF NOT COALESCE((
        SELECT baec_engine1.julianday(grant_row.issued_at) <= baec_engine1.julianday(NEW.consumed_at)
            AND baec_engine1.julianday(NEW.consumed_at) < baec_engine1.julianday(grant_row.expires_at)
        FROM baec_engine1.human_authorization_grants AS grant_row
        WHERE grant_row.organization_id = NEW.organization_id AND grant_row.workspace = NEW.workspace
            AND grant_row.grant_id = NEW.grant_id), false) THEN
        RAISE EXCEPTION 'human_authorization_grant_consumptions must fall inside the grant validity window'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;

CREATE FUNCTION baec_engine1.human_authorization_grant_consumptions_pairing() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    PERFORM baec_engine1.lock_workspace(NEW.organization_id, NEW.workspace);
    IF NOT EXISTS (
        SELECT 1 FROM baec_engine1.ai_proposal_confirmations c
        WHERE c.organization_id = NEW.organization_id AND c.workspace = NEW.workspace
            AND c.baec_id = NEW.baec_id AND c.grant_id = NEW.grant_id
    ) THEN
        RAISE EXCEPTION 'human_authorization_grant_consumptions must pair with its confirmation link'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;

-- --- The 92 SQLite triggers ------------------------------------------------------------------------------------------

-- G1 (45): <t>_no_update and <t>_no_delete on the 22 history tables; baec_records_no_delete below. G2 (23):
-- f<k>_<t>_no_replace, k = its SQLite firing position (always last). Keys exactly as SQLite's REPLACE_GUARDED_KEYS,
-- AI_REPLACE_GUARDED_KEYS, and BRIDGE_REPLACE_GUARDED_KEYS (decisions has its own function above). Every table also
-- refuses TRUNCATE, which SQLite does not have.
DO $$
DECLARE
    keys jsonb := '{
        "interactions": [1, "interaction_id"],
        "interaction_evidence": [2, "#evidence_id", "interaction_id,provenance,text"],
        "human_authorizations": [1, "#authorization_id"],
        "criterion_assessments": [1, "baec_id,criterion", "baec_id,position"],
        "criterion_evidence": [1, "baec_id,criterion,position"],
        "stringency_expressions": [1, "baec_id"],
        "dormancy_judgments": [1, "#judgment_id", "authorization_id"],
        "evaluation_evidence": [1, "#evaluation_evidence_id"],
        "non_evaluation_evidence": [1, "#non_evaluation_evidence_id"],
        "account_state_transitions": [1, "#transition_id", "authorization_id"],
        "ai_runs": [1, "ai_run_id"],
        "ai_run_results": [3, "ai_run_id"],
        "ai_run_outputs": [2, "ai_run_id"],
        "ai_artifacts": [2, "artifact_id", "ai_run_id"],
        "ai_artifact_excerpts": [2, "artifact_id,excerpt_id", "artifact_id,interaction_id,text"],
        "ai_proposals": [2, "proposal_id", "artifact_id,mapping_version"],
        "ai_proposal_review_revisions": [3, "review_revision_id", "proposal_id,revision_number"],
        "ai_proposal_review_decisions": [4],
        "human_authorization_grants": [4, "grant_id", "baec_id", "supersedes_grant_id",
                                       "review_revision_id,action,issue_sequence"],
        "human_authorization_grant_supersessions": [2, "grant_id"],
        "ai_proposal_confirmations": [4, "baec_id", "artifact_id", "proposal_id", "review_revision_id", "grant_id",
                                      "authorization_id"],
        "human_authorization_grant_consumptions": [3, "grant_id", "baec_id"]
    }';
    t text;
    args text;
BEGIN
    FOR t IN SELECT jsonb_object_keys(keys) LOOP
        EXECUTE format('CREATE TRIGGER %I BEFORE UPDATE ON baec_engine1.%I '
                       'FOR EACH ROW EXECUTE FUNCTION baec_engine1.refuse_change()', t || '_no_update', t);
        EXECUTE format('CREATE TRIGGER %I BEFORE DELETE ON baec_engine1.%I '
                       'FOR EACH ROW EXECUTE FUNCTION baec_engine1.refuse_change()', t || '_no_delete', t);
        IF t = 'ai_proposal_review_decisions' THEN
            EXECUTE format('CREATE TRIGGER %I BEFORE INSERT ON baec_engine1.%I FOR EACH ROW '
                           'EXECUTE FUNCTION baec_engine1.ai_proposal_review_decisions_no_replace()',
                           'f' || (keys -> t ->> 0) || '_' || t || '_no_replace', t);
        ELSE
            SELECT string_agg(quote_literal(k), ', ' ORDER BY n) INTO args
            FROM jsonb_array_elements_text(keys -> t) WITH ORDINALITY AS e (k, n) WHERE n > 1;
            EXECUTE format('CREATE TRIGGER %I BEFORE INSERT ON baec_engine1.%I '
                           'FOR EACH ROW EXECUTE FUNCTION baec_engine1.refuse_replace(%s)',
                           'f' || (keys -> t ->> 0) || '_' || t || '_no_replace', t, args);
        END IF;
    END LOOP;
    FOR t IN SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
             WHERE n.nspname = 'baec_engine1' AND c.relkind = 'r' LOOP
        EXECUTE format('CREATE TRIGGER %I BEFORE TRUNCATE ON baec_engine1.%I '
                       'FOR EACH STATEMENT EXECUTE FUNCTION baec_engine1.refuse_change()', t || '_no_truncate', t);
    END LOOP;
END $$;

-- baec_records: G2 (both UNIQUE keys), G3, and its G1 delete guard.
CREATE TRIGGER f1_baec_records_no_replace BEFORE INSERT ON baec_engine1.baec_records
    FOR EACH ROW EXECUTE FUNCTION baec_engine1.refuse_replace('baec_id', 'confirmation_authorization_id');
CREATE TRIGGER baec_records_protected_no_update
    BEFORE UPDATE OF baec_id, account_id, source_interaction_id, source_excerpt_id, captured_at, buyer_role,
        buyer_exact_statement, articulation_origin, elicitation_mode, classification, classification_reason,
        confirmation_authorization_id, normalized_text, normalized_generated_at, normalized_model,
        organization_id, workspace
    ON baec_engine1.baec_records
    FOR EACH ROW EXECUTE FUNCTION baec_engine1.baec_records_protected_no_update();
CREATE TRIGGER baec_records_no_delete BEFORE DELETE ON baec_engine1.baec_records
    FOR EACH ROW EXECUTE FUNCTION baec_engine1.baec_records_no_delete();

-- G4
CREATE TRIGGER f1_interaction_evidence_verbatim BEFORE INSERT ON baec_engine1.interaction_evidence
    FOR EACH ROW EXECUTE FUNCTION baec_engine1.interaction_evidence_verbatim();
CREATE TRIGGER f1_ai_artifact_excerpts_verbatim BEFORE INSERT ON baec_engine1.ai_artifact_excerpts
    FOR EACH ROW EXECUTE FUNCTION baec_engine1.ai_artifact_excerpts_verbatim();

-- G5
CREATE TRIGGER f1_ai_run_results_failure_codes_closed BEFORE INSERT ON baec_engine1.ai_run_results
    FOR EACH ROW EXECUTE FUNCTION baec_engine1.ai_run_results_failure_codes_closed();
CREATE TRIGGER f2_ai_run_results_model_binding BEFORE INSERT ON baec_engine1.ai_run_results
    FOR EACH ROW EXECUTE FUNCTION baec_engine1.ai_run_results_model_binding();
CREATE TRIGGER f1_ai_run_outputs_require_result BEFORE INSERT ON baec_engine1.ai_run_outputs
    FOR EACH ROW EXECUTE FUNCTION baec_engine1.ai_run_outputs_require_result();
CREATE TRIGGER f1_ai_artifacts_require_success BEFORE INSERT ON baec_engine1.ai_artifacts
    FOR EACH ROW EXECUTE FUNCTION baec_engine1.ai_artifacts_require_success();

-- G6
CREATE TRIGGER f1_ai_proposals_artifact_binding BEFORE INSERT ON baec_engine1.ai_proposals
    FOR EACH ROW EXECUTE FUNCTION baec_engine1.ai_proposals_artifact_binding();
CREATE TRIGGER f1_ai_proposal_review_revisions_open BEFORE INSERT ON baec_engine1.ai_proposal_review_revisions
    FOR EACH ROW EXECUTE FUNCTION baec_engine1.ai_proposal_review_revisions_open();
CREATE TRIGGER f2_ai_proposal_review_revisions_sequence BEFORE INSERT ON baec_engine1.ai_proposal_review_revisions
    FOR EACH ROW EXECUTE FUNCTION baec_engine1.ai_proposal_review_revisions_sequence();
CREATE TRIGGER f1_ai_proposal_review_decisions_terminal BEFORE INSERT ON baec_engine1.ai_proposal_review_decisions
    FOR EACH ROW EXECUTE FUNCTION baec_engine1.ai_proposal_review_decisions_terminal();
CREATE TRIGGER f2_ai_proposal_review_decisions_latest BEFORE INSERT ON baec_engine1.ai_proposal_review_decisions
    FOR EACH ROW EXECUTE FUNCTION baec_engine1.ai_proposal_review_decisions_latest();
CREATE TRIGGER f3_ai_proposal_review_decisions_binding BEFORE INSERT ON baec_engine1.ai_proposal_review_decisions
    FOR EACH ROW EXECUTE FUNCTION baec_engine1.ai_proposal_review_decisions_binding();

-- G7
CREATE TRIGGER ai_proposal_review_revisions_supersede_grants AFTER INSERT ON baec_engine1.ai_proposal_review_revisions
    FOR EACH ROW EXECUTE FUNCTION baec_engine1.ai_proposal_review_revisions_supersede_grants();
CREATE TRIGGER ai_proposal_review_decisions_reject_supersedes AFTER INSERT ON baec_engine1.ai_proposal_review_decisions
    FOR EACH ROW WHEN (NEW.decision = 'REJECTED')
    EXECUTE FUNCTION baec_engine1.ai_proposal_review_decisions_reject_supersedes();
CREATE TRIGGER f1_human_authorization_grants_one_unretired BEFORE INSERT ON baec_engine1.human_authorization_grants
    FOR EACH ROW EXECUTE FUNCTION baec_engine1.human_authorization_grants_one_unretired();
CREATE TRIGGER f2_human_authorization_grants_proposal_digest BEFORE INSERT ON baec_engine1.human_authorization_grants
    FOR EACH ROW EXECUTE FUNCTION baec_engine1.human_authorization_grants_proposal_digest();
CREATE TRIGGER f3_human_authorization_grants_accepted BEFORE INSERT ON baec_engine1.human_authorization_grants
    FOR EACH ROW EXECUTE FUNCTION baec_engine1.human_authorization_grants_accepted();
CREATE TRIGGER f1_human_authorization_grant_supersessions_binding
    BEFORE INSERT ON baec_engine1.human_authorization_grant_supersessions
    FOR EACH ROW EXECUTE FUNCTION baec_engine1.human_authorization_grant_supersessions_binding();

-- G8
CREATE TRIGGER f1_ai_proposal_confirmations_classification BEFORE INSERT ON baec_engine1.ai_proposal_confirmations
    FOR EACH ROW EXECUTE FUNCTION baec_engine1.ai_proposal_confirmations_classification();
CREATE TRIGGER f2_ai_proposal_confirmations_unretired BEFORE INSERT ON baec_engine1.ai_proposal_confirmations
    FOR EACH ROW EXECUTE FUNCTION baec_engine1.ai_proposal_confirmations_unretired();
CREATE TRIGGER f3_ai_proposal_confirmations_authorization_binding
    BEFORE INSERT ON baec_engine1.ai_proposal_confirmations
    FOR EACH ROW EXECUTE FUNCTION baec_engine1.ai_proposal_confirmations_authorization_binding();
CREATE TRIGGER f1_human_authorization_grant_consumptions_pairing
    BEFORE INSERT ON baec_engine1.human_authorization_grant_consumptions
    FOR EACH ROW EXECUTE FUNCTION baec_engine1.human_authorization_grant_consumptions_pairing();
CREATE TRIGGER f2_human_authorization_grant_consumptions_window
    BEFORE INSERT ON baec_engine1.human_authorization_grant_consumptions
    FOR EACH ROW EXECUTE FUNCTION baec_engine1.human_authorization_grant_consumptions_window();

-- PostgreSQL-only: the six SQLite rowid ids, assigned after every guard.
CREATE TRIGGER f9_interaction_evidence_assign_id BEFORE INSERT ON baec_engine1.interaction_evidence
    FOR EACH ROW EXECUTE FUNCTION baec_engine1.assign_rowid('evidence_id');
CREATE TRIGGER f9_human_authorizations_assign_id BEFORE INSERT ON baec_engine1.human_authorizations
    FOR EACH ROW EXECUTE FUNCTION baec_engine1.assign_rowid('authorization_id');
CREATE TRIGGER f9_dormancy_judgments_assign_id BEFORE INSERT ON baec_engine1.dormancy_judgments
    FOR EACH ROW EXECUTE FUNCTION baec_engine1.assign_rowid('judgment_id');
CREATE TRIGGER f9_evaluation_evidence_assign_id BEFORE INSERT ON baec_engine1.evaluation_evidence
    FOR EACH ROW EXECUTE FUNCTION baec_engine1.assign_rowid('evaluation_evidence_id');
CREATE TRIGGER f9_non_evaluation_evidence_assign_id BEFORE INSERT ON baec_engine1.non_evaluation_evidence
    FOR EACH ROW EXECUTE FUNCTION baec_engine1.assign_rowid('non_evaluation_evidence_id');
CREATE TRIGGER f9_account_state_transitions_assign_id BEFORE INSERT ON baec_engine1.account_state_transitions
    FOR EACH ROW EXECUTE FUNCTION baec_engine1.assign_rowid('transition_id');

-- Row-level security and grants, as 0003 does for Engine 2: organization isolation, the restrictive boundary, and the
-- workspace scope. The application role reads and appends; the only column it may update is accounts.state (the
-- repository's compare-and-swap). baec_records.staleness_status stays unwritable until a staleness phase exists.
DO $$
DECLARE
    t text;
BEGIN
    FOR t IN SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
             WHERE n.nspname = 'baec_engine1' AND c.relkind = 'r' LOOP
        EXECUTE format('ALTER TABLE baec_engine1.%I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('ALTER TABLE baec_engine1.%I FORCE ROW LEVEL SECURITY', t);
        EXECUTE format('CREATE POLICY tenant_isolation ON baec_engine1.%I AS PERMISSIVE FOR ALL '
                       'USING (organization_id = baec.current_organization()) '
                       'WITH CHECK (organization_id = baec.current_organization())', t);
        EXECUTE format('CREATE POLICY tenant_boundary ON baec_engine1.%I AS RESTRICTIVE FOR ALL '
                       'USING (organization_id = baec.current_organization()) '
                       'WITH CHECK (organization_id = baec.current_organization())', t);
        EXECUTE format('CREATE POLICY workspace_scope ON baec_engine1.%I AS RESTRICTIVE FOR ALL '
                       'USING (workspace = baec.current_workspace()) '
                       'WITH CHECK (workspace = baec.current_workspace())', t);
        EXECUTE format('GRANT SELECT, INSERT ON baec_engine1.%I TO baec_app', t);
    END LOOP;
END $$;

GRANT USAGE ON SCHEMA baec_engine1 TO baec_app;
GRANT UPDATE (state) ON baec_engine1.accounts TO baec_app;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA baec_engine1 FROM PUBLIC;
-- Called from guards, the TTL CHECK, and the two text-key indexes, which run as the invoking role.
GRANT EXECUTE ON FUNCTION baec_engine1.lock_workspace(text, text), baec_engine1.julianday(text),
    baec_engine1.text_key(text) TO baec_app;
