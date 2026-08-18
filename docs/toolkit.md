---
layout: page
title: Toolkit
order: 30
---

Everything runs **in-process in Rhino 8 CPython**. There is no separate server, no worker process, and no second repository to clone.

## What to bring

- A laptop with **Rhino 8** installed (a trial licence is sufficient for the workshop week)
- A GitHub account, so you can push your results back
- A phone or tablet, used for photographic capture on site

## Setup

1. Clone this repository:

   ```
   git clone https://github.com/augmentedfabricationlab/workshop_robarch_2026.git
   ```

2. Set the environment variable `ROBARCH_REPO` to the folder you just cloned. The Grasshopper components resolve the repository path at runtime from this variable, falling back to a walk-up from the saved `.gh` file, so nothing is hardcoded to one machine.

3. Open the Grasshopper definition in `rhino/` and confirm the components load without errors.

<span class="tbd">exact file names and API key handling to be added before the workshop</span>

## How the components are wired

The Grasshopper components are thin loader stubs. Each one `exec()`s a real Python file from `rhino/components/`, so the actual logic can be edited in a text editor and re-run without re-pasting anything into Grasshopper. If you want to change behaviour, edit the file, not the component.

## The joint catalogue

Repair joints live in a catalogue folder as datasheets. Each entry carries a German title (which doubles as the dropdown label), an English description, and alternative names in English and Japanese where they exist. The picker component fills its value list from that folder, so adding a joint means adding a datasheet, not editing a component.

Participants are welcome to author additional joints during the workshop; the authoring and validation components are part of the toolkit.

## Corpus

Reference cases live under `data/corpus/`. It is meant to grow: what you document during the workshop becomes part of it.
