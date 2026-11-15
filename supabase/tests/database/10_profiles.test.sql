-- Tests for public.profiles: structure, constraints, triggers and RLS.
-- Runs as postgres inside one transaction that is rolled back. The "authenticated" and
-- "anon" paths use SET LOCAL ROLE plus request.jwt.claims. The skillloop_api -> SET ROLE
-- path cannot be exercised here (postgres may not SET ROLE skillloop_api); it is covered
-- by the backend integration tests.

begin;
select plan(35);

-- Structure -------------------------------------------------------------------------
select has_table('public', 'profiles', 'profiles exists');
select columns_are(
  'public', 'profiles',
  array['id', 'display_name', 'preferred_language', 'created_at', 'updated_at'],
  'profiles has exactly the documented columns'
);
select col_is_pk('public', 'profiles', 'id', 'id is the primary key');
select col_default_is('public', 'profiles', 'preferred_language', 'en', 'language defaults to en');
select ok(
  exists (
    select 1 from pg_constraint
    where conrelid = 'public.profiles'::regclass
      and contype = 'f'
      and confrelid = 'auth.users'::regclass
      and confdeltype = 'c'
  ),
  'id references auth.users ON DELETE CASCADE'
);
select ok(
  (select relrowsecurity from pg_class where oid = 'public.profiles'::regclass),
  'RLS is enabled'
);

-- Two independent barriers: missing grants (checked here) and missing policies ----------
select ok(
  has_table_privilege('authenticated', 'public.profiles', 'select')
  and not has_table_privilege('authenticated', 'public.profiles', 'insert')
  and not has_table_privilege('authenticated', 'public.profiles', 'delete')
  and not has_table_privilege('anon', 'public.profiles', 'select'),
  'authenticated may only read; no insert/delete grant; anon has nothing'
);

-- Fixtures: two users; the trigger must create their profiles -----------------------
insert into auth.users (id, email) values
  ('11111111-1111-1111-1111-111111111111', 'a@example.test'),
  ('22222222-2222-2222-2222-222222222222', 'b@example.test');

select is(
  (select count(*)::int from public.profiles
   where id in ('11111111-1111-1111-1111-111111111111', '22222222-2222-2222-2222-222222222222')),
  2,
  'inserting into auth.users creates a profile per user'
);
select is(
  (select preferred_language from public.profiles where id = '11111111-1111-1111-1111-111111111111'),
  'en',
  'a new profile has the default language'
);
select is(
  (select display_name from public.profiles where id = '11111111-1111-1111-1111-111111111111'),
  null,
  'a new profile has no display name'
);

-- Constraints (as postgres) ---------------------------------------------------------
select throws_ok(
  $$update public.profiles set preferred_language = 'de' where id = '11111111-1111-1111-1111-111111111111'$$,
  '23514', null, 'unsupported language is rejected'
);
select throws_ok(
  $$update public.profiles set preferred_language = null where id = '11111111-1111-1111-1111-111111111111'$$,
  '23502', null, 'NULL language is rejected'
);
select throws_ok(
  $$update public.profiles set display_name = '' where id = '11111111-1111-1111-1111-111111111111'$$,
  '23514', null, 'empty display name is rejected'
);
select throws_ok(
  $$update public.profiles set display_name = repeat('x', 101) where id = '11111111-1111-1111-1111-111111111111'$$,
  '23514', null, 'display name longer than 100 characters is rejected'
);
select lives_ok(
  $$update public.profiles set display_name = repeat('x', 100), preferred_language = 'pl'
    where id = '11111111-1111-1111-1111-111111111111'$$,
  'boundary values are accepted'
);
select lives_ok(
  $$update public.profiles set display_name = null where id = '11111111-1111-1111-1111-111111111111'$$,
  'NULL display name is accepted'
);

-- updated_at trigger (now() is frozen inside a transaction, so start from the past) --
update public.profiles
set created_at = '2020-01-01', updated_at = '2020-01-01'
where id = '11111111-1111-1111-1111-111111111111';
update public.profiles set display_name = 'Touched'
where id = '11111111-1111-1111-1111-111111111111';
select ok(
  (select updated_at > '2020-01-01' and created_at = '2020-01-01'
   from public.profiles where id = '11111111-1111-1111-1111-111111111111'),
  'UPDATE refreshes updated_at and leaves created_at alone'
);

