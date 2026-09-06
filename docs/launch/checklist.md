# Launch checklist

## Pre-launch gates (all must be green before any channel)

- [ ] PyPI release published; `uvx claude-drift scan` works from a clean machine.
- [ ] `docs/demo.gif` renders on the GitHub README page (check on github.com, not locally).
- [ ] README numbers match `docs/examples/first-report.md` and the stored run
      (`drift report --run 20260906-095910`).
- [ ] CI green on `main`: `pytest -q`, `ruff check .`, `mypy`.
- [ ] Repository description, topics, and LICENSE set; issues enabled.
- [ ] A second person has run `drift scan` on their own machine without hitting an error.

## Channels, in order

1. **Show HN** — Tuesday to Thursday, 14:00–16:00 UTC. Title from `show-hn.md`. Post the
   prepared limitations comment yourself within the first ten minutes, then stay on the thread
   for the next four hours to answer.
2. **awesome-claude-code list** — open a PR adding the project to the tooling section. Link the
   README's control section rather than the repository root.
3. **r/ClaudeAI** — same finding, rewritten as a post rather than a launch. Lead with the 80%
   versus 35% contrast; put the install line at the bottom.
4. **anthropics/claude-code Discussions** — Show and Tell category. Frame it as a measurement
   method for release notes, and ask directly whether the `-p` regime difference can be reduced.
5. **X thread** — four posts: the finding, the report screenshot, why the control matters, the
   install line. Use a still from `docs/demo.gif` for the screenshot.
6. **GeekNews (news.hada.io)** — Korean summary with the same numbers, linking the GitHub repo.

## After

- [ ] Log every reported failure mode as an issue the same day.
- [ ] If someone posts their own run, ask to link it from `docs/examples/`.
