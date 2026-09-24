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

`build_image.sh` was the deploy step. It **refuses a linked git worktree** —
that path replaces prod with the whole tree and is how a feature branch
deletes other people's live work. Merge to `groundwork`, deploy from the main
checkout. `ALLOW_WORKTREE=1` overrides. Frontend has no
tests upstream; the local `yarn build` and local `verify_ui` are the initial
gates, while the in-image build and `smoke_test.py` are post-push deployment
gates. Don't run `bench run-tests` against the prod site.

**Timings (measured, per-step timing prints as it runs).** Frontend change
~75s; backend-only change ~30s, because the Dockerfile copies `crm/` BELOW
`RUN yarn build` so Python edits hit the layer cache and skip the ~50s vite
build. **User-visible downtime is ~2.4s**, the backend container swap.

Rebuilt 2026-07-28 (was: 3m27s and minutes of downtime). Four things about it
are load-bearing and easy to undo by accident:

- **The image builds `FROM ghcr.io/frappe/crm:v1.67.0`, always.** It used to be
  `docker commit` of the live prod container, which stacked a ~32 MB layer per
  deploy and never removed anything — 256 layers / 14 GB by gw228, and
  `docker commit` *pauses* the container it snapshots. Never reintroduce
  commit-based layering.
- **Layer order.** `frontend/` above `yarn build`, `crm/` below it, and
  **`ARG GIT_REV` dead last** — BuildKit invalidates everything below an ARG
  whose value changed, and GIT_REV changes every commit, so declaring it at the
  top silently voids the cache on every deploy.
- **`emptyOutDir: false`** (`frontend/vite.config.js`) plus the shared
  `crm-assets` volume. Chunks are content-hashed and a one-line component edit
  re-hashes ~124 of 127, so wiping the output dir deleted the exact files open
  tabs were still lazily importing — they 404'd, the SPA threw, and users lost
  unsaved notes. Old chunks are pruned by mtime after 7 days instead.
- **Only `backend` goes in the deploy's critical window.** websocket and the
  workers are recreated after, and the frontend container isn't recreated at
  all (it serves assets from the shared volume), so it deliberately runs an
  older image tag than the rest of the stack.

### Fast loop for iterating on UI (HMR against prod)

```bash
cd frontend && CRM_DEV_TARGET=https://crm.groundworkpro.com yarn dev
# then open http://localhost:8080/crm and log in ONCE at localhost:8080
```

Vite dev server (~1.5s start, instant HMR) serving your local `frontend/src`,
with `/api|assets|files|private|login|app|desk` proxied to prod — so you get
real prod data without deploying. `changeOrigin` is required (Frappe resolves
the site from the Host header) and `cookieDomainRewrite` scopes prod's session
cookie to localhost, hence logging in through the dev server rather than
reusing your crm.groundworkpro.com tab. Unset the env var and everything
behaves exactly as before; production builds are unaffected.

**Logging in.** Use your password at `localhost:8080` — the cookie is then
scoped to localhost and persists. **"Login with Email Link" does not work here**
and fails in two ways: the button silently does nothing if the email field is
blank (nothing is queued, nothing is logged — and it is rate-limited to 5/hour),
and even when it does send, `send_login_link` builds the URL with `get_url()`,
which reads the request `Host`. `changeOrigin` has rewritten that to
crm.groundworkpro.com, so the emailed link points at PROD and logs you into
prod, not localhost. If you want the passwordless route anyway, request the
link then hand-edit the host to `http://localhost:8080/...` before opening it —
the key is validated server-side and the Set-Cookie comes back through the
proxy with the domain rewritten.

**Caveat: you are on the real production database** — anything you create, text
or delete is real.

**The server publishes itself to the OTHER Mac automatically.** Chrome automation
defaults to the mini's Chrome, where `localhost` is the MINI — so a vite server
on the laptop is invisible to the browser doing the verifying, and every UI check
otherwise has to be driven from whichever machine happens to be running vite. On
startup `yarn dev` now opens an `ssh -R` remote forward to the peer Mac, so
`http://localhost:<port>/crm` is the correct URL **from either machine** and
nothing has to be rewritten. It prints `[crm-dev] mirrored onto mini-ts: …`, and
`/__crm_dev` reports `peer`. Disable with `CRM_DEV_PEER=`, retarget with
`CRM_DEV_PEER=<ssh-host>`.

- It is loopback-only on the far side (no `GatewayPorts`) **on purpose**: the
  proxy carries a prod API key, so binding vite to `0.0.0.0` would hand the whole
  LAN that user's session.
