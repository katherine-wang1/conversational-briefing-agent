-- Additive: W-A source contract fields (HeadlineObservation.provenance/extra, Document.body_source).
alter table headline_observations add column if not exists provenance text;
alter table headline_observations add column if not exists extra jsonb;
alter table documents add column if not exists body_source text;
