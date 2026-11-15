-- Tests for public.career_goals: structure, constraints, RLS, D13 (archived is immutable).
-- Runs as postgres in one rolled-back transaction; user paths use SET LOCAL ROLE plus
-- request.jwt.claims. Concurrency cannot be tested in a single session (see backend tests).

begin;
select plan(49);

-- Structure -------------------------------------------------------------------------
select has_table('public', 'career_goals', 'career_goals exists');
select columns_are(
  'public', 'career_goals',
  array['id', 'user_id', 'target_role', 'seniority', 'description', 'status', 'created_at', 'updated_at'],
  'career_goals has exactly the documented columns'
);
select col_is_pk('public', 'career_goals', 'id', 'id is the primary key');
select col_default_is('public', 'career_goals', 'status', 'active', 'status defaults to active');
select ok(
  exists (
    select 1 from pg_constraint
    where conrelid = 'public.career_goals'::regclass and contype = 'f'
      and confrelid = 'auth.users'::regclass and confdeltype = 'c'
  ),
  'user_id references auth.users ON DELETE CASCADE'
);
select ok(
  (select relrowsecurity from pg_class where oid = 'public.career_goals'::regclass),
  'RLS is enabled'
);
select ok(
  (select indexdef from pg_indexes where schemaname = 'public' and indexname = 'one_active_goal_per_user')
    like 'CREATE UNIQUE INDEX one_active_goal_per_user ON public.career_goals USING btree (user_id) WHERE (status = ''active''::text)',
  'partial unique index one_active_goal_per_user exists'
);
select ok(
  (select indexdef from pg_indexes where schemaname = 'public' and indexname = 'career_goals_user_id_status_idx')
    like '%USING btree (user_id, status)',
  'index on (user_id, status) exists'
);

-- Privileges -------------------------------------------------------------------------
select ok(
  has_table_privilege('authenticated', 'public.career_goals', 'select')
  and not has_table_privilege('authenticated', 'public.career_goals', 'delete')
  and not has_table_privilege('authenticated', 'public.career_goals', 'truncate')
  and not has_table_privilege('authenticated', 'public.career_goals', 'update')
  and not has_table_privilege('authenticated', 'public.career_goals', 'insert')
  and not has_table_privilege('anon', 'public.career_goals', 'select'),
  'no table-wide write privileges, no delete, anon has nothing'
);
select ok(
  has_column_privilege('authenticated', 'public.career_goals', 'target_role', 'insert')
  and has_column_privilege('authenticated', 'public.career_goals', 'user_id', 'insert')
  and not has_column_privilege('authenticated', 'public.career_goals', 'status', 'insert')
  and not has_column_privilege('authenticated', 'public.career_goals', 'id', 'insert'),
  'insert is allowed on user_id/target_role/seniority/description only (not status, id)'
);
select ok(
  has_column_privilege('authenticated', 'public.career_goals', 'status', 'update')
  and has_column_privilege('authenticated', 'public.career_goals', 'description', 'update')
  and not has_column_privilege('authenticated', 'public.career_goals', 'user_id', 'update')
  and not has_column_privilege('authenticated', 'public.career_goals', 'id', 'update')
  and not has_column_privilege('authenticated', 'public.career_goals', 'created_at', 'update')
  and not has_column_privilege('authenticated', 'public.career_goals', 'updated_at', 'update'),
  'update is allowed on target_role/seniority/description/status only'
);

-- Fixtures -----------------------------------------------------------------------------------
insert into auth.users (id, email) values
  ('11111111-1111-1111-1111-111111111111', 'a@example.test'),
  ('22222222-2222-2222-2222-222222222222', 'b@example.test');

