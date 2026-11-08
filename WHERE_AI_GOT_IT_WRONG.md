# Where the AI and checks failed

These are observed errors, not hypothetical failures. The underlying screenplay
and model outputs are confidential and are kept outside the code repository.

## Initial scan missed uppercase labels

An early ChatGPT-assisted scan counted 98 scenes because it matched scene labels
case-sensitively and missed two uppercase headings in Episode 6. A
case-insensitive scan found 100 scenes. The parser has an uppercase-label test.
Source: `STAGE2_REAL_RUN.md` and the development record.

## A judge mixed the clip with what came after

In an earlier local-model run, a judge claimed a reply was inside a selected
clip, although the reply began after the cut. The judge prompt now supplies only
in-clip source events, alongside the candidate explanation. This reduces that
specific source of confusion; it does not guarantee factual accuracy.
Source: the original local-model run record.

## An earlier question was labelled as the ending

In full-show Run A, the model labelled a question near the beginning of rank 5
as its cliffhanger. The selected clip actually ended with a completed response.
The manual review marked the clip Bad. Matching a source phrase is not enough
if the explanation misrepresents where that phrase occurs.
Source: the separate `run_A_user_audit.csv` and original Run A outputs.

## A claimed opening action was outside the clip

For Run A rank 8, the model described a strike at the opening, but the timed
strike ends before the clip starts; a reaction overlaps the start. The manual
review marked the clip Unsure. Unquoted claims can still be wrong.
Source: the separate `run_A_user_audit.csv` and original Run A outputs.

## A valid event boundary still interrupted a thought

Run A rank 3 ends at the end of a timed dialogue block, but the speaker continues
the list in the following block. The manual review marked it Unsure. Checking
for cuts inside timed dialogue cannot guarantee a complete thought or a good
cliffhanger.
Source: the separate `run_A_user_audit.csv` and original Run A outputs.