-- Two updates in ONE transaction must produce two different updated_at values
-- (fails if the trigger used now(), which is frozen for the whole transaction).
create temp table ts_probe as
select updated_at as t0 from public.profiles where id = '11111111-1111-1111-1111-111111111111';
update public.profiles set display_name = 'Again' where id = '11111111-1111-1111-1111-111111111111';
select ok(
  (select p.updated_at > t.t0 from public.profiles p, ts_probe t
   where p.id = '11111111-1111-1111-1111-111111111111'),
  'a second UPDATE in the same transaction moves updated_at forward'
);
drop table ts_probe;

-- Functions are not callable by API roles -------------------------------------------
select ok(
  not has_function_privilege('anon', 'public.handle_new_user()', 'execute')
  and not has_function_privilege('authenticated', 'public.handle_new_user()', 'execute')
  and not has_function_privilege('skillloop_api', 'public.handle_new_user()', 'execute'),
  'handle_new_user() is not executable by API roles'
);
select ok(
  not has_function_privilege('anon', 'public.set_updated_at()', 'execute')
  and not has_function_privilege('authenticated', 'public.set_updated_at()', 'execute')
  and not has_function_privilege('skillloop_api', 'public.set_updated_at()', 'execute'),
  'set_updated_at() is not executable by API roles'
);
select ok(
  (select prosecdef from pg_proc where oid = 'public.handle_new_user()'::regprocedure),
  'handle_new_user() is SECURITY DEFINER'
);

-- Authenticated user A ----------------------------------------------------------------
set local role authenticated;
select set_config('request.jwt.claims',
  '{"sub":"11111111-1111-1111-1111-111111111111","role":"authenticated"}', true);

select is((select count(*)::int from public.profiles), 1, 'A sees exactly one profile');
select is(
  (select id from public.profiles), '11111111-1111-1111-1111-111111111111'::uuid,
  'A sees its own profile'
);
select lives_ok(
  $$update public.profiles set display_name = 'Hacked' where id = '22222222-2222-2222-2222-222222222222'$$,
  'A updating the profile of B is a silent no-op (checked below)'
);
select lives_ok(
  $$update public.profiles set display_name = 'Alice', preferred_language = 'pl'
    where id = '11111111-1111-1111-1111-111111111111'$$,
  'A can update display_name and preferred_language'
);
select is(
  (select display_name || '/' || preferred_language from public.profiles),
  'Alice/pl',
  'the update is visible to A'
);
select throws_ok(
  $$update public.profiles set id = '33333333-3333-3333-3333-333333333333'$$,
  '42501', null, 'A cannot change id'
);
select throws_ok(
  $$update public.profiles set created_at = now()$$,
  '42501', null, 'A cannot change created_at'
);
select throws_ok(
  $$update public.profiles set updated_at = now()$$,
  '42501', null, 'A cannot change updated_at'
);
select throws_ok(
  $$insert into public.profiles (id) values ('33333333-3333-3333-3333-333333333333')$$,
  '42501', null, 'A cannot insert profiles'
);
select throws_ok(
  $$delete from public.profiles$$,
  '42501', null, 'A cannot delete profiles'
);

-- No identity at all ----------------------------------------------------------------------
select set_config('request.jwt.claims', '', true);
select is((select count(*)::int from public.profiles), 0, 'without claims no profile is visible');

-- Anonymous ----------------------------------------------------------------------------------
reset role;
set local role anon;
select throws_ok(
  $$select count(*) from public.profiles$$,
  '42501', null, 'anon has no access to profiles'
);

-- B untouched ------------------------------------------------------------------------------------
reset role;
select is(
  (select display_name from public.profiles where id = '22222222-2222-2222-2222-222222222222'),
  null,
  'the profile of B was not modified by A'
);

-- Cascade ------------------------------------------------------------------------------------
reset role;
delete from auth.users where id = '11111111-1111-1111-1111-111111111111';
select is(
  (select count(*)::int from public.profiles where id = '11111111-1111-1111-1111-111111111111'),
  0,
  'deleting the auth user deletes the profile'
);

select * from finish();
rollback;
