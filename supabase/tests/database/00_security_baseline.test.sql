-- Security baseline for the database. Run with:  npx supabase test db
--
-- Part 1 checks the skillloop_api role and the default privileges.
-- Part 2 holds invariants that apply to EVERY table / function in schema public, so
-- tables added by future migrations are covered automatically.
-- Everything runs in a transaction that is rolled back.

begin;
create extension if not exists pgtap with schema extensions;

select plan(22);

-- ---------------------------------------------------------------------------
-- Part 1a: the skillloop_api role
-- ---------------------------------------------------------------------------
select has_role('skillloop_api', 'role skillloop_api exists');

select results_eq(
  $$ select rolcanlogin, rolinherit, rolsuper, rolbypassrls, rolcreaterole, rolcreatedb, rolreplication
     from pg_roles where rolname = 'skillloop_api' $$,
  $$ values (true, false, false, false, false, false, false) $$,
  'skillloop_api: login, NOINHERIT, no superuser/bypassrls/createrole/createdb/replication'
);

select results_eq(
  $$ select roleid::regrole::text from pg_auth_members where member = 'skillloop_api'::regrole $$,
  $$ values ('authenticated') $$,
  'skillloop_api is a member of authenticated only'
);

select results_eq(
  $$ select inherit_option, set_option, admin_option from pg_auth_members
     where member = 'skillloop_api'::regrole and roleid = 'authenticated'::regrole $$,
  $$ values (false, true, false) $$,
  'membership in authenticated: INHERIT FALSE, SET TRUE, no ADMIN'
);

-- PostgreSQL 16+ automatically gives the creating role (postgres) ADMIN OPTION only.
select ok(
  not exists (select 1 from pg_auth_members
              where roleid = 'skillloop_api'::regrole and (set_option or inherit_option)),
  'no role can SET ROLE to skillloop_api or inherit its privileges'
);

select ok(
  not has_schema_privilege('skillloop_api', 'auth', 'USAGE'),
  'skillloop_api has no access to schema auth without SET ROLE'
);

select ok(
  not exists (
    select 1 from pg_class c join pg_namespace n on n.oid = c.relnamespace
    where n.nspname = 'public' and c.relkind in ('r', 'p', 'v', 'm', 'f')
      and has_table_privilege('skillloop_api', c.oid,
          'SELECT, INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER')
  ),
  'skillloop_api has no privileges on any table in public (fail closed without SET ROLE)'
);

select ok(
  not has_table_privilege('skillloop_api', 'auth.users', 'SELECT'),
  'skillloop_api cannot read auth.users directly'
);

-- ---------------------------------------------------------------------------
-- Part 1b: default privileges of the migration role
-- ---------------------------------------------------------------------------
select ok(
  not exists (
    select 1 from pg_default_acl d, aclexplode(d.defaclacl) a
    where d.defaclrole = 'postgres'::regrole
      and d.defaclnamespace = 'public'::regnamespace
      and a.grantee in ('anon'::regrole, 'authenticated'::regrole)
  ),
  'default privileges of postgres in schema public grant nothing to anon or authenticated'
);

select ok(
  exists (
    select 1 from pg_default_acl d, aclexplode(d.defaclacl) a
    where d.defaclrole = 'postgres'::regrole
      and d.defaclnamespace = 'public'::regnamespace and d.defaclobjtype = 'r'
      and a.grantee = 'service_role'::regrole
  ),
  'service_role keeps its default privileges on new tables'
);

-- ---------------------------------------------------------------------------
-- Part 1c: a freshly created object gets no access for anon / authenticated
-- ---------------------------------------------------------------------------
create table public.__probe_table (id int generated always as identity primary key);
create function public.__probe_fn() returns int language sql as 'select 1';
revoke execute on function public.__probe_fn() from public;

