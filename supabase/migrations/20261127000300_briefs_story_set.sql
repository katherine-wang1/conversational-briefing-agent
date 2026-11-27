-- Per-user stored set (app/contracts/notes.py UserStorySet): the brief's stories plus the next best
-- by the chief of staff's ranking (default 20). One JSON value per brief, written with the script by
-- the cos_daily job and read by the voice session. Same RLS as the rest of the briefs row.
alter table briefs add column if not exists story_set jsonb;
