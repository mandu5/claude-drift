# Launch checklist

## Pre-launch gates (all must be green before any channel)

- [x] PyPI release published (0.1.1); `uvx claude-drift scan` verified.
- [ ] `docs/demo.gif` renders on the GitHub README page (check on github.com, not locally).
- [ ] README numbers match `docs/examples/first-report.md` and the stored run
      (`drift report --run 20260906-095910`).
- [x] CI green on `main`: `pytest -q`, `ruff check .`, `mypy`.
- [ ] Repository description, topics, and LICENSE set; issues enabled.
- [ ] A second person has run `drift scan` on their own machine without hitting an error.

## Channels, in order

1. **Show HN** — Tuesday to Thursday, 14:00–16:00 UTC. Title from `show-hn.md`. Post the
   prepared limitations comment yourself within the first ten minutes, then stay on the thread
   for the next four hours to answer.
2. **GeekNews (news.hada.io)** — Korean summary from `geeknews.md`, same numbers, link the repo.
   Account rhdudals0505 was created 2026-09-06 and GeekNews blocks posting for 7 days after
   sign-up, so the earliest submission is 2026-09-13. Use the 글등록 page, type Show (프로젝트 소개).
3. **r/ClaudeAI** — same finding, rewritten as a post rather than a launch. Lead with the 80%
   versus 35% contrast; put the install line at the bottom.
4. **anthropics/claude-code Discussions** — Show and Tell category. Frame it as a measurement
   method for release notes, and ask directly whether the `-p` regime difference can be reduced.
5. **X thread** — four posts: the finding, the report screenshot, why the control matters, the
   install line. Use a still from `docs/demo.gif` for the screenshot.
6. **awesome-claude-code (hesreallyhim)** — NOT eligible until the repo is 14 days old with
   continued commits, or has 100 stars. Submission is a web issue form filled by a human
   (no PR, no gh CLI): https://github.com/hesreallyhim/awesome-claude-code/issues/new?template=recommend-resource.yml
   Description must be one line, factual, no emojis. Earliest date: 2026-09-19.

## After

- [ ] Log every reported failure mode as an issue the same day.
- [ ] If someone posts their own run, ask to link it from `docs/examples/`.
