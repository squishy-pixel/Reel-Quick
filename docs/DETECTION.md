# Detection: design

Phase 2 of Reel Quick: find our segments in a six-hour return feed so the
operator confirms candidates instead of scrubbing for them. Agreed with TJ,
November 2026. Read this before working on detection.

## What gets detected

Every detection carries two labels.

**What it is**

- **Graphics shell**: one of the Vizrt shells in `library.SHELL_ALIASES`
  (Match-Up, Player Card, Scenic Comp, ...). Shells sit over game footage,
  drone shots or anything else, so recognising the graphic is the core
  problem, not recognising the picture.
- **Booth**: the announcers on camera.
- **Scenic**: scenic and drone shots.
- **Fireworks**: picture and audio (the bursts).
- **Other graphic**: a graphic that is clearly not game footage but matches
  nothing in the library.

**Whose it is**

- **Ours**: what we deliver.
- **NBC design**: graphics made by the NBC designers.
- **Other contributor**: anyone else.

Picking up content that is not ours is not an error. It is wanted, but it is
categorised separately and, importantly, **named and filed separately**, so it
can never land among our deliverables. The owner must change the file name
and the output folder, not just a tag in the project file.

## Fireworks

Fireworks are ours unless they are a touchdown or the national anthem.

1. Detect all fireworks (picture plus audio bursts).
2. Not ours if inside the anthem window: the operator marks it once per
   game, since it sits at a predictable point in the pregame.
3. Not ours if within about 30 s after a touchdown: read the score bug, and a
   jump of 6 points marks one.
4. Everything else is ours.

## Approach

No model trained from scratch. A pretrained image model turns frames into
fingerprints (embeddings), and detection is matching against a reference
library, so it improves every time the operator confirms a clip.

1. **Library** (built: `reelquick library`, `engine/reelquick/library.py`).
   300 of last season's on-air clips parse into game, shell, sequence and
   note, with reference frames per clip.
2. **Fingerprints.** Embed the library frames and the game's frames on the
   GPU (TJ's machine: MSI Raider 16, mobile RTX 5090). Because shells overlay
   arbitrary backgrounds, whole-frame embeddings are not enough: match on the
   regions where shells sit and on patch-level features, so a Match-Up over
   grass and the same Match-Up over a drone shot score alike. Validate this
   choice on real data before building on it.
3. **Scan.** The filmstrip already samples the game every 2 s. Score each
   sample against the library, join runs into candidate spans, refine their
   edges on the full-rate source (shot changes, graphic in/out), and show
   them as candidate markers with a suggested what, whose, shell, in and out.
4. **Text.** OCR on the graphic gives naming suggestions (player names,
   matchups) and, from the score bug, the touchdown rule above.
5. **Feedback.** Every confirmed clip goes back into the library with its
   labels, so the next game is matched against more examples.

## Measuring it

Nothing ships on how it looks. Each step is scored against games whose
answers are known:

- **TB at DAL (2026)**: the tech's log is the answer key (`markers.py` reads it).
- **A full game from last season** plus its cut clips. The trailing number in
  the clip names (`...HotAirBalloons00260425`) may be the source frame
  number. Unverified: TJ is not sure. Check it first by comparing a clip's
  frames with the recording at that frame. If it holds, every clip of that
  game is placed to the frame for free. If not, find each clip by matching
  its frames against the recording.

Report, for each category: segments found, segments missed, false alarms,
and how far suggested in/out points land from the real ones.

## Order of work

1. Verify the frame-number hypothesis on one of last season's full games.
2. Fingerprint the library and one game; measure shell matching on the
   known game before anything is built into the app.
3. Candidate markers in the app (a new marker source beside `log` and `user`),
   with the what and whose labels, and owner-aware naming and folders.
4. Fireworks (audio + picture), then the anthem and touchdown rules.
5. OCR naming suggestions.
6. Feedback loop: confirmed clips into the library.

## Open questions

- Naming and filing for content that is not ours: pattern and folder for NBC
  design and for other contributors.
- Is the booth ours, someone else's, or both depending on the shot?
- How to tell NBC design graphics from ours when both are new: a separate
  reference set of NBC work would make it a matching problem as well.
