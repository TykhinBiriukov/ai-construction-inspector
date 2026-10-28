-- LEGACY direct-user transport proposal. NOT needed for selected Inspector signed-upload flow. Do not apply for this integration.
-- Run in Supabase SQL Editor for project drzxapqypaxhocrksciq.
-- Creates only two named policies; does not change other policies.
-- Existing permissive policies must be reviewed separately (policies combine with OR).
begin;
do $$ begin
  if not exists (select 1 from storage.buckets where id = 'inspection-captures' and public = false) then
    raise exception 'Create private bucket inspection-captures first';
  end if;
end $$;
drop policy if exists inspector_capture_insert_own on storage.objects;
create policy inspector_capture_insert_own on storage.objects
for insert to authenticated
with check (bucket_id = 'inspection-captures' and (storage.foldername(name))[1] = (select auth.uid()::text));
drop policy if exists inspector_capture_read_own on storage.objects;
create policy inspector_capture_read_own on storage.objects
for select to authenticated
using (bucket_id = 'inspection-captures' and (storage.foldername(name))[1] = (select auth.uid()::text));
commit;
-- No UPDATE/DELETE access: app never overwrites evidence.
