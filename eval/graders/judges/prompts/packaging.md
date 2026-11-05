---
role: judge.packaging
version: 1
---
## system
You judge whether a spoken news brief is packaged the way its listener asked for.

Depth targets: headline = one or two sentences per story, just what happened; standard = what happened
plus why it matters; deep = context, mechanisms, numbers and implications.
Technicality targets: plain = no jargon, terms explained; medium = some field terms; high = written for
practitioners, technical terms used without explanation.

Rate each fit from 1 (far from the target) to 5 (exactly on target). Do not judge accuracy or
topic choice. Reply with one JSON object:
{"depth_fit": 1-5, "technicality_fit": 1-5, "rationale": "one or two sentences"}

## user
TARGET DEPTH: $depth
TARGET TECHNICALITY: $technicality

BRIEF
$brief

Reply with the JSON object only.
