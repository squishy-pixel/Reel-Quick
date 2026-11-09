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

### TJ's notes on these findings

- **Colour will be off, by design of the data.** The library clips were
  probably rendered to SDR from low-quality 720p HDR sources, and their origin
  is not certain. Expect them to be close enough to recognise content but
  never colour-accurate. Matching must lean on shape, layout and text, not
  colour: compare on luma or edges, or normalise colour before embedding, and
  measure how much each choice helps.
- **WK13 CHI–PHI has no recording.** Its 20 library clips stay as reference
  frames only. TJ can download the game again if it is ever needed (a few
  days), so plan without it.
- **WKWC is WK19 GB–CHI** (see Open questions).

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

## Findings: shell matching on WK04 (step 2)

Measured November 2026 on TJ's PC (RTX 5090 Laptop) with
`engine/scripts/shell_eval.py`; its docstring has the commands. Caches and
full results (`eval-2025WK04-SEAvsARI.json`, `colour.json`) are in
`F:\_AI-Resources\App-Reel-Quick\Work\detection-eval`.

### Setup

- **Test game: WK04 SEA–ARI, unseen.** All 16 WK04 clips are left out of the
  reference set. The answer key is 15 of them, placed by their timecode
  (offset 0 for WK04). NextWeekPromo_02 is left out because it is not in the
  feed. WhoYaGot_01 is placed by its timecode, since its name carries the
  wrong number (checked: 50 dB at that frame).
- **References:** 5 frames (20–80% through, skipping handles) from each of the
  other 284 library clips: 1420 frames, 26 shells.
- **Samples:** every keyframe of the recording, 17308 frames, about one per
  1.1 s. A keyframe-only decode of the 5.3 h file takes about 4 minutes. A
  detection is a run of at least 2 samples with one label.
- **Background:** 3595 WK05 keyframes from outside WK05's clips. They are
  used to standardise each shell's scores and to weight the patch matcher.
- **Features:** DINOv2-base (pretrained, no training) on 448×252 frames. Two
  matchers on the same features:
  - **whole:** cosine of the frame's CLS embedding, best reference per shell.
  - **patch:** cosine at each of the 18×32 patch locations. Each location is
    weighted per shell by how consistently that shell looks there across its
    clips (leaving each clip out), minus how much background footage already
    looks like it there. So the weight sits on the graphic, not on the grass
    or drone shot behind it.
- **Colour treatments:** `rgb`; `gray` (BT.709 luma); `eq` (luma with
  per-frame histogram equalisation).
- **Label:** the shell of the best-scoring reference. **Threshold:** fixed
  before WK04 is scored, at the score where 80% of library frames are found
  (correctly labelled) in the other games, leave-one-game-out.

### Results

Booth, Scenic Comp, Scenic Droneshow and On Field are counted apart as
*camera categories*: they are shots, not graphics, and the clip list only
holds the few that were cut.

| Matcher | Colour | Graphics found | Graphics false alarms | Booth found | Camera false alarms | Label right, LOO | Label right, WK04 clips |
|---|---|---|---|---|---|---|---|
| whole | rgb | 9 / 11 | 182 | 4 / 4 | 94 | 73% | 80% |
| whole | gray | 9 / 11 | 144 | 4 / 4 | 97 | 74% | 78% |
| whole | eq | 9 / 11 | 124 | 4 / 4 | 98 | 73% | 77% |
| patch | rgb | 10 / 11 | 66 | 4 / 4 | 72 | 81% | 88% |
| patch | gray | 10 / 11 | 52 | 4 / 4 | 77 | 81% | 88% |
| **patch** | **eq** | **10 / 11** | **34** | **4 / 4** | 89 | 81% | 86% |

"Label right, LOO" is the share of library frames whose best match in the
*other* games is their own shell. "Label right, WK04 clips" is the same on
the WK04 samples inside cut clips.

**The one graphics miss is Special_01_BirdFight.** "Special" has no other
clip in the library, so it cannot be found by matching. Every findable shell
in WK04 is found by the patch matcher at the fixed threshold.

Per shell, patch + eq (found / missed, false alarms by the clip list):

| Shell | Found | Missed | False alarms |
|---|---|---|---|
| Booth | 4 | 0 | 5 |
| In The Know Classic | 1 | 0 | 1 |
| Match-Up | 1 | 0 | 14 |
| Next Week Promo | 1 | 0 | 0 |
| Player Card | 2 | 0 | 0 |
| Prime Stories | 1 | 0 | 1 |
| Repurposed Shell | 1 | 0 | 0 |
| Score | 1 | 0 | 3 |
| Special | 0 | 1 | 0 (no reference) |
| Standings | 1 | 0 | 0 |
| Who Ya Got | 1 | 0 | 0 |
| Shells not in WK04's clips | | | Scenic Comp 51, Scenic Droneshow 33, NBA Promo 8, Kelce Close Up 3, Playoff Picture 2, others 1 each |

