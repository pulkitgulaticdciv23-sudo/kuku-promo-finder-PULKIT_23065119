# Evidence and limitations

- The supplied input pack was parsed into 50 episodes, 100 scenes and 3,727
  timed events. The input pack itself is not in this repository.
- Two real local Qwen runs each processed 50/50 episodes. Run A proposed 115
  candidates; 100 passed structural checks, 85 were judged, 15 were unjudged,
  and 81 were ranked after overlap removal. Run B produced 71 ranked and 12
  unjudged candidates. Both runs are marked partial.
- The Run A top ten received 4 Good, 3 Bad and 3 Unsure verdicts in one manual
  screenplay-based review. This small, non-random sample is not an audience test.
- The two top-ten lists share 3 exact cuts; their Jaccard overlap is 17.6%.
  Similar cuts with different boundaries do not count as exact matches, and the
  partial statuses and different run labels limit interpretation.
- The Streamlit interface opened saved full-show results and ran the local model
  on one episode from the same show. Generalization to a different show's format
  is not established.
- The review found a stated cliffhanger near the beginning of one clip, a claimed
  opening action that actually ends before another clip starts, and a continuing
  thought split between timed dialogue blocks.

Source text, model prompts, raw outputs, and the source-dependent audit are kept
out of the public code repository. Model scores are suggestions; editorial and
video review remain necessary.
