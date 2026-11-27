# Try it on your computer

For Kate, 2026-11-27: run the whole system live and get a feel for it. About 10 minutes the first time,
about $0.45 of OpenRouter for the first day (the shared newsroom is most of it), then about $0.10 per
rebuilt brief.

## 1. Get the branch

```bash
git fetch origin claude/sweet-curie-r8ufht
```

```bash
git checkout claude/sweet-curie-r8ufht
```

```bash
git pull
```

## 2. Run it

```bash
./scripts/dev.sh --reset
```

`--reset` asks before wiping your local database, so you onboard fresh on today's news; answer `y`.
Without `--reset` it keeps your existing user and briefs. The script installs what's missing, asks for
your OpenRouter key and Tavily key the first time (saved to `.env`), and starts everything on
**http://localhost:8000**. Leave the terminal open; Ctrl-C stops it. It sets a $2 spend cap per
process (`LLM_SPEND_CAP_USD` in `.env`).

## 3. In the browser (Chrome)

1. **Dev sign-in (bypass)**.
2. Say what you want to hear about, in your own words. Pick **~4 min** (6 stories).
3. Wait about 4 minutes the first time: "Gathering today's news" (the newsroom for three beats and the
   story notes), then "Writing your brief". The worker's progress is in the terminal.
4. **Play.** Tap the microphone (or **Type instead**) whenever you want to ask something; it answers out
   loud and goes back to the brief. Try:
   - a question about the story you're hearing ("who's behind that?", "why now?");
   - "What else happened today?" (it answers from your set of 20 stories, not only the brief);
   - a question about one of those other stories.
5. **Settings → Show what's happening** shows the live log: what it heard, each model call with time
   and cost, searches.

## 4. Look at how the brief was built

- The spoken script and its sources: **History**.
- The full build trace (searches, candidates, scores, picks, claim-check drops):
  `var/traces/cos/<brief id>.json`.

## 5. Change something and hear it again

Rebuild today's brief on the same news (about $0.10; the newsroom doesn't rerun):

```bash
.venv/bin/python scripts/rebrief.py
```

It prints the new script and what the claim check dropped; reload the app to hear it. Handy after
editing `app/agents/prompts/cos.write_brief.md`, or after changing your interests on the Memory screen.

## What's new since the midpoint (what to listen for)

- **Podcast style:** no "now to energy" or "outside your usual beat"; bridges between stories instead;
  background on each story (what the company is, what came before, why it matters); about 4 minutes.
- **Shared newsroom and story notes:** every user gets the same ranked stories per beat, each with one
  cited note; the brief and the voice draw on those notes.
- **20 stories per day for you:** the brief reads 5–6, the conversation can go into any of the 20.
- **What isn't there yet:** the brief doesn't yet know which stories you've heard on earlier days in
  detail (only headlines, as follow-ups); newsroom ranking can still repeat old news or cluster on one
  company; the UI is the old one.

## Things to note while listening (for the next session)

- Does it sound like one person talking to you? Which bridges work, which feel forced?
- Is the amount of context right, story by story?
- Which stories shouldn't be there (old, off-topic, low quality)? Which did you expect?
- Voice answers: right length? Did it ever make something up or dodge?

## If something goes wrong

- Port 8000 busy: stop the earlier `./scripts/dev.sh` (Ctrl-C) or whatever uses the port.
- "Gathering today's news" for more than 6 minutes: check the terminal for a failed `newsroom_run`.
- Logs: `var/logs/worker.log` and `var/logs/web.log`. More in the README's Troubleshooting section.
