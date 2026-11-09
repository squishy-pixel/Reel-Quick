# Detection: design

Phase 2 of Reel Quick: find our segments in a six-hour return feed so the
operator confirms candidates instead of scrubbing for them. Agreed with TJ,
November 2026. Read this before working on detection.

## What gets detected

Every detection carries three labels: what it is, whose it is, and which part
of the broadcast it is in.

**What it is**

- **Graphics shell**: one of the Vizrt shells in `library.SHELL_ALIASES`
  (Match-Up, Player Card, Scenic Comp, ...). Shells sit over game footage,
  drone shots or anything else, so recognising the graphic is the core
  problem, not recognising the picture.
- **Booth**: the announcers (Al Michaels and Kirk Herbstreit) on camera. The
  booth is ours: they sit in front of an LED wall showing our background
  design, so any booth shot carries our work. Recognise it by that background.
  A booth clip is named `Booth`, never after whoever is on camera.
- **Scenic**: scenic and drone shots.
- **Fireworks**: picture and audio (the bursts).
- **Other graphic**: a graphic that is clearly not game footage but matches
  nothing in the library.

**Whose it is**

- **Ours** (Girraphic): what we deliver, booth included.
- **NBC design**: graphics made by the NBC designers.
- **Other contributor**: anyone else.

Picking up content that is not ours is not an error. It is wanted, but it is
categorised separately and, importantly, **named and filed separately**, so it
can never land among our deliverables. The owner must change the file name
and the output folder, not just a tag in the project file.

## Filing and naming (decided)

Last season every clip and still was duplicated by hand into folders by type
and by sequence per game. The app replaces that with one copy, filed once:

- **Folders by content type**: one folder per shell or category (Match-Up,
  Player Card, Booth, Scenic, Fireworks, ...) inside the game's clips folder.
  Stills go with their clips, in the type folder's `stills/`.
- **Phase and sequence in the file name**, not in folders: the sequence keeps
  program order across types, and the phase says which group it is for.
- **Content that is not ours goes in `Other Content`**, a folder of its own
  beside the type folders, so it cannot mix with our deliverables. File
  names keep the same structure as ours. TJ names these by hand, since what
  they are is not known in advance; the app fills in date, game, sequence and
  phase and leaves the description to him.
- The folder name and the pattern stay settings, not hard-coded.

There are no NBC examples yet. TJ will cut them as they come up; they become a
second reference set, so ours-or-NBC is a matching problem like the shells.

**Which part of the broadcast**

`pre`, `game` (first half), `half`, `game` (second half), `post`, in program
order. The studio shows (pre, half, post) are produced by a different group
from the game, and we work for both, so every clip carries its phase. Phases
come from four boundaries per game: kickoff, end of the first half,
second-half kickoff, end of the game (overtime is still `game`). The operator
can set them, and the detector can suggest them: the score bug is on screen
during the game and absent in the studio shows. The phase is a `{phase}` field
in the naming pattern (see Filing and naming).

## Fireworks

Fireworks are ours unless they are a touchdown or the national anthem.

1. Detect all fireworks (picture plus audio bursts).
2. Not ours if inside the anthem window: the operator marks it once per
   game, alongside the phase boundaries, since it sits at a predictable
   point in the pregame.
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
- **Last season's full games**, at `G:\2025 TNF Game Reviews` on TJ's PC,
  plus their cut clips in the library. The trailing number in the clip names
  places each clip in its recording to the frame, once the recording's
  timecode base is known: see Findings below.

Report, for each category: segments found, segments missed, false alarms,
and how far suggested in/out points land from the real ones.

## Findings: last season's recordings

Checked November 2026 on TJ's PC. The clips were cut in Resolve from these
files, not from the original MXFs.

### The files in `G:\2025 TNF Game Reviews`