- A `tailscale serve` mapping is published too (`https://<host>:<port+1000>/crm`,
  tailnet-only, torn down on exit) — but **do not rely on it from the mini**: the
  mini resolves `*.ts.net` through PUBLIC DNS and gets the Funnel ingress
  addresses, so a tailnet-only URL times out there while the same host answers
  fine over raw TCP (`nc -z 100.x.x.x 9080` succeeds). That is why the peer
  mirror uses the `-ts` ssh aliases, which are tailscale IPs and need no MagicDNS.
- Vite's DNS-rebinding guard rejects a non-localhost Host header, hence
  `allowedHosts: ['.ts.net']`.

**The `crm-dev-boot` plugin is load-bearing — don't trim it.** Production renders
`crm.html` through jinja and injects exactly three globals: `site_name`,
`csrf_token`, `sysdefaults`. The dev server renders `index.html` itself and gets
none of them, so the plugin re-creates two:

- **`sysdefaults`** (fetched live from System Settings). `stores/meta.js`,
  `utils/index.js` and `utils/numberFormat.js` dereference it WITHOUT optional
  chaining (`window.sysdefaults.currency`, `.date_format`, `.float_precision`),
  so its absence throws mid-render. Symptom was the Lead activity feed stuck on
  "Loading…" forever while every request returned 200 — the failure surfaces
  nowhere near its cause.
- **`site_name`** — the socket.io NAMESPACE. Without it realtime silently
  connects to `/undefined` and never receives an event.

`csrf_token` is deliberately not injected: token auth skips the CSRF check and
FilesUploader already guards on the global being present.

**Realtime works in dev**, verified end-to-end: a task created via the API from
outside the browser appeared in the DOM live, and disappeared again on delete.
It needs all three of the above plus `socketio: false` on the FrappeUI plugin,
so frappe-ui's own hardcoded `:9000` socket doesn't fight the app's.

Harmless leftover: one console error, `Unexpected token '<'`, from a call to
`frappe.onboarding.get_onboarding_status` made with a RELATIVE url — it resolves
against the current route, hits vite, and gets index.html back.

### Several agents / worktrees at once

```bash
CRM_DEV_TARGET=https://crm.groundworkpro.com yarn dev    # same command in every worktree
```

- **Ports are claimed automatically — agents need no coordination.** Each
  server takes the first free port in **8080-8099** and prints
  `[crm-dev] worktree "<dir>" (<branch>) -> http://localhost:<port>/crm`.
  Run the identical command in five worktrees and you get five servers. Set
  `CRM_DEV_PORT` only if you want a fixed one; it is then honoured strictly.
  - **GOTCHA — the free-port probe must CONNECT, not bind** (fixed gw330; it
    got this wrong two different ways first). Bound to `127.0.0.1` it misses a
    server holding only `::1`, and vite binds `localhost`, which resolves to
    `::1` FIRST here — so another worktree's live server read as free. Bound to
    the wildcard instead it *still* succeeds, because Node sets `SO_REUSEADDR`
    and macOS lets a wildcard bind coexist with a specific one. Either way we
    claimed a port someone else was serving on and `strictPort` then killed our
    OWN start with "Port 8080 is already in use" — while that port visibly
    worked in a browser, because it was the other agent's app answering. A
    successful TCP connect to `localhost` is the unambiguous question.
- **`strictPort: true` regardless.** Vite's default is to bump a busy port,
  which is the dangerous failure: you open 8080 and get a *different
  worktree's* bundle with nothing to indicate it — the same trap as the
  Electron apps. Now it fails with `Port 8080 is already in use`.
- **How an agent learns its own port** (never assume 8080):
  ```bash
  cat frontend/.dev-port                      # this worktree's port
  curl -s localhost:<port>/__crm_dev          # {dir, branch, path, port}
  ```
  For certainty rather than trust, match on the resolved root — two worktrees
  can share a basename, and a stale `.dev-port` outlives a crashed server:
  ```bash
  ROOT=$(git rev-parse --show-toplevel)
  for p in $(seq 8080 8099); do
    curl -s --max-time 1 localhost:$p/__crm_dev | grep -q "\"path\":\"$ROOT\"" && echo $p
  done
  ```
- **Every dev page carries a corner badge** (`<dir> · <branch> · :<port>`,
  bottom-right, `pointer-events:none`). Servers can no longer overlap; the
  badge is what stops *humans* overlapping — judging a change from the wrong
  worktree's tab is the realistic mistake once several are open.