Threshold trade-off for patch + eq (graphics found / false alarms), by the
leave-one-game-out recall the threshold is set at: 99% 10 / 543, 95% 10 /
95, 90% 10 / 58, **80% 10 / 34**, 70% 10 / 22, 50% 9 / 10. Whole-frame
+ eq never finds more than 9. From the 95% setting down, it has 1.8 to 5
times the false alarms (95%: 9 / 168, 80%: 9 / 124, 50%: 7 / 50).

### What the false alarms are

"False alarm" here means "not in the clip list". I looked at a sample of 30
of each kind by eye.

- **Graphics, patch + eq (34):**
  - 2 are uncut instances of the right shell: a "Seahawks Starting Offensive
    Line" Player List and a "7–3 End of 1st Quarter" Score.
  - 5 are real shells with the wrong label: the "49ers vs Rams, Next
    Thursday" Next Week Promo three times, labelled Match-Up; a Luis
    Gonzalez card (Player Card); a SEA–TNF–ARI logo graphic.
  - 12 are graphics that are not library shells: Stay Tuned, Halftime and
    State Farm Halftime Show titles, Post Game, Kickoff, feature-story
    titles, promos for other games, ads.
  - 11 are plain footage: stadium wides, game action, city aerials.
- **Graphics, patch + rgb (66):** the same picture, with more Prime / TNF /
  NFL logo bumpers and network packaging.
- **Camera categories:** almost none is a real uncut scenic or booth shot
  (1 booth in each sample of 30). They are black frames, and the plain
  end cards of ads (Novartis, Prime, Wingstop, Subway, Behr, ...). The
  single dark Scenic Droneshow clip makes any dark frame look like it; `eq`
  moves the same frames to Scenic Comp.

### Colour

Per TJ's note, the library will never be colour-accurate. The question is
whether features still match across that. For every WK04 and WK10 clip I
compared 3 clip frames with the exact recording frames they were cut from
(45 and 39 pairs), against 400 other moments of the same recording. WK10's
colour shift is the visible one.

| Game | Colour | whole cos | patch cos | true frame ranked first |
|---|---|---|---|---|
| WK04 (no shift) | rgb / gray / eq | 1.00 / 1.00 / 1.00 | 1.00 / 1.00 / 1.00 | 100% |
| WK10 (shifted) | rgb / gray / eq | 0.99 / 0.99 / 0.99 | 0.96 / 0.96 / 0.96 | 100% |

The colour shift costs almost nothing: every clip frame still picks out its
own recording frame, by a wider margin on WK10 than on WK04. Luma and
equalisation do not change that. Where `eq` helps is the WK04 test above:
it halves graphics false alarms against `rgb`, with the same finds.
Equalising seems to play down team colours and backgrounds when one
instance of a shell is matched against another, not to fix a colour shift.

### What this means

1. **Use the patch matcher on equalised luma.** It finds every findable
   shell in an unseen game with about 34 graphics false alarms over a 5.3 h
   show (fewer than one every 9 minutes). Whole-frame matching is clearly
   worse.
2. **Add a reject class before the shell vote.** Most false alarms are
   frames that are not ours at all: black frames, flat ad cards, network
   packaging, ads, promos. Black and flat frames can be cut with a simple
   picture test. Packaging and ads need a reference set of their own
   ("not ours"). That set is the same one the NBC / other-contributor
   categories need, so it serves both.
3. **Shells with one or two clips are fragile.** Scenic Droneshow (1 clip)
   causes 33–62 false alarms alone; Special cannot be found at all. Either
   hold such shells back until they have 3 or more clips, or require a
   stricter threshold for them. Every confirmed clip adds to this (step 5,
   feedback).
4. **The label is right less often than the detection.** Next Week Promo
   and Match-Up confuse each other (similar layout with two team logos).
   OCR (step 6) or a second, region-specific pass would separate them.
5. **The clip list is a fair answer key for graphics, not for camera
   shots.** For Booth and Scenic, scoring needs a hand-labelled sample of
   the game rather than the cut clips.

Not tried yet: other feature models (CLIP, SigLIP), patch matching with
small spatial shifts. (The reject class and a second test game follow.)

## Findings: reject class, tested on WK06

Measured November 2026 with the same script (`score --reject-manifest`).
Results are in `eval-2025WK06-PHIvsNYG.json` in the detection-eval folder.

### The gate

Before the shell vote, a sample is dropped when either test fires:

