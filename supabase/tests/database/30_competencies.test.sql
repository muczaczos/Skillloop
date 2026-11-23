-- pgTAP tests for public.competencies (docs/DATABASE-DESIGN.md, sections 4.3, 4.3.1, 4.8, 6.2.1).
-- Stage 9.1a. Every data operation of a user runs as "authenticated" with JWT claims, exactly like
-- the backend does. The fixtures (as postgres) create users and career goals; the only competency
-- written as postgres is the one of user C, and it follows contract L2 (goal locked first), after
-- which the goal of C is archived. All other competencies are created inside the tests.
-- Not covered here (needs two connections, Stage 9.1b): waiting on a goal lock, deadlock freedom,
-- re-evaluation of RLS after a lock wait, the lock mode (FOR SHARE NOWAIT must fail and
-- FOR KEY SHARE NOWAIT must succeed against a held goal lock), and a multi-row INSERT that spans
-- an active and an archived goal (expected: the whole statement is refused).
begin;

select plan(112);

-- Helper: runs a statement as the current role and returns the violated constraint / index name
-- (or the SQLSTATE when there is none, or 'no error'). The statement runs in a sub-transaction,
-- so a failed statement leaves no trace.
create function pg_temp.constraint_of(p_sql text) returns text
language plpgsql as $$
declare v_name text;
begin
  execute p_sql;
  return 'no error';
exception when others then
  get stacked diagnostics v_name = constraint_name;
  return coalesce(nullif(v_name, ''), sqlstate);
end;
$$;

-- Helper: runs a data-changing statement as the current role and returns the number of rows it
-- changed. A data-modifying WITH is not allowed inside a sub-select, so assertions cannot count
-- "update ... returning" rows themselves. An error is not caught: it fails the assertion.
create function pg_temp.rows_affected(p_sql text) returns integer
language plpgsql as $$
declare v_rows integer;
begin
  execute p_sql;
  get diagnostics v_rows = row_count;
  return v_rows;
end;
$$;

-- Fixtures (as postgres) -------------------------------------------------------------------
-- A: active goal GA + archived goal GA0.   B: active goal GB (isolation).
-- C: only an archived goal GC0 holding one competency (frozen map, no active goal).
-- D: multi-row tests.  E, F: statement-trigger lock probes.  H: privileged-role tests.
insert into auth.users (id, email) values
  ('11111111-1111-1111-1111-111111111111', 'a@example.test'),
  ('22222222-2222-2222-2222-222222222222', 'b@example.test'),
  ('33333333-3333-3333-3333-333333333333', 'c@example.test'),
  ('44444444-4444-4444-4444-444444444444', 'd@example.test'),
  ('55555555-5555-5555-5555-555555555555', 'e@example.test'),
  ('66666666-6666-6666-6666-666666666666', 'f@example.test'),
  ('77777777-7777-7777-7777-777777777777', 'h@example.test');

insert into public.career_goals (id, user_id, target_role, seniority, status) values
  ('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa', '11111111-1111-1111-1111-111111111111', 'DevOps', 'mid', 'active'),
  ('a0a0a0a0-a0a0-a0a0-a0a0-a0a0a0a0a0a0', '11111111-1111-1111-1111-111111111111', 'Old goal', 'mid', 'archived'),
  ('bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb', '22222222-2222-2222-2222-222222222222', 'Backend', 'senior', 'active'),
  ('c0c0c0c0-c0c0-c0c0-c0c0-c0c0c0c0c0c0', '33333333-3333-3333-3333-333333333333', 'Frozen', 'junior', 'active'),
  ('dddddddd-dddd-dddd-dddd-dddddddddddd', '44444444-4444-4444-4444-444444444444', 'Data', 'mid', 'active'),
  ('eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee', '55555555-5555-5555-5555-555555555555', 'QA', 'mid', 'active'),
  ('ffffffff-ffff-ffff-ffff-ffffffffffff', '66666666-6666-6666-6666-666666666666', 'SRE', 'mid', 'active'),
  ('99999999-9999-9999-9999-999999999999', '77777777-7777-7777-7777-777777777777', 'Security', 'mid', 'active');

-- C's competency is written as postgres following contract L2 (goal locked first), then the goal
-- is archived, which freezes the map.
select 1 from public.career_goals where id = 'c0c0c0c0-c0c0-c0c0-c0c0-c0c0c0c0c0c0' for no key update;
insert into public.competencies (career_goal_id, user_id, name, knowledge_type, importance, expected_level)
values ('c0c0c0c0-c0c0-c0c0-c0c0-c0c0c0c0c0c0', '33333333-3333-3333-3333-333333333333',
        'Frozen skill', 'memory', 'high', 'beginner');
update public.career_goals set status = 'archived' where id = 'c0c0c0c0-c0c0-c0c0-c0c0-c0c0c0c0c0c0';

