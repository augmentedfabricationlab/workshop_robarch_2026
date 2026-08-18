# docs/ — participant-facing workshop pages

Static Jekyll site, deployed to GitHub Pages by
`.github/workflows/jekyll-gh-pages.yml` on every push to `main`.

Live URL: https://augmentedfabricationlab.github.io/workshop_robarch_2026/

## Structure

```
docs/
├── _config.yml            site title, baseurl, sidebar cross-links
├── _includes/             head.html, sidebar.html
├── _layouts/              default.html, page.html
├── public/css/            poole.css, hyde.css, syntax.css (vendored theme)
│                          workshop.css  ← put local tweaks here
├── images/                see images/README.md for the expected file names
├── index.md               About  (layout: default)
├── program.md             ┐
├── learning_objectives.md │  layout: page + order:  → appear in the sidebar
├── toolkit.md             │  automatically, sorted by `order`
├── where.md               │
├── team.md                │
└── references.md          ┘
```

## Adding a page

Create `newpage.md` with:

```yaml
---
layout: page
title: New page
order: 35
---
```

It shows up in the sidebar by itself, positioned by `order`. No nav file to edit.

## Local preview

```
cd docs
bundle install
bundle exec jekyll serve
```

Then open http://127.0.0.1:4000/workshop_robarch_2026/ — note the baseurl.

## Publishing checklist

1. Copy this `docs/` folder and `.github/workflows/jekyll-gh-pages.yml` into the repo root.
2. On GitHub: **Settings → Pages → Build and deployment → Source: GitHub Actions**.
   This matters — if the source is set to a branch instead, the workflow builds
   but nothing gets served.
3. Push to `main` and watch the Actions tab.

## Note on the existing `docs.yml` workflow

The cookiecutter ships a `docs.yml` that builds the Sphinx API docs and pushes
them to a `website` branch via `peaceiris/actions-gh-pages`. That branch is not
what Pages serves once the source is set to GitHub Actions, so the two coexist —
but only one of them is visible at the Pages URL. If you want the Sphinx docs
published as well, put them under a subpath rather than at the root.