- **Picture test (black and flat frames).** A frame is flat when 90% or more
  of it sits within ±10 luma levels of its median. The cut-off was set on
  WK04's false alarms:
  - It catches all 37 black and flat frames, plus 30 of the 76 ad cards
    and packaging frames (the plain ones).
  - It catches none of the real shells, and 2 of 1420 library frames.
  - Those 2 are black frames inside clips: `WK02 Drone_01` and
    `WK11 Scenic_01_TNFLogo`. That explains Scenic Droneshow matching dark
    frames: one of its references was black. The picture test now drops
    flat frames from the references too.
- **"Not ours" reference set.** 75 WK04 frames, labelled by eye from WK04's
  144 false-alarm spans; the manifest is `engine/scripts/reject_wk04.json`.
  - **What it holds:** network packaging (Stay Tuned, Tonight, TNF / Prime /
    NFL logo bumpers, Kickoff, Thursday wipes, 2nd / 4th Quarter titles,
    State Farm Halftime Show, TNF Nightcap); ad cards and ads (Sunday
    Ticket, DraftKings, FanDuel, State Farm, Liberty Mutual, Novartis,
    Spectrum, Schwab, Big Deal Days, film trailers, ...); and other shows'
    promos (Dublin game, Our Stars, boxing).
  - **How a frame is matched:** a sample is rejected when its mean patch
    cosine (equalised luma, location by location) to the closest reject
    frame passes 0.427. That cut-off was fixed beforehand so that only 1% of
    library shell frames would be rejected (15 of 1408).
- **Built from WK04 only**, so WK06 is unseen by the gate as well as by the
  matcher. The WK06 references are every other game's clips, WK04's
  included; WK05 is still the background game.

### WK06 PHI–NYG answer key

18 library clips, all on 00:00 timecode. 16 sit exactly at their timecode
frame (42–52 dB). Two are not in the recording at all:
`BaseballCard_01_00080315` and `PlayerList_01_00112199`. A keyframe search of
the whole file finds nothing better than 17 dB (the control clip,
`Booth_01`, is found exactly). Both are early pregame graphics and were left
out of the key. That leaves 10 graphics clips and 6 camera clips: 5 Booth,
1 Scenic Comp.

### Results (patch matcher, equalised luma)

| | Graphics found | Graphics false alarms | Camera found | Camera false alarms |
|---|---|---|---|---|
| no gate | 8 / 10 | 67 | 5 / 6 | 98 |
| + picture test | 8 / 10 | 66 | 5 / 6 | 22 |
| **+ picture test + reject set** | **8 / 10** | **36** | **5 / 6** | **6** |
| whole-frame, no gate (for scale) | 8 / 10 | 101 | 5 / 6 | 244 |
| whole-frame + gate | 8 / 10 | 67 | 5 / 6 | 152 |

- **The gate costs nothing that was found.** It drops 686 of 17838 samples
  (3.8%; 295 of them flat), but only 2 of the 224 samples inside cut clips:
  one each in a Match-Up and a Who Ya Got clip, both still found.
- **The picture test is what fixes the camera categories:** 98 → 22
  (Scenic Comp 69 → 20, Scenic Droneshow 27 → 0). The reject set takes them
  to 6 (Scenic Comp 20 → 4).
- **The reject set halves the graphics false alarms:** 66 → 36. Match-Up
  goes 30 → 9.

Threshold trade-off with the gate (graphics found / false alarms; camera
found / false alarms), by the leave-one-game-out recall the threshold is set
at: 95% 8 / 63, 5 / 24; 90% 8 / 50, 5 / 8; **80% 8 / 36, 5 / 6**; 70% 8 / 30,
5 / 2; 50% 7 / 22, 0 / 0. Without the gate, 80% gives 8 / 67, 5 / 98.

**The misses are labels, not detections.**
- **Both NBA Promo clips are detected** (strength z 10 and 13 against a
  threshold of 5.4), but labelled **Promo**, a sibling shell with one
  library clip. They count as missed, and their spans as two of the 36
  false alarms.
- **Scenic_02 (Scenic Comp) is genuinely weak:** its own shell peaks at
  z 3.3.
- The three promo shells (NBA Promo, Promo, Next Week Promo) confuse each
  other, as Match-Up and Next Week Promo did on WK04.

WK06's ungated false alarms (67) are about twice WK04's (34). WK06 has far
more scenic aerials (New York skyline, Statue of Liberty, Empire State), and
the matcher labels them as shells.

### What is left after the gate

All 42 remaining false-alarm spans on WK06, by eye:

