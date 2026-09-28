# NOTICE — what this repository is, and what it is not

## This repository

Everything in this repository is original work by its author, licensed under
the GNU Affero General Public License v3.0 (see `LICENSE`). It was written to
sit *beside* BirdNET-Pi and AvianVisitors, not inside them:

- it reads `birds.db` and the extraction folder that BirdNET-Pi produces;
- it reads and writes its own files, services and endpoints;
- it does not copy, modify or embed code from either project.

Measured, not assumed: the code here was compared line-by-line against the
BirdNET-Pi and AvianVisitors source trees before publication. The overlap is
1.9 % of lines, all of it boilerplate that any program contains
(`WantedBy=multi-user.target`, `if __name__ == "__main__":`,
`header('Content-Type: …')`).

## What it depends on, and their licences

| work | licence | how this repo relates to it |
|---|---|---|
| **BirdNET model V2.4** (Cornell Lab of Ornithology / TU Chemnitz) | CC BY-NC 4.0 | not included; runs on the Pi via BirdNET-Pi. Non-commercial use only. |
| **BirdNET-Pi** (Patrick McGuire; maintained by Nachtzuster) | CC BY-NC-SA 4.0 | not included; this repo reads its output |
| **AvianVisitors** (Teddy Warner) | CC BY-NC-SA 4.0 | not included; `vogelbilder-nachziehen.py` *calls* its `generate_one.py` as a subprocess but contains none of it |
| **Piper** (Rhasspy) | MIT | narration engine for the videos, installed separately |
| Piper voices `de_DE-thorsten`, `de_DE-kerstin` | CC0 | downloaded at setup, not included |
| **ffmpeg**, **Caddy**, **Pillow**, **numpy** | LGPL/GPL, Apache-2.0, HPND, BSD | called as tools or imported as libraries, not included |

## Changes to AvianVisitors itself

The author also runs a modified AvianVisitors front end (live band, call
comparison, waveform button). Those are edits to AvianVisitors' own files and
therefore stay under CC BY-NC-SA 4.0. They are **not** in this repository; they
live in a public fork of AvianVisitors where the licence and the attribution
chain (McGuire → Warner → fork) are carried by the fork itself.

## A plain-language consequence

Because this repository is useless without BirdNET-Pi and the BirdNET model,
and both are non-commercial, the practical effect is: you can run all of this
for yourself, for research, for education, and you can give it away. You cannot
sell a product built on it without a separate agreement with the BirdNET team
and the AvianVisitors author — whatever this repository's own licence says.

## Not included, on purpose

No bird photographs (each carries its own licence, some non-commercial), no
Piper voice files, no BirdNET model, no Gemini-generated illustrations, and no
Wikipedia text. Each is fetched at runtime or at setup by the scripts here, and
the scripts record the source.
