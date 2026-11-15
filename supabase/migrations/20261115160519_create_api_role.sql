-- Dedicated database role for the FastAPI backend, plus safe default privileges.
--
-- skillloop_api is the ONLY role the backend logs in with (through the Supavisor session
-- pooler). It is deliberately powerless on its own:
--   * NOINHERIT, NOBYPASSRLS, no superuser / createrole / createdb / replication;
--   * no privileges on any application table.
-- The backend must run every user request inside a transaction that executes
--   SET LOCAL ROLE authenticated
-- plus a transaction-local request.jwt.claims (see backend app/db/session.py).
-- A query issued without that context fails with "permission denied" (fail closed)
-- instead of silently running with broad privileges.
--
-- The password is NOT set here (secrets never live in git). Set it manually per
-- environment, e.g.:  ALTER ROLE skillloop_api PASSWORD '<secret>';

do $$
begin
  if not exists (select 1 from pg_roles where rolname = 'skillloop_api') then
    -- Superuser, bypassrls, replication, createdb and createrole all default to "no".
    -- They are not spelled out because the migration role (postgres) is not a superuser
    -- and PostgreSQL refuses to let it name those attributes. The pgTAP test in
    -- supabase/tests/database/ asserts them instead.
    create role skillloop_api with login noinherit;
  end if;
end
$$;

-- Re-asserted so that the two attributes we can control cannot drift.
alter role skillloop_api with login noinherit;

-- Backstops that apply to the whole session, including after SET LOCAL ROLE
-- (role-level settings of "authenticated", e.g. its statement_timeout, are NOT applied by SET ROLE).
alter role skillloop_api set statement_timeout = '15s';
alter role skillloop_api set idle_in_transaction_session_timeout = '30s';

-- The only privilege: the right to switch to "authenticated" (SET), without inheriting
-- its privileges (INHERIT FALSE). skillloop_api cannot switch to postgres, service_role
-- or any other role.
grant authenticated to skillloop_api with inherit false, set true;

-- Supabase grants every new object created by "postgres" in schema public to anon and
-- authenticated by default (and to service_role). Remove that for anon and authenticated,
-- so a table added by a future migration is NOT accessible until it is explicitly granted.
-- service_role is intentionally left untouched. Default privileges of other owners
-- (e.g. supabase_admin) only affect objects they create, not our migrations.
--
-- This is only a safety net: every migration must still REVOKE explicitly on each new
-- table and then GRANT the minimum (see supabase/tests/database/).
alter default privileges for role postgres in schema public
  revoke all on tables from anon, authenticated;
alter default privileges for role postgres in schema public
  revoke all on sequences from anon, authenticated;
alter default privileges for role postgres in schema public
  revoke all on functions from anon, authenticated;