-- Structure --------------------------------------------------------------------------------
select has_table('public', 'competencies', 'competencies table exists');
select columns_are('public', 'competencies',
  array['id', 'career_goal_id', 'user_id', 'name', 'description', 'rationale', 'category',
        'knowledge_type', 'importance', 'expected_level', 'display_order', 'status',
        'created_at', 'updated_at'],
  'competencies has exactly the approved columns (no current_level, source, expectations)');
select col_is_pk('public', 'competencies', 'id', 'id is the primary key');
select is(
  (select column_default from information_schema.columns
   where table_schema = 'public' and table_name = 'competencies' and column_name = 'status'),
  '''active''::text', 'status defaults to active');
select is(
  (select column_default from information_schema.columns
   where table_schema = 'public' and table_name = 'competencies' and column_name = 'display_order'),
  '0', 'display_order defaults to 0');
select ok(
  exists (select 1 from pg_constraint
          where conname = 'competencies_goal_owner_fkey' and contype = 'f'
            and conrelid = 'public.competencies'::regclass
            and confrelid = 'public.career_goals'::regclass
            and confdeltype = 'c'
            and (select array_agg(attname::text order by a.attnum) from pg_attribute a
                 where a.attrelid = conrelid and a.attnum = any (conkey))
                = array['career_goal_id', 'user_id']),
  'composite FK (career_goal_id, user_id) -> career_goals ON DELETE CASCADE');
select ok(
  exists (select 1 from pg_constraint
          where conname = 'career_goals_id_user_id_key' and contype = 'u'
            and conrelid = 'public.career_goals'::regclass),
  'career_goals has UNIQUE (id, user_id)');
select ok(
  (select indexdef from pg_indexes
   where schemaname = 'public' and indexname = 'competencies_active_name_unique')
    like 'CREATE UNIQUE INDEX%(career_goal_id, lower(btrim(name)))%WHERE (status = ''active''::text)',
  'active names are unique per goal (case- and whitespace-insensitive, partial index)');
select ok(
  (select indexdef from pg_indexes
   where schemaname = 'public' and indexname = 'competencies_goal_status_order_idx')
    like '%(career_goal_id, status, display_order)',
  'listing index on (career_goal_id, status, display_order)');
select ok(
  (select relrowsecurity from pg_class where oid = 'public.competencies'::regclass),
  'RLS is enabled on competencies');
select policies_are('public', 'competencies',
  array['competencies_select_own', 'competencies_insert_own', 'competencies_update_own'],
  'competencies has exactly select/insert/update policies (no delete policy)');
select triggers_are('public', 'competencies',
  array['competencies_set_updated_at', 'competencies_lock_goal', 'competencies_enforce_goal_rules'],
  'competencies has exactly the three approved triggers');
select is(
  (select tgtype::int from pg_trigger
   where tgrelid = 'public.competencies'::regclass and tgname = 'competencies_lock_goal'),
  22, 'competencies_lock_goal is BEFORE INSERT OR UPDATE FOR EACH STATEMENT');
select is(
  (select tgtype::int from pg_trigger
   where tgrelid = 'public.competencies'::regclass and tgname = 'competencies_enforce_goal_rules'),
  23, 'competencies_enforce_goal_rules is BEFORE INSERT OR UPDATE FOR EACH ROW');
select ok(
  (select bool_and(tgenabled = 'O') from pg_trigger
   where tgrelid = 'public.competencies'::regclass and not tgisinternal),
  'all competency triggers are enabled');
select ok(
  (select provolatile = 'v' and not prosecdef from pg_proc
   where oid = 'public.competencies_lock_active_goal()'::regprocedure),
  'competencies_lock_active_goal is VOLATILE and SECURITY INVOKER');
select is(
  (select proconfig from pg_proc where oid = 'public.competencies_lock_active_goal()'::regprocedure),
  array['search_path=""'], 'competencies_lock_active_goal has an empty search_path');
select ok(
  (select provolatile = 'v' and not prosecdef from pg_proc
   where oid = 'public.competencies_enforce_goal_rules()'::regprocedure),
  'competencies_enforce_goal_rules is VOLATILE and SECURITY INVOKER');
select is(
  (select proconfig from pg_proc where oid = 'public.competencies_enforce_goal_rules()'::regprocedure),
  array['search_path=""'], 'competencies_enforce_goal_rules has an empty search_path');
select ok(
  not has_function_privilege('authenticated', 'public.competencies_lock_active_goal()', 'execute')
  and not has_function_privilege('anon', 'public.competencies_lock_active_goal()', 'execute')
  and not has_function_privilege('skillloop_api', 'public.competencies_lock_active_goal()', 'execute')
  and not has_function_privilege('authenticated', 'public.competencies_enforce_goal_rules()', 'execute')
  and not has_function_privilege('anon', 'public.competencies_enforce_goal_rules()', 'execute')
  and not has_function_privilege('skillloop_api', 'public.competencies_enforce_goal_rules()', 'execute'),
  'no API role can execute the trigger functions directly');

-- Grants -----------------------------------------------------------------------------------
select ok(
  has_table_privilege('authenticated', 'public.competencies', 'select')
  and not has_table_privilege('authenticated', 'public.competencies', 'delete')
  and not has_table_privilege('authenticated', 'public.competencies', 'truncate'),
  'authenticated can select but cannot delete or truncate');
select ok(
  not has_table_privilege('anon', 'public.competencies', 'select')
  and not has_table_privilege('anon', 'public.competencies', 'insert')
  and not has_table_privilege('anon', 'public.competencies', 'update')
  and not has_table_privilege('skillloop_api', 'public.competencies', 'select'),
  'anon and skillloop_api have no table privileges');
select ok(
  (select bool_and(has_column_privilege('authenticated', 'public.competencies', c, 'insert'))
   from unnest(array['career_goal_id', 'user_id', 'name', 'description', 'rationale', 'category',
                     'knowledge_type', 'importance', 'expected_level', 'display_order']) c),
  'insert is allowed on the ten approved columns');
select ok(
  (select not bool_or(has_column_privilege('authenticated', 'public.competencies', c, 'insert'))
   from unnest(array['id', 'status', 'created_at', 'updated_at']) c),
  'insert is not allowed on id, status, created_at, updated_at');
select ok(
  (select bool_and(has_column_privilege('authenticated', 'public.competencies', c, 'update'))
   from unnest(array['name', 'description', 'rationale', 'category', 'knowledge_type',
                     'importance', 'expected_level', 'display_order', 'status']) c),
  'update is allowed on the nine approved columns');
select ok(
  (select not bool_or(has_column_privilege('authenticated', 'public.competencies', c, 'update'))
   from unnest(array['id', 'career_goal_id', 'user_id', 'created_at', 'updated_at']) c),
  'update is not allowed on id, career_goal_id, user_id, created_at, updated_at');
select ok(
  has_table_privilege('authenticated', 'public.career_goals', 'select')
  and has_column_privilege('authenticated', 'public.career_goals', 'status', 'update')
  and has_column_privilege('authenticated', 'public.career_goals', 'user_id', 'insert')
  and not has_column_privilege('authenticated', 'public.career_goals', 'id', 'update')
  and not has_table_privilege('authenticated', 'public.career_goals', 'delete'),
  'career_goals grants are unchanged');

-- User A: lock capability on career_goals ----------------------------------------------------
set local role authenticated;
select set_config('request.jwt.claims',
  '{"sub":"11111111-1111-1111-1111-111111111111","role":"authenticated"}', true);

select is(
  (select count(*)::int from (
     select 1 from public.career_goals g
     where g.id = 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa' for no key update) s),
  1, 'authenticated can lock its own active goal');
select is(
  (select count(*)::int from (
     select 1 from public.career_goals g
     where g.id = 'a0a0a0a0-a0a0-a0a0-a0a0-a0a0a0a0a0a0' for no key update) s),
  0, 'an own archived goal cannot be locked (UPDATE policy)');
select is(
  (select count(*)::int from (
     select 1 from public.career_goals g
     where g.id = 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb' for no key update) s),
  0, 'a foreign goal cannot be locked');

-- INSERT: goal rules -------------------------------------------------------------------------
select lives_ok(
  $$insert into public.competencies (career_goal_id, user_id, name, knowledge_type, importance, expected_level)
    values ('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa', '11111111-1111-1111-1111-111111111111',
            'Docker', 'memory', 'high', 'beginner')$$,
  'insert into the own active goal works');
select is(
  pg_temp.constraint_of($$insert into public.competencies (career_goal_id, user_id, name, knowledge_type, importance, expected_level)
    values ('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa', '22222222-2222-2222-2222-222222222222',
            'Wrong owner', 'memory', 'high', 'beginner')$$),
  'competencies_goal_not_active', 'claiming another user_id for the own goal is refused');
select is(
  pg_temp.constraint_of($$insert into public.competencies (career_goal_id, user_id, name, knowledge_type, importance, expected_level)
    values ('bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb', '11111111-1111-1111-1111-111111111111',
            'Foreign goal', 'memory', 'high', 'beginner')$$),
  'competencies_goal_not_active', 'insert into a foreign goal is refused');
select is(
  pg_temp.constraint_of($$insert into public.competencies (career_goal_id, user_id, name, knowledge_type, importance, expected_level)
    values ('bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb', '22222222-2222-2222-2222-222222222222',
            'Impersonation', 'memory', 'high', 'beginner')$$),
  'competencies_goal_not_active', 'insert as another user into their goal is refused');
select is(
  pg_temp.constraint_of($$insert into public.competencies (career_goal_id, user_id, name, knowledge_type, importance, expected_level)
    values ('a0a0a0a0-a0a0-a0a0-a0a0-a0a0a0a0a0a0', '11111111-1111-1111-1111-111111111111',
            'Archived goal', 'memory', 'high', 'beginner')$$),
  'competencies_goal_not_active', 'insert into an own archived goal is refused (23514, not 42501)');

-- With the row trigger disabled (as postgres, rolled back with the test) the policy alone refuses.
reset role;
alter table public.competencies disable trigger competencies_enforce_goal_rules;
set local role authenticated;
select is(
  pg_temp.constraint_of($$insert into public.competencies (career_goal_id, user_id, name, knowledge_type, importance, expected_level)
    values ('a0a0a0a0-a0a0-a0a0-a0a0-a0a0a0a0a0a0', '11111111-1111-1111-1111-111111111111',
            'Policy only', 'memory', 'high', 'beginner')$$),
  '42501', 'without the trigger the INSERT policy refuses an archived goal');
select is(
  pg_temp.constraint_of($$insert into public.competencies (career_goal_id, user_id, name, knowledge_type, importance, expected_level)
    values ('bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb', '11111111-1111-1111-1111-111111111111',
            'Policy only', 'memory', 'high', 'beginner')$$),
  '42501', 'without the trigger the INSERT policy refuses a foreign goal');
reset role;
alter table public.competencies enable trigger competencies_enforce_goal_rules;
select ok(
  (select tgenabled = 'O' from pg_trigger
   where tgrelid = 'public.competencies'::regclass and tgname = 'competencies_enforce_goal_rules'),
  'the row trigger is enabled again');
set local role authenticated;
select set_config('request.jwt.claims',
  '{"sub":"11111111-1111-1111-1111-111111111111","role":"authenticated"}', true);

select is(
  pg_temp.constraint_of($$insert into public.competencies (career_goal_id, user_id, name, knowledge_type, importance, expected_level, status)
    values ('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa', '11111111-1111-1111-1111-111111111111',
            'Born archived', 'memory', 'high', 'beginner', 'archived')$$),
  '42501', 'status cannot be set on insert (no column grant)');

-- RLS: own vs foreign ------------------------------------------------------------------------
select is((select count(*)::int from public.competencies), 1, 'A sees only the own competency');

set local role authenticated;
select set_config('request.jwt.claims',
  '{"sub":"22222222-2222-2222-2222-222222222222","role":"authenticated"}', true);
select lives_ok(
  $$insert into public.competencies (career_goal_id, user_id, name, knowledge_type, importance, expected_level)
    values ('bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb', '22222222-2222-2222-2222-222222222222',
            'Docker', 'memory', 'high', 'beginner')$$,
  'the same name is allowed in another goal of another user');
select is((select count(*)::int from public.competencies where career_goal_id = 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa'),
  0, 'B does not see competencies of A');
select is(
  pg_temp.rows_affected($$update public.competencies set description = 'hacked'
              where career_goal_id = 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa'$$),
  0, 'B cannot update competencies of A');
select throws_ok($$delete from public.competencies$$, '42501', null,
  'authenticated cannot delete competencies');

set local role anon;
select throws_ok($$select count(*) from public.competencies$$, '42501', null,
  'anon cannot read competencies');

-- Back to A: constraints ---------------------------------------------------------------------
set local role authenticated;
select set_config('request.jwt.claims',
  '{"sub":"11111111-1111-1111-1111-111111111111","role":"authenticated"}', true);

select is(pg_temp.constraint_of($$insert into public.competencies (career_goal_id, user_id, name, knowledge_type, importance, expected_level)
  values ('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa', '11111111-1111-1111-1111-111111111111', '', 'memory', 'high', 'beginner')$$),
  'competencies_name_check', 'empty name is rejected');
select is(pg_temp.constraint_of($$insert into public.competencies (career_goal_id, user_id, name, knowledge_type, importance, expected_level)
  values ('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa', '11111111-1111-1111-1111-111111111111', '   ', 'memory', 'high', 'beginner')$$),
  'competencies_name_check', 'whitespace-only name is rejected');
select is(pg_temp.constraint_of($$insert into public.competencies (career_goal_id, user_id, name, knowledge_type, importance, expected_level)
  values ('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa', '11111111-1111-1111-1111-111111111111', repeat('n', 101), 'memory', 'high', 'beginner')$$),
  'competencies_name_check', 'a name of 101 characters is rejected');
select lives_ok($$insert into public.competencies (career_goal_id, user_id, name, knowledge_type, importance, expected_level)
  values ('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa', '11111111-1111-1111-1111-111111111111', repeat('n', 100), 'memory', 'high', 'beginner')$$,
  'a name of 100 characters is accepted');
select is(pg_temp.constraint_of($$insert into public.competencies (career_goal_id, user_id, name, description, knowledge_type, importance, expected_level)
  values ('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa', '11111111-1111-1111-1111-111111111111', 'Long description', repeat('d', 1001), 'memory', 'high', 'beginner')$$),
  'competencies_description_check', 'a description of 1001 characters is rejected');
select lives_ok($$insert into public.competencies (career_goal_id, user_id, name, description, knowledge_type, importance, expected_level)
  values ('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa', '11111111-1111-1111-1111-111111111111', 'Max description', repeat('d', 1000), 'memory', 'high', 'beginner')$$,
  'a description of 1000 characters is accepted');
select is(pg_temp.constraint_of($$insert into public.competencies (career_goal_id, user_id, name, rationale, knowledge_type, importance, expected_level)
  values ('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa', '11111111-1111-1111-1111-111111111111', 'Long rationale', repeat('r', 1001), 'memory', 'high', 'beginner')$$),
  'competencies_rationale_check', 'a rationale of 1001 characters is rejected');
select is(pg_temp.constraint_of($$insert into public.competencies (career_goal_id, user_id, name, category, knowledge_type, importance, expected_level)
  values ('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa', '11111111-1111-1111-1111-111111111111', 'Long category', repeat('c', 61), 'memory', 'high', 'beginner')$$),
  'competencies_category_check', 'a category of 61 characters is rejected');
select lives_ok($$insert into public.competencies (career_goal_id, user_id, name, category, knowledge_type, importance, expected_level)
  values ('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa', '11111111-1111-1111-1111-111111111111', 'Max category', repeat('c', 60), 'memory', 'high', 'beginner')$$,
  'a category of 60 characters is accepted');
select is(pg_temp.constraint_of($$insert into public.competencies (career_goal_id, user_id, name, category, knowledge_type, importance, expected_level)
  values ('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa', '11111111-1111-1111-1111-111111111111', 'Blank category', '   ', 'memory', 'high', 'beginner')$$),
  'competencies_category_check', 'a blank category is rejected');
select is(pg_temp.constraint_of($$insert into public.competencies (career_goal_id, user_id, name, knowledge_type, importance, expected_level)
  values ('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa', '11111111-1111-1111-1111-111111111111', 'Bad type', 'skill', 'high', 'beginner')$$),
  'competencies_knowledge_type_check', 'an unsupported knowledge_type is rejected');
select is(pg_temp.constraint_of($$insert into public.competencies (career_goal_id, user_id, name, knowledge_type, importance, expected_level)
  values ('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa', '11111111-1111-1111-1111-111111111111', 'Bad importance', 'memory', 'urgent', 'beginner')$$),
  'competencies_importance_check', 'an unsupported importance is rejected');
select is(pg_temp.constraint_of($$insert into public.competencies (career_goal_id, user_id, name, knowledge_type, importance, expected_level)
  values ('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa', '11111111-1111-1111-1111-111111111111', 'Bad level', 'memory', 'high', 'expert')$$),
  'competencies_expected_level_check', 'an unsupported expected_level is rejected');
select is(pg_temp.constraint_of($$insert into public.competencies (career_goal_id, user_id, name, knowledge_type, importance, expected_level, display_order)
  values ('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa', '11111111-1111-1111-1111-111111111111', 'Bad order', 'memory', 'high', 'beginner', -1)$$),
  'competencies_display_order_check', 'a negative display_order is rejected');
select lives_ok($$insert into public.competencies (career_goal_id, user_id, name, knowledge_type, importance, expected_level)
  values
  ('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa', '11111111-1111-1111-1111-111111111111', 'KT application', 'application', 'critical', 'intermediate'),
  ('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa', '11111111-1111-1111-1111-111111111111', 'KT documentation', 'documentation', 'low', 'advanced')$$,
  'all enum values are accepted');
-- Active in GA now: Docker, n*100, Max description, Max category, KT application, KT documentation = 6.

-- Active-name uniqueness ---------------------------------------------------------------------
select is(pg_temp.constraint_of($$insert into public.competencies (career_goal_id, user_id, name, knowledge_type, importance, expected_level)
  values ('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa', '11111111-1111-1111-1111-111111111111', 'docker', 'memory', 'high', 'beginner')$$),
  'competencies_active_name_unique', 'an active name is unique ignoring case');
select is(pg_temp.constraint_of($$insert into public.competencies (career_goal_id, user_id, name, knowledge_type, importance, expected_level)
  values ('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa', '11111111-1111-1111-1111-111111111111', '  DOCKER ', 'memory', 'high', 'beginner')$$),
  'competencies_active_name_unique', 'an active name is unique ignoring surrounding whitespace');
select is(
  pg_temp.constraint_of($$update public.competencies set name = 'docker' where name = 'KT application'$$),
  'competencies_active_name_unique', 'renaming onto an existing active name is rejected');
select lives_ok(
  $$update public.competencies set status = 'archived' where name = 'Docker' and status = 'active'$$,
  'an active competency can be archived');
select lives_ok($$insert into public.competencies (career_goal_id, user_id, name, knowledge_type, importance, expected_level)
  values ('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa', '11111111-1111-1111-1111-111111111111', 'Docker', 'memory', 'high', 'beginner')$$,
  'an archived competency releases its name');

-- One-way archiving; immutable columns; updated_at -------------------------------------------
select is(
  pg_temp.rows_affected($$update public.competencies set name = 'Renamed archived'
              where name = 'Docker' and status = 'archived'$$),
  0, 'an archived competency cannot be edited (0 rows)');
select is(
  pg_temp.rows_affected($$update public.competencies set status = 'active'
              where status = 'archived'$$),
  0, 'an archived competency cannot be reactivated (0 rows)');
select is(pg_temp.constraint_of($$update public.competencies set status = 'deleted' where name = 'KT application'$$),
  'competencies_status_check', 'an unsupported status is rejected');
select is((select count(*)::int from public.competencies where status = 'archived'),
  1, 'an archived competency stays readable');
select is(pg_temp.constraint_of($$update public.competencies set career_goal_id = 'a0a0a0a0-a0a0-a0a0-a0a0-a0a0a0a0a0a0' where name = 'KT application'$$),
  '42501', 'a competency cannot be moved to another goal (no column grant)');
select is(pg_temp.constraint_of($$update public.competencies set user_id = '22222222-2222-2222-2222-222222222222' where name = 'KT application'$$),
  '42501', 'a competency cannot be given to another user (no column grant)');
update public.competencies set display_order = 5 where name = 'KT application';
select ok(
  (select updated_at > created_at from public.competencies where name = 'KT application'),
  'updated_at moves forward on update');

-- Limit of 50 active competencies (single-row statements) ----------------------------------------
select lives_ok($$insert into public.competencies (career_goal_id, user_id, name, knowledge_type, importance, expected_level)
  select 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa', '11111111-1111-1111-1111-111111111111', 'A comp ' || g, 'memory', 'low', 'beginner'
  from generate_series(1, 44) g$$,
  'a multi-row insert filling the goal to exactly 50 active competencies works');
select is((select count(*)::int from public.competencies where status = 'active'),
  50, 'the goal holds 50 active competencies');
select is(pg_temp.constraint_of($$insert into public.competencies (career_goal_id, user_id, name, knowledge_type, importance, expected_level)
  values ('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa', '11111111-1111-1111-1111-111111111111', 'The 51st', 'memory', 'high', 'beginner')$$),
  'competencies_active_limit', 'the 51st active competency is rejected');
select is(pg_temp.constraint_of($$insert into public.competencies (career_goal_id, user_id, name, knowledge_type, importance, expected_level)
  values ('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa', '11111111-1111-1111-1111-111111111111', 'docker', 'memory', 'high', 'beginner')$$),
  'competencies_active_limit', 'when both rules are broken the limit is reported first');
select lives_ok($$update public.competencies set status = 'archived' where name = 'A comp 1'$$,
  'archiving frees a slot');
select lives_ok($$insert into public.competencies (career_goal_id, user_id, name, knowledge_type, importance, expected_level)
  values ('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa', '11111111-1111-1111-1111-111111111111', 'Freed slot', 'memory', 'high', 'beginner')$$,
  'a freed slot can be used (archived competencies do not count)');
select is((select count(*)::int from public.competencies where status = 'active'),
  50, 'the goal is at the limit again');

-- User D: multi-row statements -----------------------------------------------------------------
set local role authenticated;
select set_config('request.jwt.claims',
  '{"sub":"44444444-4444-4444-4444-444444444444","role":"authenticated"}', true);

select lives_ok($$insert into public.competencies (career_goal_id, user_id, name, knowledge_type, importance, expected_level)
  select 'dddddddd-dddd-dddd-dddd-dddddddddddd', '44444444-4444-4444-4444-444444444444', 'D comp ' || g, 'memory', 'medium', 'beginner'
  from generate_series(1, 50) g$$,
  'one INSERT statement can create 50 competencies (the count sees rows of the same statement)');
select is((select count(*)::int from public.competencies where status = 'active'), 50, 'D has 50 active competencies');
select is(
  pg_temp.rows_affected($$update public.competencies set status = 'archived'
              where name in ('D comp 1', 'D comp 2', 'D comp 3')$$),
  3, 'one UPDATE statement can archive several competencies');
select is((select count(*)::int from public.competencies where status = 'active'), 47, 'archiving several frees several slots');
select is(pg_temp.constraint_of($$insert into public.competencies (career_goal_id, user_id, name, knowledge_type, importance, expected_level)
  select 'dddddddd-dddd-dddd-dddd-dddddddddddd', '44444444-4444-4444-4444-444444444444', 'E' || g, 'memory', 'low', 'beginner'
  from generate_series(1, 4) g$$),
  'competencies_active_limit', 'a statement creating the 51st competency is rejected');
select is((select count(*)::int from public.competencies where name like 'E_'),
  0, 'the rejected statement left no rows (all or nothing)');
select lives_ok($$insert into public.competencies (career_goal_id, user_id, name, knowledge_type, importance, expected_level)
  select 'dddddddd-dddd-dddd-dddd-dddddddddddd', '44444444-4444-4444-4444-444444444444', 'E' || g, 'memory', 'low', 'beginner'
  from generate_series(1, 3) g$$,
  'a statement filling exactly the remaining slots works');
update public.competencies set status = 'archived' where name in ('D comp 4', 'D comp 5');
select is(pg_temp.constraint_of($$insert into public.competencies (career_goal_id, user_id, name, knowledge_type, importance, expected_level)
  values ('dddddddd-dddd-dddd-dddd-dddddddddddd', '44444444-4444-4444-4444-444444444444', 'dup', 'memory', 'low', 'beginner'),
         ('dddddddd-dddd-dddd-dddd-dddddddddddd', '44444444-4444-4444-4444-444444444444', 'DUP', 'memory', 'low', 'beginner')$$),
  'competencies_active_name_unique', 'a duplicate name inside one statement fails the whole statement');
select is((select count(*)::int from public.competencies where lower(name) = 'dup'),
  0, 'no row of the failed duplicate statement remains');
select is(
  pg_temp.rows_affected($$update public.competencies set name = name || ' renamed'
              where name in ('D comp 10', 'D comp 11', 'D comp 12')$$),
  3, 'one UPDATE statement can rename several competencies');
select is(pg_temp.constraint_of($$update public.competencies
  set name = case name when 'D comp 20' then 'D comp 21' else 'D comp 20' end
  where name in ('D comp 20', 'D comp 21')$$),
  'competencies_active_name_unique', 'swapping two names in one statement is rejected');
select is((select count(*)::int from public.competencies where name in ('D comp 20', 'D comp 21')),
  2, 'the failed swap changed nothing');
select is(
  pg_temp.rows_affected($$update public.competencies set display_order = 7$$),
  48, 'an UPDATE without WHERE touches only own active rows (archived and foreign rows are skipped)');
select is(
  pg_temp.rows_affected($$update public.competencies set display_order = 8 where false$$),
  0, 'a zero-row UPDATE succeeds');

set local role authenticated;
select set_config('request.jwt.claims',
  '{"sub":"22222222-2222-2222-2222-222222222222","role":"authenticated"}', true);
select is((select display_order from public.competencies where name = 'Docker'),
  0, 'rows of another user were not touched by the multi-row update');
select lives_ok($$insert into public.competencies (career_goal_id, user_id, name, knowledge_type, importance, expected_level)
  values ('bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb', '22222222-2222-2222-2222-222222222222', 'Second', 'memory', 'high', 'beginner')$$,
  'the limit is per goal: B is not affected by A and D being full');

-- User C: archived goal (frozen map), no active goal ---------------------------------------------
set local role authenticated;
select set_config('request.jwt.claims',
  '{"sub":"33333333-3333-3333-3333-333333333333","role":"authenticated"}', true);
select is((select count(*)::int from public.competencies), 1, 'the frozen map stays readable');
select is(
  pg_temp.rows_affected($$update public.competencies set description = 'x'$$),
  0, 'competencies of an archived goal cannot be updated (0 rows, no error)');
select is(pg_temp.constraint_of($$insert into public.competencies (career_goal_id, user_id, name, knowledge_type, importance, expected_level)
  values ('c0c0c0c0-c0c0-c0c0-c0c0-c0c0c0c0c0c0', '33333333-3333-3333-3333-333333333333', 'New', 'memory', 'high', 'beginner')$$),
  'competencies_goal_not_active', 'insert into an archived goal is refused when the user has no active goal');
select lives_ok($$insert into public.competencies (career_goal_id, user_id, name, knowledge_type, importance, expected_level)
  select 'c0c0c0c0-c0c0-c0c0-c0c0-c0c0c0c0c0c0', '33333333-3333-3333-3333-333333333333', 'None', 'memory', 'high', 'beginner'
  where false$$,
  'a zero-row INSERT works for a user without an active goal (the statement trigger does nothing)');

-- Statement trigger: a statement that changes no rows still takes a lock on the active goal.
-- A non-zero xmax on the goal row shows that SOME row lock was taken (nothing else touches these
-- two goals). It does NOT show the lock mode: xmax looks the same for FOR KEY SHARE, FOR SHARE and
-- FOR NO KEY UPDATE. The mode is tested directly in Stage 9.1b with a second connection
-- (FOR SHARE NOWAIT must fail, FOR KEY SHARE NOWAIT must succeed).
reset role;
select is((select xmax::text from public.career_goals where id = 'eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee'),
  '0', 'goal E carries no row-lock marker before the test');
select is((select xmax::text from public.career_goals where id = 'ffffffff-ffff-ffff-ffff-ffffffffffff'),
  '0', 'goal F carries no row-lock marker before the test');
set local role authenticated;
select set_config('request.jwt.claims',
  '{"sub":"55555555-5555-5555-5555-555555555555","role":"authenticated"}', true);
update public.competencies set description = 'x' where false;
select isnt((select xmax::text from public.career_goals where id = 'eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee'),
  '0', 'a zero-row UPDATE leaves a row-lock marker on the active goal (some lock was taken)');
select set_config('request.jwt.claims',
  '{"sub":"66666666-6666-6666-6666-666666666666","role":"authenticated"}', true);
insert into public.competencies (career_goal_id, user_id, name, knowledge_type, importance, expected_level)
  select 'ffffffff-ffff-ffff-ffff-ffffffffffff', '66666666-6666-6666-6666-666666666666', 'None', 'memory', 'high', 'beginner'
  where false;
select isnt((select xmax::text from public.career_goals where id = 'ffffffff-ffff-ffff-ffff-ffffffffffff'),
  '0', 'a zero-row INSERT leaves a row-lock marker on the active goal (some lock was taken)');

-- Privileged role (postgres): contract L2 ---------------------------------------------------------
reset role;
select set_config('request.jwt.claims', '', true);
select 1 from public.career_goals where id = '99999999-9999-9999-9999-999999999999' for no key update;
select lives_ok($$insert into public.competencies (career_goal_id, user_id, name, knowledge_type, importance, expected_level)
  values ('99999999-9999-9999-9999-999999999999', '77777777-7777-7777-7777-777777777777', 'Privileged', 'memory', 'high', 'beginner')$$,
  'a privileged write that follows contract L2 works');
select is(pg_temp.constraint_of($$insert into public.competencies (career_goal_id, user_id, name, knowledge_type, importance, expected_level)
  values ('a0a0a0a0-a0a0-a0a0-a0a0-a0a0a0a0a0a0', '11111111-1111-1111-1111-111111111111', 'Privileged archived', 'memory', 'high', 'beginner')$$),
  'competencies_goal_not_active', 'even a privileged insert into an archived goal is refused by the row trigger');
select is(pg_temp.constraint_of($$update public.competencies set description = 'x' where name = 'Frozen skill'$$),
  'competencies_goal_not_active', 'even a privileged update under an archived goal is refused');
select lives_ok($$insert into public.competencies (career_goal_id, user_id, name, knowledge_type, importance, expected_level)
  select 'dddddddd-dddd-dddd-dddd-dddddddddddd', '44444444-4444-4444-4444-444444444444', 'P' || g, 'memory', 'low', 'beginner'
  from generate_series(1, 2) g$$,
  'a privileged insert may fill the goal up to the limit');
select is(pg_temp.constraint_of($$insert into public.competencies (career_goal_id, user_id, name, knowledge_type, importance, expected_level)
  values ('dddddddd-dddd-dddd-dddd-dddddddddddd', '44444444-4444-4444-4444-444444444444', 'P3', 'memory', 'low', 'beginner')$$),
  'competencies_active_limit', 'a privileged insert cannot exceed the limit');

-- Cascade -----------------------------------------------------------------------------------------
select is((select count(*)::int from public.competencies where career_goal_id = '99999999-9999-9999-9999-999999999999'),
  1, 'the competency of the goal exists before the goal is deleted');
delete from public.career_goals where id = '99999999-9999-9999-9999-999999999999';
select is((select count(*)::int from public.competencies where career_goal_id = '99999999-9999-9999-9999-999999999999'),
  0, 'deleting a goal removes its competencies (ON DELETE CASCADE)');
select is((select count(*)::int from public.competencies where user_id = '33333333-3333-3333-3333-333333333333'),
  1, 'the competency of the user exists before the user is deleted');
delete from auth.users where id = '33333333-3333-3333-3333-333333333333';
select is((select count(*)::int from public.competencies where user_id = '33333333-3333-3333-3333-333333333333'),
  0, 'deleting a user removes the competencies of their goals');

select * from finish();
rollback;
