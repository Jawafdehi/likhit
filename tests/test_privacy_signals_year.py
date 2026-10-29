"""The citizenship signal is left exactly as it is, and the reason is recorded here.

🛑 **Three attempts to narrow it were all wrong, in three different ways.** The
scanner serves two masters and they disagree:

* the platform's gate wants **detector ⊆ remover** — anything reported that no
  rule can remove refuses the document forever, since the sweep runs at
  ``on_residue="raise"``;
* the published disclosure wants **detector ⊇ reality** — a real identifier that
  goes uncounted understates what the corpus contains.

A 5-digit citizenship number sits in the gap: real, and below
``redact.DIGIT_BOUNDS``'s measured floor of 7. No change to this pattern can
satisfy both, and each attempt traded one failure for the other:

1. a flat floor of five stopped counting `११-२२-३३-४४४४`, a removable 10-digit
   identifier whose longest contiguous run is four;
2. a year-shape exclusion on bare four-digit runs admitted runs the remover's
   floor refuses, reopening refuse-forever from the other end;
3. a separator-adjacency rule used `[-/.]`, which is neither the remover's set
   (`- / whitespace` plus the danda) nor a superset of it — so space- and
   danda-separated values stopped being counted while dot-separated ones became
   countable-but-unremovable.

The resolution is not in this file. It is for the GATE to distinguish "residue a
rule can remove" from "detected and deliberately out of scope", which is a change
to ``materials.redaction.residual_classes`` in JawafdehiAPI and wants its own
measurement of how many documents each reading refuses.

Until then the pattern stays as measured, and this module documents why rather
than carrying a fourth attempt.
"""
