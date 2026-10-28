-- 0006: Exact grant validity window. Runs as the migration role, which owns every object here.
--
-- The guard on baec_engine1.human_authorization_grant_consumptions compared timestamps with baec_engine1.julianday,
-- which reproduces SQLite's julianday(): a float of whole milliseconds. Below a millisecond two instants compared
-- equal, so a consumption one microsecond before a sub-millisecond expiry was refused. The SQLite schema now compares
-- exact integer microseconds (baec_app.data.database._exact_instant_us); this migration gives PostgreSQL the same
-- rule, so both backends agree to the microsecond. Only the trigger's function body changes. The trigger, the table,
-- every row, and baec_engine1.julianday (still used by the grant TTL CHECK) are unchanged.

-- The timestamp as exact microseconds since the Unix epoch, or NULL unless it is 'YYYY-MM-DDTHH:MM:SS' (a real calendar
-- date, hour 00 to 23), an optional '.' and 1 to 6 digits, then 'Z', 'z', or '+HH:MM' / '-HH:MM' (offset hours <= 14).
-- The same grammar and the same value as the SQLite expression. Integer arithmetic only; never the current time.
CREATE FUNCTION baec_engine1.exact_instant_us(text) RETURNS bigint
    LANGUAGE plpgsql IMMUTABLE
    AS $$
DECLARE
    m text[];
    day date;
    zone bigint := 0;
BEGIN
    m := pg_catalog.regexp_match($1,
        '^([0-9]{4})-([0-9]{2})-([0-9]{2})T([0-9]{2}):([0-9]{2}):([0-9]{2})(?:\.([0-9]{1,6}))?(?:([Zz])|([+-])([0-9]{2}):([0-9]{2}))$');
    IF m IS NULL OR m[4]::int > 23 OR m[5]::int > 59 OR m[6]::int > 59 THEN
        RETURN NULL;
    END IF;
    IF m[9] IS NOT NULL THEN
        IF m[10]::int > 14 OR m[11]::int > 59 THEN
            RETURN NULL;
        END IF;
        zone := (m[10]::bigint * 3600 + m[11]::bigint * 60) * CASE WHEN m[9] = '-' THEN -1 ELSE 1 END;
    END IF;
    BEGIN
        day := pg_catalog.make_date(m[1]::int, m[2]::int, m[3]::int);
    EXCEPTION WHEN datetime_field_overflow OR invalid_datetime_format THEN
        RETURN NULL;
    END;
    RETURN ((day - DATE '1970-01-01')::bigint * 86400 + m[4]::bigint * 3600 + m[5]::bigint * 60 + m[6]::bigint - zone)
        * 1000000 + pg_catalog.rpad(COALESCE(m[7], ''), 6, '0')::bigint;
END $$;

CREATE OR REPLACE FUNCTION baec_engine1.human_authorization_grant_consumptions_window() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    PERFORM baec_engine1.lock_workspace(NEW.organization_id, NEW.workspace);
    IF NOT COALESCE((
        SELECT baec_engine1.exact_instant_us(grant_row.issued_at) <= baec_engine1.exact_instant_us(NEW.consumed_at)
            AND baec_engine1.exact_instant_us(NEW.consumed_at) < baec_engine1.exact_instant_us(grant_row.expires_at)
        FROM baec_engine1.human_authorization_grants AS grant_row
        WHERE grant_row.organization_id = NEW.organization_id AND grant_row.workspace = NEW.workspace
            AND grant_row.grant_id = NEW.grant_id), false) THEN
        RAISE EXCEPTION 'human_authorization_grant_consumptions must fall inside the grant validity window'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;

REVOKE ALL ON FUNCTION baec_engine1.exact_instant_us(text) FROM PUBLIC;
-- Called from the window guard, which runs as the invoking role.
GRANT EXECUTE ON FUNCTION baec_engine1.exact_instant_us(text) TO baec_app;