-- CHECK constraints (as postgres) --------------------------------------------------------
select throws_ok(
  $$insert into public.career_goals (user_id, target_role, seniority)
    values ('11111111-1111-1111-1111-111111111111', 'DevOps', 'expert')$$,
  '23514', null, 'unsupported seniority is rejected'
);
select throws_ok(
  $$insert into public.career_goals (user_id, target_role, seniority, status)
    values ('11111111-1111-1111-1111-111111111111', 'DevOps', 'mid', 'deleted')$$,
  '23514', null, 'unsupported status is rejected'
);
select throws_ok(
  $$insert into public.career_goals (user_id, target_role, seniority)
    values ('11111111-1111-1111-1111-111111111111', '', 'mid')$$,
  '23514', null, 'empty target_role is rejected'
);
select throws_ok(
  $$insert into public.career_goals (user_id, target_role, seniority)
    values ('11111111-1111-1111-1111-111111111111', '   ', 'mid')$$,
  '23514', null, 'whitespace-only target_role is rejected'
);
select throws_ok(
  $$insert into public.career_goals (user_id, target_role, seniority)
    values ('11111111-1111-1111-1111-111111111111', repeat('x', 101), 'mid')$$,
  '23514', null, 'target_role longer than 100 characters is rejected'
);
select throws_ok(
  $$insert into public.career_goals (user_id, target_role, seniority, description)
    values ('11111111-1111-1111-1111-111111111111', 'DevOps', 'mid', repeat('x', 2001))$$,
  '23514', null, 'description longer than 2000 characters is rejected'
);
select throws_ok(
  $$insert into public.career_goals (user_id, target_role, seniority)
    values ('11111111-1111-1111-1111-111111111111', null, 'mid')$$,
  '23502', null, 'NULL target_role is rejected'
);
select throws_ok(
  $$insert into public.career_goals (user_id, target_role, seniority)
    values ('11111111-1111-1111-1111-111111111111', 'DevOps', null)$$,
  '23502', null, 'NULL seniority is rejected'
);
select lives_ok(
  $$insert into public.career_goals (user_id, target_role, seniority, description, status)
    values ('22222222-2222-2222-2222-222222222222', repeat('x', 100), 'senior', repeat('x', 2000), 'archived')$$,
  'boundary values are accepted (and an archived row for B is prepared)'
);

-- Authenticated user A ----------------------------------------------------------------------
set local role authenticated;
select set_config('request.jwt.claims',
  '{"sub":"11111111-1111-1111-1111-111111111111","role":"authenticated"}', true);

select lives_ok(
  $$insert into public.career_goals (user_id, target_role, seniority, description)
    values ('11111111-1111-1111-1111-111111111111', 'DevOps Engineer', 'junior', 'Goal A1')$$,
  'A creates a goal'
);
select is(
  (select status from public.career_goals where target_role = 'DevOps Engineer'),
  'active', 'the new goal is active'
);
select throws_ok(
  $$insert into public.career_goals (user_id, target_role, seniority)
    values ('11111111-1111-1111-1111-111111111111', 'Second', 'mid')$$,
  '23505', null, 'a second active goal for A is rejected (unique violation)'
);
select throws_ok(
  $$insert into public.career_goals (user_id, target_role, seniority)
    values ('22222222-2222-2222-2222-222222222222', 'Spoof', 'mid')$$,
  '42501', null, 'A cannot create a goal owned by B'
);
select throws_ok(
  $$insert into public.career_goals (user_id, target_role, seniority, status)
    values ('11111111-1111-1111-1111-111111111111', 'Archived at birth', 'mid', 'archived')$$,
  '42501', null, 'A cannot insert an archived goal (no grant on status)'
);
-- The insert policy must hold even if the grant on status were widened later.
reset role;
grant insert (status) on public.career_goals to authenticated;
set local role authenticated;
select throws_ok(
  $$insert into public.career_goals (user_id, target_role, seniority, status)
    values ('11111111-1111-1111-1111-111111111111', 'Archived at birth', 'mid', 'archived')$$,
  '42501', null, 'the insert policy alone rejects an archived goal (grant widened)'
);
reset role;
revoke insert (status) on public.career_goals from authenticated;
set local role authenticated;

select is(
  (select count(*)::int from public.career_goals), 1,
  'A sees only its own goal (the archived goal of B is invisible)'
);
select lives_ok(
  $$update public.career_goals set target_role = 'Platform Engineer', seniority = 'mid',
      description = 'Edited' where target_role = 'DevOps Engineer'$$,
  'A edits an active goal'
);
select is(
  (select target_role || '/' || seniority || '/' || description from public.career_goals),
  'Platform Engineer/mid/Edited', 'the edit is visible'
);
select throws_ok(
  $$update public.career_goals set user_id = '22222222-2222-2222-2222-222222222222'$$,
  '42501', null, 'A cannot reassign the goal to B (user_id)'
);
select throws_ok($$update public.career_goals set id = gen_random_uuid()$$,
  '42501', null, 'A cannot change id');
