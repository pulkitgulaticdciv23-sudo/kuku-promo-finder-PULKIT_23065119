# Stage 2 — real run on the supplied pack

This report describes a real parser and validator run. The cases used to exercise
the validator were automatically assembled from source events; they are not
editorially selected promos or model outputs.

| Measurement | Result |
| --- | ---: |
| Input episodes | 50 |
| Parsed scenes | 100 |
| Parsed events | 3,727 |
| Parsed event blocks checked against their complete source blocks | 3,727 of 3,727 |
| Parser warnings | 1 |
| Distinct source event boundary times, summed per episode | 4,965 |
| Boundaries allowed by the current dialogue/ellipsis rules | 4,564 |
| Boundaries inside a spoken event | 323 |
| Boundaries in an obvious same-speaker ellipsis continuation | 78 |
| Source-backed structural samples that passed | 50 of 50 |
| Invented timestamps correctly rejected | 50 of 50 test cases |
| Invented hook lines correctly rejected | 50 of 50 test cases |
| Mid-dialogue boundaries correctly rejected | 40 test cases |
| Automated unit tests | 30 passed |

The 40 mid-dialogue tests are the episodes whose structural test sample offered an
in-range mid-dialogue event boundary to substitute; the other 10 were not tested by
that particular mutation. The validator was also tested on a controlled fixture.

The warning is a sound-effect block in Episode 22 that ends 0.30 seconds after its
scene's end. The program keeps the supplied timestamp and reports the mismatch.

The complete pack was also tested both as a ZIP and as a folder: the resulting
structured episodes were identical. The original machine-readable output is kept
outside the source repository with the private run evidence.

These measurements do not establish hook quality, standalone clarity, cliffhanger
quality, spoiler safety, or performance on another screenplay format. Those require
later model calls, manual review, and new-show testing.
