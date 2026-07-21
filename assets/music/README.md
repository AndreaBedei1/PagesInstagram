# Music library & licensing

The engine bakes music **into the video file** before upload. This is required:
the Meta Graph API does **not** let you attach Instagram catalogue/trending audio
via API (see `docs/PLAN.md` §2). So every track here must be one you are legally
allowed to embed in a published video.

## License safety rule

`src/music/library.py` syncs `catalog.json` into the DB. **A track with no
`license` value is stored with `instagram_safe = 0` and is never auto-selected.**
Only tracks with a recorded license/provenance are used for publishing.

## Allowed sources

- Original music you created
- Royalty-free tracks with a license that permits social/commercial embedding
- Public-domain works (and public-domain / CC0 recordings of them)
- Tracks purchased/licensed for social use

Always record the license and source URL in `catalog.json`.

## Placeholder tones (CC0)

`ice music generate` synthesizes one calm ambient pad per mood (`tone_<mood>_1.wav`)
and writes them under the category folders + a `catalog.json`. These are generated
from scratch (sine chords), carry **no third-party rights (CC0-1.0)**, and are safe
to use while you assemble a real library. They are intentionally quiet, simple pads
— replace them with proper tracks for a polished feed.

## Adding real tracks

1. Drop the audio file into the matching category folder (e.g. `assets/music/calm/`).
2. Add an entry to `catalog.json` (see `catalog.example.json`) with a real `license`.
3. Run `ice music sync` to load it into the DB.

Categories mirror the folders: calm, reflective, hopeful, peaceful, inspirational,
soft_energy, gentle_piano, ambient, soft_acoustic, light_cinematic.