| What it is | Spans | Examples |
|---|---|---|
| Real shell, right label, just not cut | 8 | Eagles Secondary lineup (Player List), Eagles Defense stats, "NBA on Prime, Coming October 24", Black Friday Bears–Eagles promos, Steelers–Bengals promo, 2 booth shots with guests |
| Real shell, wrong label | 7 | Eagles / TNF / Giants matchup graphic over aerials, 3rd-quarter score, Times Square NBA promo, Steelers–Bengals "Next Week" over Cincinnati |
| Scenic shots | 11 | NYC skyline and night aerials, Statue of Liberty, Empire State, stadium aerials, two with a sponsor box (Depend, Subway) |
| Player close-ups | 5 | labelled "Kelce Close Up" (a one-clip shell), and they are that kind of shot |
| Plain footage | 6 | videoboard, sideline, celebrations |
| Not ours, got through | 3 | State Farm Halftime tease, Saquon documentary promo, Prime card |
| Unclear | 2 | TNF logo over the field, Eagles / Giants logo wipe |

So after the gate, almost nothing on screen is someone else's work: 3 of
42. 15 are our shells that were not cut, and 16 are scenic shots and
close-ups that the shell vote has no category for.

### What this means

1. **Keep the gate.** It is cheap and lost nothing on an unseen game. The
   picture test alone fixes the dark and flat-frame problem. The reject set
   needs topping up as ads change week to week; packaging repeats, so it
   carries over.
2. **Give scenic shots and close-ups their own classes** rather than letting
   them vote for a shell. Then the shell vote only has to say *which*
   graphic it is. Scenic is already a category in "What gets detected".
3. **Group look-alike shells before naming them.** Promo / NBA Promo / Next
   Week Promo, and Match-Up / Next Week Promo, should be one candidate with
   a suggested label. OCR (step 6) can then settle the name.
4. ~~**TJ to confirm what counts as ours.**~~ Answered: see "What counts
   as ours" below. The two quarter titles in `reject_wk04.json` are now
   `maybe_ours` and no longer reject references; rerun the WK06 measurement
   before relying on the numbers above. One frame first marked "NBA promo"
   was already out: NBA Promo is a library shell, so it is ours.

### What counts as ours (TJ, November 2026)

- **Quarter titles can be ours, or not.** We do the score going to break at
  the end of the 1st and 3rd quarters, and since this season the score
  coming into the 3rd quarter, just before the Prime Stories shell. The
  usual text is `END OF 1ST QUARTER`, `START OF 3RD QUARTER`,
  `END OF 3RD QUARTER`, but production can call for one at any other time,
  and the text can be wrong (one game this season said just `1ST QUARTER`).
  So a quarter title is never rejected outright. It is a Score candidate,
  and the reading of its text (OCR, step 6) plus where it falls in the game
  suggest whether it is ours.
- **Standings and schedules: ours is known by its layout, not its words.**
  Our graphic runs just before kickoff: the two head coaches in video boxes
  on either side, and the table in the centre. Its title contains
  `STANDINGS`, with the rest of the title changing with the leagues
  involved. The same layout is sometimes used for the remaining schedule,
  and then the title contains `SCHEDULE` (the Schedules shell). The network
  also shows standings and schedules its own way throughout the game, with
  the same words. So the word alone decides nothing:
  - **Ours or not** comes from the layout (coach boxes plus a centre table),
    which is what the patch matcher already keys on.
  - **Standings or Schedules** comes from the title text, once the layout
    says it is ours (OCR, step 6).
  - A standings or schedule graphic without that layout is the network's,
    and is logged in Other Content if it is kept at all.
- **Logo bumpers are mostly not ours, but some are.** TJ will mark ours as
  they come up, and those frames move from the reject set to the library.
  An animated bumper, or one over a scenic shot, is logged in the other
  category (Other Content) either way.
  - Not ours, and a good reject reference: the bumper used at most games of
    a view of Earth from space, with a satellite orbiting carrying the team
    logos and the TNF logo.
- **State Farm Halftime Show title: not ours.** It stays in the reject set.

## Order of work

1. ~~Verify the frame-number hypothesis on one of last season's full games.~~
   Done: see Findings.
2. ~~Fingerprint the library and one game; measure shell matching on the
   known game before anything is built into the app.~~ Done on WK04: see
   Findings: shell matching. ~~Reject class for black frames, packaging and
   ads, on a second game.~~ Done on WK06: see Findings: reject class. Next
   measuring step: scenic and close-up classes, and grouping look-alike
   shells.
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
- ~~Which game is the WKWC DEN–CHI library folder, given the WK19 GB–CHI
  recording?~~ TJ: the Wild Card game was in Chicago, so the WK19 GB–CHI
  recording is that game and goes with the WKWC library folder. The folder's
  away team (DEN) looks like a slip; matching its clips against the recording
  will confirm it.
