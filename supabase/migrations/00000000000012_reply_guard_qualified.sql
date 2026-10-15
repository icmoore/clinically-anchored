-- Make the reply guard work under an empty search_path.
--
-- messages_reply_target_guard (migration 9) looked up `messages` unqualified. pg_dump starts every
-- dump with set_config('search_path', '', false), so restoring a backup (reset_trial_data.sh keeps
-- one) failed on the first reply row: 'relation "messages" does not exist'. The function now says
-- public.messages. Same checks, same error text; no data changes, safe to re-run.
create or replace function messages_reply_target_guard() returns trigger
language plpgsql
as $$
begin
  if new.origin = 'reply' and not exists (
    select 1 from public.messages m
    where m.id = new.reply_to_message_id and m.episode_id = new.episode_id and m.origin = 'clinician'
  ) then
    raise exception 'reply_target_invalid: a reply must answer a clinician message in the same episode';
  end if;
  return new;
end;
$$;