- **Put worktrees OUTSIDE Dropbox** (`~/crm-worktrees/…`, not `.worktrees/`
  inside the repo). Each needs its own `node_modules`, and ~400 MB landing in a
  synced folder sends Dropbox + Spotlight to ~50% CPU each and load average past
  10 — it silently doubled build times mid-measurement.
- **`node_modules` per worktree is now mandatory**, not optional: the fork pins
  its own vite/plugin versions, so a worktree on an older commit genuinely needs
  a different tree. Don't symlink a shared one.
- **The `sites/common_site_config.json` stub is no longer needed.** Nothing
  imports it since socket.js stopped reading `socketio_port` — a worktree builds
  with no scaffolding at all.
- **Deploys serialise safely.** `build_image.sh` takes a machine-wide lock
  (`/tmp/frappe-crm-build.lock`), the next `gwN` is read from the SERVER's pin
  so two agents can't collide on a tag. Worktrees are for local `yarn dev`
  only; a deploy from one is refused. The shared `crm-assets` volume is
  additive, so one agent's chunks never delete another's.
- The dev API token is shared (Infisical), so every agent's dev server acts as
  the same user. Fine on one laptop; worth remembering if a session looks like
  it is "someone else's" activity.
- **(Historical — solved by the server-side merge in `push_deploy.sh`, 2026-09-24.
  Kept for the image-build path, `build_image.sh BASE_UPGRADE=1`.)**
  **PUSH BEFORE YOU DEPLOY, PULL BEFORE YOU BUILD — this is the one that bites
  ACROSS MACHINES.** The serialisation above only protects agents on the *same*
  laptop; the lock, the tag counter and the assets volume say nothing about
  whether the tree you're shipping is current. `build_image.sh` ships the
  deployer's whole tree against a fixed base image, so a deploy from a stale
  checkout doesn't merge — it **replaces** prod's app code, silently deleting
  every feature committed since that checkout. Nothing warns you: the build
  succeeds, `smoke_test.py` passes (it only asserts prod matches *your* repo,
  which it now does), and the regression surfaces days later as "feature X
  stopped working".
  - **2026-07-31, the case in point.** gw256/gw257 (buyer drag, buyboxes,
    delete access, IL duplicate-buyer race fix) were committed on the MBP but
    never pushed. Next morning an agent on the **mini**, whose checkout was at
    `8b8ea82d` (six commits behind), deployed gw258 to ship Showing Access.
    Prod lost the drag, the buyboxes, the call classification badges, buyer
    import lists, lead photos AND the duplicate-buyer race fix. Recovered by
    merging both sides and redeploying as gw259.
  - **Diagnosing it takes one command** — the image records the tree it was
    built from, and `-dirty`/an old sha is the tell:
    `docker image inspect ghcr.io/frappe/crm:<tag> --format '{{json .Config.Labels}}'`
    → `org.opencontainers.image.revision`. Compare against `git log` before
    assuming the feature's own code broke.
  - So: `git push` the moment a feature is committed (an unpushed commit is
    invisible to the other machine and *will* be clobbered), and
    `git pull` immediately before `build_image.sh`.

**Don't tune `yarn build` flags — they do nothing.** Measured on Vite 5:
baseline 42s, `--minify false` 39s, `--sourcemap false` 39s, both 41s, dropping
vite-plugin-pwa 38s. `lucideIcons: false` just fails the build. Sourcemaps are
68% of output SIZE but almost none of the TIME, so they stay on.

**The bundler is Vite 8 (Rolldown), ahead of upstream** — adopted 2026-07-28,
~40s → ~25s (36s → 18s best case). Upstream frappe/crm is still on vite ^5.4.21
and frappe-ui's develop only reached vite ^7, with no Rolldown work in flight,
so a rebase may try to drag this backwards — keep the pins.
**`vite-plugin-pwa` must stay >= 1.x.** On 0.21.x the build still *succeeds*
under Rolldown but silently drops `registerSW.js` and `manifest.webmanifest`,
so the self-destroying service worker never registers and users get stale
bundles from an old SW — the exact bug `selfDestroying` exists to prevent, and
invisible unless you diff the output.

`scripts/verify_no_drift.py` (also run by `smoke_test.py`) asserts prod matches
this repo file-for-file. It exists because the old `docker cp`-based deploy had
no delete semantics, so files removed from the repo lived on in prod for over a
year — including an abandoned module with a live whitelisted endpoint.

## Upstream sync

Upstream moves fast (v1.73+ as of 2026-06). To take upstream changes: rebase
`groundwork` onto the target tag, verify the image-tag-contents problem is
fixed (or build our own image), re-test everything in ../frappe-crm-deploy/CLAUDE.md.
