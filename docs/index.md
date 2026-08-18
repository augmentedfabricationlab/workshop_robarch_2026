---
layout: default
title: About
---

# About

**What if damage were a prompt: which repair strategies might it generate, and which of them can a robot carry out?**

<figure>
  <img src="{{ '/images/title_image.jpg' | relative_url }}"
       alt="From damaged half-timbered wall to assembly model to robotic repair action">
  <figcaption>From damaged structure, to assembly model and damage state, to a repair sequence shared between human and robot.</figcaption>
</figure>

This workshop connects high-level repair design with situated robotic perception, planning, and action. Its central contribution is a Multimodal Reasoning-to-Action workflow that translates textual observations, visual documentation, and geometry files of a building structure into a structured **Assembly Model** describing components, connections, and damage states.

That model is the basis for exploring repair designs and generating an **Action Model** containing structured repair sequences that combine human- and robot-executable actions. Robotic routines enable precise operations such as scanning, marking, or milling, while complementary steps — material removal, surface treatment, assembly — are performed manually.

As a case study, participants engage with the repair of a curated set of damaged beams in a half-timbered wall, prototyping interventions through iterative cycles of planning, execution, and evaluation.

---

## What you need

A laptop with **Rhino 8**. Everything runs in-process in Rhino 8 CPython, so there is no server, no worker process, and nothing else to install.

Repository: [workshop_robarch_2026]({{ site.github.repo }})
