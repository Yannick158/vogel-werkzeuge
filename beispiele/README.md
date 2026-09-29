# Beispiele / Samples

What the tools in this repository produce, from one real week in one real
garden.

| file | what it is |
|---|---|
| `vogelwoche-2026-W38.en.mp4` | the weekly recap for ISO week 38, 2026 — German narration (Piper, voice `thorsten`), **English subtitles burned in**, 90 s |
| `vogelwoche-2026-W38.mp4` | the same episode with the German subtitles, exactly as it was published |
| `vogelwoche-2026-W38.de.srt`, `.en.srt` | the subtitles as sidecar files, cue-timed from the narration |
| [`../doku/live-band.png`](../doku/live-band.png) | the live spectrogram band with real detections, captured from the running system |

## What was changed for publication

Exactly one thing: the migration-map scene originally labelled the departure
point with the town's name. Here it says *Ihr Garten* ("your garden"). The
narration was untouched — it never named the place.

## Licence of these files

These samples are **not** under the repository's AGPL licence, because they
are built from material that is not ours:

- the bird illustrations come from the
  [AvianVisitors](https://github.com/Twarner491/AvianVisitors) illustration set
  and from the same generation pipeline, and are **CC BY-NC-SA 4.0**;
- the narration voice is Piper's `de_DE-thorsten-medium` (CC0);
- the script was written by a language model from the week's detection data.

The videos and the live-view capture are therefore offered under
**CC BY-NC-SA 4.0** as well. Use them to see what the tools do, share them
non-commercially with attribution, and build on them under the same terms.
Everything else in this repository stays AGPL-3.0.
