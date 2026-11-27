-- Shared story notes (app/contracts/notes.py; docs/architecture-revisit.md part 2): one note per digest
-- story, written after each newsroom run, read by every user's run and the voice agent. Append-only:
-- the latest row by created_at per (run_id, story_id) is the current note. No user data.
create table if not exists story_notes (
  id          bigint generated always as identity primary key,
  run_id      text not null default 'live',
  story_id    text not null,
  digest_id   text not null,
  beat        text not null,
  note_json   jsonb not null,
  created_at  timestamptz not null default now()
);
create index if not exists story_notes_story_idx on story_notes (run_id, story_id, created_at desc);
create index if not exists story_notes_digest_idx on story_notes (run_id, digest_id);

-- Shared (newsroom) table: readable and writable by the backend in any context, like digests.
alter table story_notes enable row level security;
drop policy if exists shared_rw on story_notes;
create policy shared_rw on story_notes to app_rw using (true) with check (true);
grant select, insert, update, delete on story_notes to app_rw;