select ok(
  not has_table_privilege('anon', 'public.__probe_table',
        'SELECT, INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER'),
  'new table: anon has no privileges'
);
select ok(
  not has_table_privilege('authenticated', 'public.__probe_table',
        'SELECT, INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER'),
  'new table: authenticated has no privileges until a migration grants them'
);
select ok(
  not has_table_privilege('skillloop_api', 'public.__probe_table', 'SELECT, INSERT, UPDATE, DELETE'),
  'new table: skillloop_api has no privileges'
);
select ok(
  has_table_privilege('service_role', 'public.__probe_table', 'SELECT, INSERT, UPDATE, DELETE'),
  'new table: service_role keeps its privileges (intentionally untouched)'
);
select ok(
  not has_sequence_privilege('anon', 'public.__probe_table_id_seq', 'USAGE, SELECT, UPDATE')
  and not has_sequence_privilege('authenticated', 'public.__probe_table_id_seq', 'USAGE, SELECT, UPDATE'),
  'new sequence: no privileges for anon and authenticated'
);
select ok(
  not has_function_privilege('anon', 'public.__probe_fn()', 'EXECUTE')
  and not has_function_privilege('authenticated', 'public.__probe_fn()', 'EXECUTE')
  and not has_function_privilege('skillloop_api', 'public.__probe_fn()', 'EXECUTE'),
  'new function + explicit REVOKE FROM PUBLIC: not executable by anon/authenticated/skillloop_api'
);

-- The probe objects are dropped so that the invariants below only see real objects.
drop function public.__probe_fn();
drop table public.__probe_table;

-- ---------------------------------------------------------------------------
-- Part 2: invariants for every table / function in public (present and future)
-- ---------------------------------------------------------------------------
select is(
  (select count(*)::int from pg_class c join pg_namespace n on n.oid = c.relnamespace
   where n.nspname = 'public' and c.relkind in ('r', 'p') and not c.relrowsecurity),
  0,
  'every table in public has row level security enabled'
);

select is(
  (select count(*)::int from pg_class c join pg_namespace n on n.oid = c.relnamespace
   where n.nspname = 'public' and c.relkind in ('r', 'p', 'v', 'm', 'S', 'f')
     and has_table_privilege('anon', c.oid,
         'SELECT, INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER')
     and c.relkind <> 'S'),
  0,
  'anon has no privileges on any relation in public'
);

select is(
  (select count(*)::int from pg_class c join pg_namespace n on n.oid = c.relnamespace
   where n.nspname = 'public' and c.relkind in ('r', 'p')
     and has_table_privilege('authenticated', c.oid, 'TRUNCATE, REFERENCES, TRIGGER')),
  0,
  'authenticated has no TRUNCATE / REFERENCES / TRIGGER on any table in public'
);

select is(
  (select count(*)::int from pg_class c join pg_namespace n on n.oid = c.relnamespace
   where n.nspname = 'public' and c.relkind = 'S'
     and (has_sequence_privilege('anon', c.oid, 'USAGE, SELECT, UPDATE')
          or has_sequence_privilege('authenticated', c.oid, 'USAGE, SELECT, UPDATE'))),
  0,
  'no sequence in public is accessible to anon or authenticated'
);

-- Functions: PostgreSQL grants EXECUTE to PUBLIC by default and that cannot be turned
-- off per schema, so every function we define must REVOKE it explicitly. The backend
-- does not call database functions; extension-owned functions are excluded.
select is(
  (select count(*)::int from pg_proc p join pg_namespace n on n.oid = p.pronamespace
   where n.nspname = 'public'
     and not exists (select 1 from pg_depend d where d.objid = p.oid and d.deptype = 'e')
     and (has_function_privilege('anon', p.oid, 'EXECUTE')
          or has_function_privilege('authenticated', p.oid, 'EXECUTE')
          or has_function_privilege('skillloop_api', p.oid, 'EXECUTE'))),
  0,
  'no function defined in public is executable by anon, authenticated or skillloop_api'
);

select is(
  (select count(*)::int from pg_proc p join pg_namespace n on n.oid = p.pronamespace
   where n.nspname = 'public' and p.prosecdef
     and (p.proconfig is null or not exists (
            select 1 from unnest(p.proconfig) c where c like 'search_path=%'))),
  0,
  'every SECURITY DEFINER function in public pins its search_path'
);

select * from finish();
rollback;
