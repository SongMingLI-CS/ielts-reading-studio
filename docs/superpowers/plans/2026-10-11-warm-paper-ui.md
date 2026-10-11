# Warm Paper UI Implementation Plan

**Goal:** Extend the existing reading practice visual language to all shared-shell application pages.
**Architecture:** Keep the standalone practice/result workspace intact. Map shared tokens to its warm palette, consolidate practice-library styles, and finish the shared UI pattern layer with consistent bounded surfaces. Preserve routes, forms, IDs, keyboard behavior and mobile navigation.
**Tech stack:** Jinja2, CSS, FastAPI, pytest, browser rendering.

- [x] Align shared tokens with practice colors; retain readable secondary text and semantic success/error states.
- [x] Consolidate the reading list into a bounded paper surface with separators and aligned actions.
- [x] Apply the same surface, control, navigation and spacing language to home, writing, review, knowledge, learning, novel, materials, jobs, settings and supporting pages.
- [x] Verify existing web contracts and routes; render representative populated desktop and mobile pages and check horizontal overflow.
- [x] Review the diff and report actual verification results and limitations.
