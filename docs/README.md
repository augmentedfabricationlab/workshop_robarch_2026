# docs/ — participant pages

Jekyll site, deployed by `.github/workflows/jekyll-gh-pages.yml` on push to `main`.
Same setup as `workshop_foc_2026`.

Live: https://augmentedfabricationlab.github.io/workshop_robarch_2026/

A page with `layout: page` and an `order:` in its front matter appears in the
sidebar automatically. Sidebar cross-links to the other workshop are in `_config.yml`
under `related:`. Local tweaks go in `public/css/workshop.css`; the vendored
poole/hyde files stay untouched.

Local preview: `bundle install && bundle exec jekyll serve`
→ http://127.0.0.1:4000/workshop_robarch_2026/
