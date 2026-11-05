-- Integration (additive): state the real agents need between runs.
--
-- stories.item_urls / features: the newsroom's story identity across runs (TDD §7 N3) needs each
--   story's item URLs and features; the newsroom_run job reloads the last 7 days as `history`.
-- sessions.memory_outcome: the per-session outcome returned by the memory rules engine (TDD §9.3,
--   repeated-skip rule) plus the applied-change report, so the next memory_update can pass
--   `prior_sessions` and changes stay auditable.

alter table stories add column if not exists item_urls jsonb not null default '[]';
alter table stories add column if not exists features jsonb;
create index if not exists stories_beat_first_seen_idx on stories (run_id, beat, first_seen desc);

alter table sessions add column if not exists memory_outcome jsonb;