select throws_ok($$update public.career_goals set created_at = now()$$,
  '42501', null, 'A cannot change created_at');
select throws_ok($$update public.career_goals set updated_at = now()$$,
  '42501', null, 'A cannot change updated_at');
select throws_ok($$delete from public.career_goals$$,
  '42501', null, 'A cannot delete goals');

-- Archive, then D13 ---------------------------------------------------------------------------
select lives_ok(
  $$update public.career_goals set status = 'archived' where target_role = 'Platform Engineer'$$,
  'A archives its active goal (active -> archived)'
);
select lives_ok(
  $$insert into public.career_goals (user_id, target_role, seniority)
    values ('11111111-1111-1111-1111-111111111111', 'Second goal', 'senior')$$,
  'after archiving, A can create a new active goal'
);
select is(
  (select count(*)::int from public.career_goals where status = 'archived'), 1,
  'the archived goal is still visible (history is kept)'
);
select lives_ok(
  $$update public.career_goals set description = 'Tampered', target_role = 'Tampered'
    where status = 'archived'$$,
  'editing an archived goal is a silent no-op (checked below)'
);
select lives_ok(
  $$update public.career_goals set status = 'active' where target_role = 'Platform Engineer'$$,
  'restoring an archived goal is a silent no-op (checked below); no 23505 either'
);
select lives_ok(
  $$update public.career_goals set status = 'archived' where target_role = 'Second goal'$$,
  'A archives the second goal too (two archived goals can coexist)'
);

-- No identity at all --------------------------------------------------------------------------
select set_config('request.jwt.claims', '', true);
select is((select count(*)::int from public.career_goals), 0, 'without claims no goal is visible');

-- Authenticated user B (isolation) -----------------------------------------------------------
select set_config('request.jwt.claims',
  '{"sub":"22222222-2222-2222-2222-222222222222","role":"authenticated"}', true);
select is((select count(*)::int from public.career_goals), 1, 'B sees only its own (archived) goal');
select lives_ok(
  $$update public.career_goals set status = 'archived', target_role = 'Hacked'$$,
  'B updating goals of A is a no-op'
);
select lives_ok(
  $$insert into public.career_goals (user_id, target_role, seniority)
    values ('22222222-2222-2222-2222-222222222222', 'B goal', 'junior')$$,
  'B creates its own active goal independently of A'
);

-- Anonymous -----------------------------------------------------------------------------------
reset role;
set local role anon;
select throws_ok($$select count(*) from public.career_goals$$,
  '42501', null, 'anon has no access to career_goals');

-- Verify as postgres that nothing was changed by the no-op updates ------------------------------
reset role;
select is(
  (select target_role || '/' || coalesce(description, '') || '/' || status
   from public.career_goals where user_id = '11111111-1111-1111-1111-111111111111'
     and target_role = 'Platform Engineer'),
  'Platform Engineer/Edited/archived',
  'the archived goal was neither edited nor restored'
);
select is(
  (select count(*)::int from public.career_goals
   where user_id = '22222222-2222-2222-2222-222222222222' and target_role = 'Hacked'),
  0, 'B did not modify any goal of A (or its own archived one)'
);

-- updated_at (two updates in one transaction must differ) ---------------------------------------
create temp table ts_probe as
select updated_at as t0 from public.career_goals where target_role = 'B goal';
update public.career_goals set description = 'Again' where target_role = 'B goal';
select ok(
  (select g.updated_at > t.t0 from public.career_goals g, ts_probe t where g.target_role = 'B goal'),
  'UPDATE moves updated_at forward within the same transaction'
);
drop table ts_probe;

-- Cascade -----------------------------------------------------------------------------------------
delete from auth.users where id = '11111111-1111-1111-1111-111111111111';
select is(
  (select count(*)::int from public.career_goals where user_id = '11111111-1111-1111-1111-111111111111'),
  0, 'deleting the user deletes all its goals, including archived ones'
);

select * from finish();
rollback;
