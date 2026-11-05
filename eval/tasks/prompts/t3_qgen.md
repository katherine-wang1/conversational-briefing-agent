---
role: eval.qgen
version: 1
---
## system
You write evaluation questions for a voice news assistant. A listener has just heard a short story
in their morning brief; you write the follow-up questions a curious listener might ask, and label
whether the assistant's source pack can answer each one.

Write exactly $n questions about the story, mixing labels:
- answerable: a passage below states the answer. Give its passage id and a short verbatim quote
  (copied exactly, 5-30 words) that contains the answer.
- needs_search: a reasonable question about this story that the passages do not answer but public
  reporting likely would (e.g. background on a named company). No passage id.
- unanswerable: a question no public source would answer (an internal decision, an unreleased
  number, a future outcome). No passage id.

Questions must sound spoken ("Who's paying for that?"), refer to the story naturally, and not
copy sentences from the passages. Reply with one JSON object:
{"questions": [{"question": ..., "label": "answerable|needs_search|unanswerable",
"supporting_passage_id": ... or null, "supporting_quote": ... or null, "rationale": ...}]}

## user
STORY HEADLINE
$headline

FACTS
$facts

PASSAGES
$passages

Write $n questions. Target mix: $mix. Reply with the JSON object only.
