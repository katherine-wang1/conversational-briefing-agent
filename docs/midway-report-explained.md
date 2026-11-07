# Midway report outline, explained in plain language

This is a companion to `docs/midway-report-outline.md`. It walks through each section in plain language so the ideas are easier to follow. It is written to you, as an explanation, and is not meant as report text. The course doesn't allow AI-written reports, so use it to understand the material and then write your own version.

---

## 1. Problem specification

This section answers one question: what problem does the project solve, and for whom? The answer is simple. People who need to keep up with AI news don't have time to read everything, and the time they do have, like a commute, is a bad moment for scrolling and typing. So the agent talks to you. Each morning it gives a short spoken brief of a handful of stories. You can cut in at any point to ask about one, and over time it learns what you care about from how you react.

The interesting part is that every brief has to balance three things. Some stories matter to everyone, some matter to you in particular, and some are things you wouldn't think to look for but might like. If the agent only chased what you already like, your view would shrink over time, which is the "filter bubble" problem. So it deliberately saves room for the third kind.

The research questions turn that balance into things you can test. Does learning from conversation pick better stories than a profile that never changes? Does saving a slot for new topics actually widen what you see? Does preparing sources ahead of time let it answer questions quickly enough to feel like a conversation? And is everything it says actually backed by the sources it cites?

## 2. Environment & Data

This section explains the world the agent works in and how you'll test it. It is one of the most heavily graded sections.

**The environment.** The agent runs in two modes. In live mode it uses the real internet: a news search engine, real web pages, and real headline sites like Techmeme and Hacker News. In replay mode it uses a frozen copy of the news from a past period and behaves as if each past day were today. The important point is that the agent's code is identical in both modes. Only the data source changes. That's what makes the evaluation fair and repeatable: every version of the agent sees exactly the same news.

**The data.** You're collecting a snapshot of AI news from August through October 2026: Hacker News posts, arXiv papers, company blogs, and archived copies of front pages from the Wayback Machine. A lot of this is already collected, including about 91,000 Hacker News items and about 49,000 arXiv papers. The test period will be about 40 days in September and October. The first 10 days are for tuning, and the last 30 are kept untouched for the final test.

The test period was chosen carefully. It starts after the AI models' training data ends, so a model can't "already know" the news. It also ends at least two weeks before today, so you know how each story played out afterward. There's also a strict rule that the agent only sees what was visible on the morning of each day, never what happened later.

**How you'll know a story was important.** Once two weeks have passed, you can look back. If many independent news sites covered a story, it was important. That hindsight judgment is the answer key the agent's picks are graded against.

**Simulated users.** You only have one real user (you), and that isn't enough to measure much. So you built 18 fake users. Each has hidden interests, some interests it never mentions but would enjoy, preferences about length and depth, and interests that change partway through the test. The agent never sees these hidden details. It has to figure them out from how the fake user behaves, just as it would with you. One of the fake users is modeled on you.

**What counts as success.** Some measures are simple counts that code can compute. Did the agent include the big stories? Did it pick stories matching the user's hidden interests? Did it cover a range of topics? Did its picture of the user come to match the hidden profile? Other measures need judgment, so an AI "judge" handles them. The main one is whether each claim is actually supported by the source it cites. You'll check the judges against your own labels to make sure they can be trusted.

## 3. Methods

This section describes how the agent is built. A diagram helps a lot here.

The system has three parts.

**The newsroom** is shared by everyone and knows nothing about any user. It collects headlines, groups articles about the same event into one "story," and scores how important each story is. The model has to back up its importance scores with evidence, like "seven independent outlets covered this," rather than its own opinion. The output is a daily digest of the top stories.

**The chief of staff** is personal: one runs for each user, early each morning. It starts from the newsroom's digest and adds its own searches based on what it knows about you, including some searches on neighboring topics you might not think to ask about. It scores every candidate story for relevance to you, importance, and novelty.

Then a fixed set of rules, not the AI, picks the final lineup. Two slots go to big news, three to stories for you, and one to something outside your usual interests. Doing this step in plain code is a deliberate choice. It's predictable, easy to inspect, and easy to switch off when you want to test what each piece contributes.

For each chosen story, the chief of staff gathers short passages copied word for word from the sources. That's the preparation that lets the voice agent answer questions quickly later. Then it writes the brief, with every sentence citing a passage, and double-checks each sentence against its sources. Unsupported sentences get rewritten or cut. Finally the brief is turned into audio.

**The voice agent** plays the brief and handles interruptions. When you cut in, it knows where you were in the brief, so "tell me more about that one" works. It answers from the prepared passages when it can. If they don't cover the question, it does a quick search or saves the question to answer before tomorrow's brief.

**Memory** works in two steps. After each session, the AI reads what happened and suggests what to remember, for example "she asked two questions about humanoid robots." Then fixed rules decide how much each signal counts. Something you say outright counts more than something inferred. Skipping a story only counts against it if you actually had time to listen. Interests fade if they aren't reinforced, and no single topic can take over your profile. Splitting the work this way keeps memory predictable while still letting the AI interpret the conversation.

**Comparisons.** To show that each piece matters, you'll test simpler versions of the agent and remove one ingredient at a time:
- one that ignores your interests and only picks big news;
- one whose memory never updates after setup;
- one with no "outside your usual" slot;
- one that doesn't prepare sources ahead of time;
- a naive version that does everything in a single AI call.

## 4. Summary of progress

This section is about being honest about where things stand.

**What's done.** The whole system is built and works end to end. In a browser test with real audio, a new user signed up, got a brief about two minutes later, listened, interrupted to ask a question, heard a spoken answer with sources, went back to the brief, and finished. After a session where a question was asked about a topic, the memory step raised that interest's weight, which shows the learning works. The speech reads the brief word for word (checked independently), and the whole thing costs very little to run. The data collection is well underway.

**What's left.** The main remaining work is evaluation: finish collecting the archived news, deploy the app to your phone, use it yourself for a few weeks, label some data, run the full tests against the simpler versions, and analyze where it fails. There are no evaluation results yet, and the report should say so directly.

**What could go wrong.**
- The biggest technical gap is speed. Answers currently take about 2.5–3 seconds to start, and the goal is about 1 second. There's a planned fix.
- Collecting the archived news is slow, because the Wayback Machine limits how fast you can download.
- Archive coverage near the morning brief time is thinner than hoped.
- Some news sites block automated access, so a few big stories ended up with no usable sources.
- Simulated users might not behave like real people. That's why you'll compare the one modeled on you against your real behavior.
- You're a single real user, so most of the statistics will come from the simulated panel.

## 5. References

Use the same format as the proposal, and reuse most of the proposal's references. The ones about citation accuracy, diversity in recommendations, and simulated users are especially relevant to Methods and to the risks.

## 6. AI-use disclosure

This statement just has to be honest and specific about what AI did:
- **Code:** AI coding agents wrote almost all of it from your design documents, while you made the design decisions and directed the work. You'll need to be able to explain the code.
- **Data and evaluation:** AI also built the data collectors and the simulated users.
- **Testing:** AI ran the tests and wrote up the results.
- **This report:** AI helped organize the facts and explain them, and the writing is yours.