Sixteen reduced MP4 copies (6 to 12 GB each), not the original MXFs. All are
progressive, constant 59.94 fps (60000/1001) with every frame present
(frame count = duration × 59.94), BT.709 SDR, one stereo AAC track. **None
carries a timecode**, not even a `tmcd` track; the only clock in them is the
file's `creation_time`, shown below in Pacific time.

| Game | Codec | Resolution | Bit rate | Duration | creation_time (PT) |
|---|---|---|---|---|---|
| WK02 WAS–GB | HEVC Main 10 | 1920×1080 | 5 Mb/s | 5:33:05 | 15:58:01 |
| WK03 MIA–BUF | HEVC Main 10 | 1920×1080 | 5 Mb/s | 5:07:32 | 15:58:01 |
| WK04 SEA–ARI | HEVC Main 10 | 1920×1080 | 5 Mb/s | 5:17:36 | 15:58:00 |
| WK05 SF–LAR | HEVC Main 10 | 1920×1080 | 5 Mb/s | 5:37:59 | 15:58:00 |
| WK06 PHI–NYG | HEVC Main 10 | 1920×1080 | 5 Mb/s | 5:24:00 | 15:58:01 |
| WK07 PIT–CIN | HEVC Main 10 | 1920×1080 | 5 Mb/s | 5:27:32 | 15:57:44 |
| WK08 MIN–LAC | HEVC Main 10 | 1920×1080 | 5 Mb/s | 5:38:08 | 15:40:01 |
| WK09 BAL–MIA | H.264 High | 1280×720 | 3 Mb/s | 5:33:35 | 15:40:34 |
| WK10 LV–DEN | H.264 High | 1280×720 | 3 Mb/s | 5:40:00 | 15:40:37 |
| WK11 NYJ–NE | H.264 High | 1280×720 | 3 Mb/s | 5:24:49 | 15:40:43 |
| WK12 BUF–HOU | H.264 High | 1280×720 | 3 Mb/s | 5:56:02 | 15:40:35 |
| WK14 DAL–DET | H.264 High | 1280×720 | 2 Mb/s | 5:59:49 | 15:40:29 |
| WK15 ATL–TB | H.264 High | 1280×720 | 2 Mb/s | 5:51:15 | 15:40:29 |
| WK16 LAR–SEA | H.264 High | 1280×720 | 2 Mb/s | 6:15:32 | 15:40:43 |
| WK17 DEN–KC | H.264 High | 1280×720 | 2 Mb/s | 5:04:23 | 16:10:32 |
| WK19 GB–CHI | H.264 High | 1280×720 | 2 Mb/s | 5:37:14 | 16:10:36 |

The HEVC files' first frame sits at 0.0167 s, the H.264 files' at 0. Frame
numbers below count decoded frames from the first one.

### The trailing number is the clip's source timecode, as a frame count

The clips carry the timecode Resolve gave them, and the trailing number is
that start timecode converted to a frame count at 59.94 (drop-frame aware
when the timecode is drop-frame). `BaseballCard_01_00996938` starts at
`04:36:55:38` = (4·3600 + 36·60 + 55)·60 + 38 = 996938;
`BaseballCard_01_03534371` (WK09) starts at `16:22:45;07`, which is 3537907
nominal frames less 3536 dropped. The rule holds for every library clip except:

- WK17 DEN–KC (all 20 clips): timecode `00:00:00:00` and number `00000000`.
  These can only be placed by matching pictures.
- Three naming slips: `WK04 WhoYaGot_01_00952645` carries Standings_01's
  number (its timecode is `00:53:03:51`); `WK05 Scenic_01_00491772` and
  `WK15 Matchup_01_00319299` disagree with their timecodes.

Whether the number is the recording's frame number then depends on where
Resolve started that file's timecode. Two bases turned up:

