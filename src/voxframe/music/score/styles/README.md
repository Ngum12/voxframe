# Score styles

Each `.toml` file here is one style of generated score (D-176). Voxframe
reads every file in this folder, so adding a file adds a style, with no code
change. The loader checks every value and says what is wrong and where.

## What a style sets

```toml
name = "inspiring"            # the file's id: lower case, no spaces
label = "Inspiring"           # shown in the app
description = "..."           # one line, shown under the label
order = 1                     # position in the list

[key]
tonics = ["D", "E", "C"]      # major keys a video may be in; one is chosen per video
home = "I"                    # the chord it ends on: "I" (major) or "vi" (minor)
lift_last_section = true      # the last section a whole step up
modulate = 0.0                # chance (0 to 1) that a later section changes key
modulations = [5, -2]         # by how many semitones, when it does

[tempo]
min = 94                      # beats a minute: one tempo is chosen per video,
max = 106                     # and it breathes slightly from chord to chord

[energy]
offset = 1.4                  # how high the energy sits for the same speech
min = 1                       # the energy level never goes below this
max = 4                       # or above this (levels are 0 to 4)

[harmony]
progressions = [["I", "V", "vi", "IV"], ["vi", "IV", "I", "V"]]
chord_bars = [2, 2, 2, 1, 4]  # bars per chord, chosen at random each time

[rhythm]
patterns = [[0, 0, 1, 0, 0, 1, 0, 2]]   # ostinato: eight eighth notes over
                                        # (0 root, 1 fifth, 2 octave, 3 third)
accents = [1.0, 0.62, 0.7, 0.95, 0.62, 0.7, 0.92, 0.66]

[instruments]
leads = ["piano", "violins", "cellos"]  # who carries the melodic line, by section
arpeggio = "piano"                      # "piano", "harp" or "none"
arpeggio_eighths_from = 3               # the level from which arpeggios run in eighths

[layers]                      # the energy level at which each part enters;
pad = 0                       # leave a part out to never use it
string_pads = 1

[levels]                      # group volumes, 1.0 as designed
ostinato = 1.0
```

## Chords

Chords are Roman numerals in the major key: `I`, `ii`, `iii`, `IV`, `V` and
`vi`, each optionally followed by `7` or `maj7`.

## The parts in `[layers]`

| part | what it is |
|---|---|
| pad | a soft synthesised pad under everything |
| melody | the sparse piano melody, from its level up to `melody_until` |
| string_pads | sustained cellos and violas |
| contrabass | sustained contrabass |
| high_violins | sustained violins high above |
| sub_bass | a synthesised sub bass |
| arpeggio | piano or harp arpeggios |
| lead_line | the section lead's slow melodic line |
| cello_ostinato, viola_ostinato, violin_ostinato | short-bowed ostinatos |
| chord_hits | timpani and bass drum on each chord |
| driving_percussion | timpani in 3-3-2, and bass drum every bar |
| section_swell | a cymbal swell and a timpani roll into a new section |
| soft_section_roll | a soft timpani roll into a new section |
| gong | one gong, at the first section start at this level |
| pause_roll | a timpani roll through a long pause |
| pause_riser | a synthesised riser through a long pause |
| final_hit | (`true` or `false`) a low hit with the last word |

## The groups in `[levels]`

`keys`, `strings`, `ostinato`, `percussion`, `pads`, `bass` and `effects`.
