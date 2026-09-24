# Frappe CRM fork — Groundwork

**Keep end-of-turn summaries SHORT** (Lance, 2026-08-17: "reduce by 80%"). Lead
with the result, name only what he has to decide or act on, and stop. The work is
in the commits and the docs — the reply is not where it gets re-explained.

Fork of frappe/crm (github.com/lancejohnson/crm). Working branch: **groundwork**,
based on upstream tag **v1.67.0** — the last upstream release whose published
image actually contains the crm app (their image CI is broken after it).

**This repo is the source of truth for UI/app-code changes.** Deployment,
server scripts, infra, and all operational context live in the ops repo:
`../frappe-crm-deploy` (read its CLAUDE.md first).

## Before starting a feature — check what else is in flight

Lance often runs several Claude sessions/agents at once. **Before building anything,
check that another agent isn't already on it** (this exact check surfaced an
already-merged `feature/daily-call-review` branch and avoided rebuilding it):

```bash
git worktree list      # other agents' isolated worktrees
git branch -a          # existing feature branches (incl. related-sounding ones)
```

If a related branch/worktree exists, read it first and build on it rather than
duplicating. Work substantial features in a worktree of your own.

## Our changes vs upstream (keep this list current)

The full list lives in **`docs/upstream-diff.md`**, one entry per change, newest first.
It is too big to load on every turn, so it is not inlined here.

- **Before touching an area**, grep it for the feature, file, or doctype
  (`grep -n -i "kanban" docs/upstream-diff.md`) and read the matching entries.
- **After changing behaviour**, add an entry at the top of that file in the same
  format: bold title, date, and what changed and why. Do not add entries here.

## Testing & verification — local first (prod-backed dev)

The full local backend/database mirror was **removed (2026-06-19)**; there is no
`dev.sh`/`docker-compose.dev.yml`, and one should not be recreated. Frontend work
is nevertheless tested **before push/deploy** through the local Vite dev server,
which serves the local source while proxying authenticated API/realtime traffic
to prod. Production must never be the first place a UI change is exercised.

- **GOTCHA — `docker cp`ing a changed `.py` onto prod does NOT change what the
  WEB path serves.** The gunicorn workers already imported the module, so an
  HTTP request keeps running the old code even after the file is replaced and
  `__pycache__` is cleared. `bench execute` forks a fresh process and therefore
  picks the change up immediately — which makes this very easy to misdiagnose:
  the bench smoke test shows new fields while the browser shows stale data.
  `docker compose restart backend` (~seconds, same window as a deploy) is what
  actually reloads it. Cost a full failed verification round on 2026-08-05.
- **Backend logic** — validate read-only against the live DB with `bench
  execute` / `bench console` on the prod backend; roll back anything that writes:

  ```bash
  ssh groundwork-apps "cd /opt/frappe-crm && docker compose exec -T backend \
    bench --site crm.groundworkpro.com execute <dotted.path.func> --kwargs '{...}'"
  ```

  Read-only queries (SELECTs / report builders) are safe to run as-is. To probe
  unmerged code without deploying, `docker cp` the module into the backend
  container under a throwaway name, `execute` it, then remove it. Any snippet
  that writes must end in `frappe.db.rollback()`.
- **Compile gate — before push/deploy.** Run `cd frontend && yarn build`; it must
  succeed (there are no upstream frontend tests). Worktrees need their own
  `node_modules`; no sites config stub is required. The later in-image build is
  a second gate, not the first test.
- **Visual / UI verification — MANDATORY before push/deploy for any UI change.**
  Start the prod-backed local server with
  `cd frontend && CRM_DEV_TARGET=https://crm.groundworkpro.com yarn dev`, read
  the actual port from `frontend/.dev-port`, and call π's `verify_ui` against
  `http://localhost:<port>/crm`. If the relevant device target is not already
  known, call `verify_ui` without a target first and ask Lance which target
  matters. Exercise the changed behavior (click, type, unfold, save/reload where
  safe) and verify the resulting state; merely loading the page is insufficient.
  Complete this local verification before committing/pushing or running
  `build_image.sh`. The dev page uses the real production database, so avoid or
  roll back destructive test data.
- **After deploy** — run `smoke_test.py` and make only a focused production
  spot-check for deploy/cache/auth differences. This is confirmation, not the
  initial test pass; do not push a change merely to make it testable.

## Ship a change

Do not deploy in order to test. The order is local compile + local `verify_ui`,
then commit/push, then deploy and smoke-test:

```bash
# 1. Before commit/push/deploy (for frontend/UI work)
cd frontend && yarn build
CRM_DEV_TARGET=https://crm.groundworkpro.com yarn dev
# read .dev-port and run verify_ui against http://localhost:<port>/crm

# 2. After the local checks pass, commit and push this app repo
# 3. Pull the latest deploy repo, then deploy and smoke-test
cd ../../frappe-crm-deploy && git pull
./scripts/push_deploy.sh && python3 scripts/smoke_test.py
```

**Deploys are a git push since 2026-09-24** — prod runs this repo from a host
checkout, no image build. **Deploy from your own worktree/branch; do not
rebase.** Run `../frappe-crm-deploy/scripts/push_deploy.sh` from inside the
worktree (it detects it): the host merges your commit into whatever is live,
runs `crm/tests/run_unit.py` on the merge, and ships it. Exit 2 = you and live
edited the same lines — `git merge live/groundwork`, fix, re-run. Exit 3 =
tests failed on the merged tree. Clean tree required. It then rebuilds the
frontend only if `frontend/` changed and restarts the backend (standby-backed,
no outage) only if Python changed. ~17s Python, ~55s frontend. Details in
`../frappe-crm-deploy/CLAUDE.md` → Workflows. `build_image.sh` is now ONLY
for Frappe/base upgrades (`BASE_UPGRADE=1`); the rest of this section
describes it.

`build_image.sh` is now only for Frappe/base upgrades, and it refuses a linked worktree.
Its history, layer-order rules and timings are in `docs/local-dev-and-image-build.md`.
Don't run `bench run-tests` against the prod site.

### Local UI loop (HMR against prod)

```bash
cd frontend && CRM_DEV_TARGET=https://crm.groundworkpro.com yarn dev   # same command in every worktree
```

- Serves your local `frontend/src` with the API proxied to **production**. Anything you create is real.
- Log in once with your password at the printed `localhost` URL. "Login with Email Link" does not work here.
- Each server takes the first free port in 8080-8099 and writes it to `.dev-port`. Never assume 8080.
- It mirrors itself to the other Mac, so the same `localhost:<port>` works from the Mini's Chrome.
- One `node_modules` per worktree, and keep worktrees outside Dropbox (`~/crm-worktrees/`).
- Don't trim the `crm-dev-boot` plugin, don't tune `yarn build` flags, and keep `vite-plugin-pwa` at 1.x or later (0.21 silently drops the service worker).

Before debugging the dev server, the ports, realtime, or the build, read
`docs/local-dev-and-image-build.md`. It has the gotchas and how each was found.

## Upstream sync

Upstream moves fast (v1.73+ as of 2026-06). To take upstream changes: rebase
`groundwork` onto the target tag, verify the image-tag-contents problem is
fixed (or build our own image), re-test everything in ../frappe-crm-deploy/CLAUDE.md.
