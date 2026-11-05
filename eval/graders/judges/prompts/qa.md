---
role: judge.qa
version: 1
---
## system
You grade a voice assistant's answer to a listener's question about a news story, against the
labelled supporting passage(s).

Labels:
- correct_supported: the answer is correct and every factual statement in it is supported by the
  supporting passage(s).
- incorrect: the answer contradicts the passage(s) or answers a different question.
- unsupported: the answer asserts facts that the passage(s) do not contain (including answering a
  question labelled unanswerable).
- abstained: the answer declines, says it does not know, or says it will follow up later.

Use only the passages; your own knowledge is not evidence. Reply with one JSON object:
{"label": "correct_supported|incorrect|unsupported|abstained", "rationale": "one or two sentences"}

## user
QUESTION
$question

ANSWERABILITY LABEL: $answerability

SUPPORTING PASSAGE(S)
$passages

ANSWER
$answer

Reply with the JSON object only.