- **WK04 SEA–ARI: the number is the frame, offset 0.** Its clips' timecode
  counts from `00:00:00:00`. Checked 5 clips (NextWeekPromo_02, Booth_01,
  MatchUp_01_HotAirBalloons, Score_01, BaseballCard_02): four match the
  recording exactly at the named frame, 47–51 dB PSNR, every other offset in
  ±150 frames 10–25 dB worse. Most games' clips use this 00:00-based,
  non-drop timecode (WK02–07, WK11, WK12, WK14–16, WKWC, and all but a few
  clips of WK08 and WK09), so the number should be the frame there too; not
  checked frame by frame outside WK04.
- **WK10 LV–DEN: constant offset of 3380616 frames.** Its clips carry
  drop-frame time-of-day timecode (16:53 to 20:22), and Resolve's base for
  the file is `15:40:00;00` = 3380616 frames. Recording frame = number −
  3380616. Found by searching the whole file for 6 clips (they landed 2220
  frames, 37.04 s, after the creation_time guess), then checked frame by
  frame on all 13 time-of-day clips: 12 peak exactly at offset 0, OnField_01
  is a 0.1 dB tie between 0 and −1 on a nearly static shot.

Drop-frame time-of-day timecode shows up only in WK10 (13 of 14 clips), WK13
(all 20, and there is no WK13 recording) and a few clips of WK08 (1) and WK09
(2). So the base can differ between clips of the same game, and has to be
found once per base, not per game. creation_time gives it
only to within a minute: WK10's real base is 37 s before its creation_time,
and the round values (15:40, 16:10) suggest scheduled start times. A
keyframe-only search of one recording (every 250 frames, about 80 s for a
5.7 h file) finds a few clips and gives the base.

### Surprises

- **The clips are not graded like the recordings.** WK04 clips match their
  recording almost exactly (~50 dB). WK10 clips sit at only 22–27 dB at the
  right frame: same framing, same pixels, but different colour (the Broncos'
  orange reads red in the recording). Fingerprints must tolerate a colour
  shift between the library and the game.
- **Not every library clip was cut from the feed.** WK04
  `NextWeekPromo_02` is a rendered "Next Week" graphic; at its frame the
  recording shows the studio, and nothing within ±150 frames matches. WK10
  `RepurposedShell_01_00255654`, the one 00:00-style clip in a time-of-day
  game, is not found at its number nor anywhere in the recording's keyframes.
- **Recordings and library games do not line up one to one.** There is no
  recording for WK13 CHI–PHI (20 clips). The recordings include WK19 GB–CHI,
  while the library has WKWC DEN–CHI; check which game that is.
- **Two kinds of copy.** WK02–08 are 1080p HEVC 10-bit, WK09 onward 720p
  H.264 at 2–3 Mb/s; detection runs on whichever the game has.

## Order of work

1. ~~Verify the frame-number hypothesis on one of last season's full games.~~
   Done: see Findings.
2. Fingerprint the library and one game; measure shell matching on the
   known game before anything is built into the app.
3. Phase boundaries and the anthem window in the app (set by hand first),
   with `{phase}` in the naming pattern.
4. Candidate markers in the app (a new marker source beside `log` and `user`),
   with the what, whose and phase labels, and owner-aware naming and folders.
5. Fireworks (audio + picture), then the anthem and touchdown rules.
6. OCR naming suggestions, and phase suggestions from the score bug.
7. Feedback loop: confirmed clips into the library.

## Open questions

- Where `{phase}` sits in the default pattern (currently
  `{date}_{game}_{seq:02d}_{shell}_{camera}_{desc}`): proposed after the
  sequence, e.g. `261022_WK08-NEvCHI_07_Pre_MatchUp_HotAirBalloons` and
  `261022_WK08-NEvCHI_08_Game_Booth`, with the phase spelled `Pre`, `Game`,
  `Half`, `Post`. TJ confirmed this placement.
- ~~Does `G:\2025 TNF Game Reviews` hold the original MXFs or reduced
  copies?~~ Reduced copies, every frame kept at 59.94, and the clips were cut
  from them, so the frame numbers line up (see Findings).
- Which game is the WKWC DEN–CHI library folder, given the WK19 GB–CHI
  recording?
