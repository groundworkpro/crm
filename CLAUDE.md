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

- **Status lifecycle: real deletion + a rename/delete guard** (2026-09-22) —
  deleting a kanban column used to only set `delete: true` on the saved view,
  so the status survived, stayed assignable, and any lead given it vanished
  from the board (this is what hid "Signed Contract"). Now the column's
  Delete calls `crm.api.lead_status.delete_status` (manager-only): refuses
  while records sit in the status (optional `move_to` migrates them via
  `doc.save()`), deletes the `CRM Lead Status` row, and strips the column
  from every user's saved view (`columns` + `kanban_columns`). The same
  module is the **status guard**: `watched_statuses()` is DERIVED from the
  code constants that match statuses by name (`CHASE_STATUSES` /
  `CLOSER_STATUSES` / `POST_CONTRACT_STATUSES` / `ACQ_STATUSES` /
  `DISPO_STATUSES` / `DISPO_LEAD_STATUSES`), and `CRM Lead Status`
  on_update/on_trash hooks + a `daily_long` integrity sweep email Lance when
  a watched status is renamed, deleted, or simply missing. Motivation: "Needs
  Listing"/"Marketing to Buyer" had been renamed to "Submit to Dispo"/
  "Dispo Accepted" and the code kept matching the old names — Dispo Accepted
  leads were getting daily Today nudge cards. **"Signed Contract" was retired
  through this flow** (zero leads sat in it; signed deals rest in Contract
  Sent until photos start, so they keep closer cards in that gap — Dennis
  moves them within a day). Acquisition scope is now New → Contract Sent;
  dispo starts at Photos & Lockbox. `get_data` now returns `doctype` so the
  board can map to its status doctype. Tests: `unit_test_lead_status.py`;
  `frappe_shim` gained query-builder stubs. Deploy needs `sync_jobs` (new
  daily_long hook).

- **Next workspace (Telnyx desk + Talk), backend** — staging-first; the
  build contract is `docs/next-workspace-build.md`. Four pieces:
  - **Gating** (`crm/api/workspace.py`): `get()` → `{version, allowed}`,
    `set_version()`. `next` is offered to and accepted from site_config
    `crm_next_users` only (default `[lance.johnson@…]`); a malformed key
    degrades to that default, never to everyone. Stored as user default
    `crm_workspace_version` (+ `defaults._clear_cache("__default")` after the
    write — same stale-read trap as Lead Assignment). Classic is byte-identical.
  - **Talk** (`crm/api/talk.py`; ops `setup_talk.py`: `CRM Channel` slug-named
    with `members` child, `CRM Message` with UNIQUE `mm_post_id`, `CRM Channel
    Read`; seeds `acquisitions`/`dispo`/`ops-bot`/`standup`(bot)). Membership is
    the access model (public channel = no members listed = everyone). Unread is
    one grouped pass (`unread_counts`, pure). Realtime `crm_talk` (site-wide,
    after commit) / `crm_talk_read` (user) / `crm_presence`. Bot posts
    (`post_as_bot`) carry `props.mirror=false` when the Mattermost DM already IS
    the copy: the 5am standup lands in `#standup`, BatchData wallet alerts in
    `#ops-bot`, both still DM'd. The live-one alert now ALSO writes a `CRM
    Notification` (type **Live one** — `setup_notification_types.py` widens the
    Select via Property Setter, no `bench migrate` here), a Talk DM rep→closer,
    and publishes `crm_live_one` to the closer; Mattermost DM kept.
  - **Mattermost two-way sync** (`crm/integrations/mattermost/`). Mattermost
    stays source of truth during the transition; the sync dying loses nothing.
    Outbound: `CRM Message` hooks enqueue `sync.mirror_message` after commit;
    posts as the author via `mattermost_user_tokens` PAT, else the bot with a
    `**Name:**` prefix; `props.crm_origin=1`; stores `mm_post_id`. Inbound:
    `webhook.event` (allow_guest) verifies HMAC-SHA256 over the RAW body in
    `X-Groundwork-Signature` with `mattermost_sync_secret`, **fails closed**
    without it, constant-time compare. Loop guard = `props.crm_origin` OR a
    known `mm_post_id` (a test caught the first cut comparing against the CRM
    name). Unknown channel → auto-created from MM type (O/P→channel, D→dm);
    users by email; unmapped author → bot + `author_label`. `sync.backfill`.
    The forwarder lives in `Projects/Groundwork/mattermost/agent-listener`.
  - **Telnyx desk** (`crm/api/telephony.py` write side + pure
    `crm/integrations/telnyx/desk.py`; ops `setup_phone_lines.py`: `CRM Phone
    Line` with `owner_user` — `owner` is a Frappe column — and members with
    SEPARATE `view`/`use`/`ring`; `CRM Telephony Settings` Single). **Conference-
    first**: `dial()` rings the REP first (softphone SIP URI from their
    telephony credential, else their cell), the webhook creates `crm-<desk_id>`
    on answer and dials the other party into it. Legs are told apart by
    `client_state.kind` (rep/peer/caller/ring/supervisor); internal legs never
    get a CRM Call Log — only the external one, still written by the webhook.
    Inbound on a line with ring members: one ring leg per member (+cell when
    `ring_cell_fallback`, off by default — carrier voicemail can win), first
    answer wins, losers hung up, `crm_incoming` to members; nobody → existing
    voicemail path. `join(call_log, monitor|whisper|barge)` rings the closer
    with a supervisor leg; on answer `conferences/{id}/actions/join` with
    `supervisor_role` (+`whisper_call_control_ids=[rep_leg]`), `beep_enabled:
    never`, then `play`/`speak` scoped to `call_control_ids=[rep_leg]` — the
    rep-only tone. Recording starts at bridge on the external leg when
    `line.recording` resolves on (inherit → settings default). Transient call
    state = Redis hash `crm:telnyx:desks` keyed by desk id (+ `..._by_call_log`);
    the durable record is the Call Log. `history(number)` unions calls + texts
    across providers by last-10. GOTCHA to verify on staging: dual-channel
    recording on the EXTERNAL leg may invert `_save_transcript`'s A=rep mapping.
  - Unit tests run without a bench: `python3 crm/tests/run_unit.py` (stubs
    frappe via `crm/tests/frappe_shim.py`; files are `unit_test_*.py` so
    bench's `test_*` discovery never loads them).
  - **GOTCHA (2026-09-10) — `shape_message` went missing without an import
    error.** `cb8142b2` (per-lead chats) pasted `_from_comment` OVER the
    `def shape_message(...)` line; the orphaned body stayed indented, so
    Python read it as dead code at the tail of `_from_comment` and the module
    imported fine — every Talk read/post/`_publish` then raised `NameError`
    on prod (gw488) while the unit suite stayed green, because nothing called
    the shaper. Restored with its original signature
    `(row, full_names=None, reply_count=0)`; `ShapeTests` in
    `unit_test_talk.py` now pins the function and the frontend's keys.

- **Dev-only Phone Preview** — `/phone-preview` opens an actual CRM lead
  with `?phonePreview=1`: collapsed phone dock, bottom team bar, persistent
  live-one invitations, Listen/Whisper/Barge, history/text threads, incoming
  Answer/Decline, and chat anchored above each teammate status. Three layouts
  via `phoneDesign=A|B|C`; dev `/phone-settings-preview` mocks numbers,
  ownership/access groups, recording defaults, and number-search/review only.
  Phone fixtures stay explicitly distinct from the real lead; no telephony,
  notification APIs, or writes. The bottom bar reserves height; compact
  minimizable popovers leave the real lead's width intact. Route/sidebar/widget
  activation are DEV-gated. Comms mockups (`commsDesign=A|B|C|D`, D = the
  chosen direction: Slack-style left nav, conversations as `/talk/:kind/:id`
  pages) sit on the same activation. **Workspace switch**: the user menu's
  "Try the new workspace" / "Back to classic" toggles classic ↔ next
  (phone A + comms D) in memory; the real mechanism is the per-user default
  `crm_workspace_version` (`utils/workspaceVersion.js`, not written yet).
  **Mattermost-style shortcuts** (`utils/talkShortcuts.js`, bound once in
  `TalkShortcuts.vue`): Talk section inside the existing ⌘K palette (via a
  new `registerPaletteExtension` hook in `composables/modals.js`), ⌘⇧K DM
  switcher (same palette, scoped), ⌥↑/↓ prev/next, ⌥⇧↑/↓ unread, ⌘⇧L
  composer, Esc read+blur, ⌘/ help sheet.
  See `docs/phone-preview.md`; this is a design mockup, not enabled calling.

- **Properties (comps + calcs without a lead)** — sidebar **Properties**
  (`/properties`): type an address → `/properties/PROP-…` is the REAL comps
  page (`CompsView` page mode) for a house that is not a lead. Fills the gap
  where the only way to comp a drive-by / a buyer's ask / a neighbour was to
  create a fake lead that then sat on the Kanban, the Today board, the round
  robin tally and the dashboard cohort. `CRM Property` (ops
  `setup_properties.py`, autoname `PROP-.#####`) carries the SAME column
  names a CRM Lead uses for property / geocode / Zillow / BatchData caches
  and `comps_hidden`/`comps_selected`/`comps_types`/`sqft_override`, and
  `crm.api.comps.subject_doctype(name)` dispatches on the `PROP-` prefix —
  so `get_lead_comps`, picks/hides/types, the sqft override, Zillow/Redfin/
  Realtor facts, the BatchData fallback and the offer calculator run
  unchanged (writers use `doc.doctype`, loaders `_load_subject`; the
  `_*_supported()` gates take the subject doctype). **Adding a comps column
  to CRM Lead means adding it to `setup_properties.py` too.** Saved calcs
  live on the property (`offer_calcs` JSON, newest first, cap 25) since
  there is no timeline; `save_cash_offer` branches there and
  `get_lead_comps` returns the latest as `offer` — the same seed contract
  practice uses — so a reopen lands on the saved numbers. **`/properties`
  is a Board (drag between columns, Refunds-board shape) or a List**
  (`localStorage['propertiesView']`); columns = `crm.api.properties.STAGES`
  (New / Comped / Offer Sent / Follow Up / Dead), stored as free-text
  `status` so a rename is a one-line edit, unknown values render in the
  first column. `set_property_status` (`db.set_value`), also from the list
  row's stage dropdown and a stage pill on the property header. Colours in
  `utils/propertyStages.js`. **Add accepts a Zillow / Redfin / Realtor /
  Auction.com listing URL** as well as an address: `crm/api/listing_url.py`
  (pure, `unit_test_listing_url.py`) reads the address off the slug — no API
  call. Redfin/Realtor delimit street/city/state; Zillow/Auction are one
  hyphen run, split at the last street-suffix word (post-directional + unit
  tokens stay on the street; no suffix → unsplit, still geocodes). Kept as
  `listing_url` (→ **Listing** header button, ↗ on the card); the add
  dialog previews the parsed address (`preview_address`) and refuses a
  non-property page. Header: **Saved calcs (N)** (history dialog of `CashOfferComment` cards, Tweak works),
  **Edit** (an address change clears every location cache; picks/calcs
  stay), delete. Optional **lead** Link (`CRM Property.lead`, ops same
  script, has_column-guarded): a neighbour / second house / buyer's ask
  can hang off a real seller without becoming a lead — blank is the
  default. Picker on add + edit, chip on the property header, seller name
  on the board/list card, **Properties** sidebar card on the lead (desktop
  + mobile) lists them and can add or attach an existing one.
  `list_properties(q, mine, lead)`, `set_property_lead`, `search_leads` /
  `search_properties`. Underwrite and Live one are hidden (`isScratch`). Team-
  visible like practice sets. Guarded on the
  doctype existing. `crm/api/properties.py` + `pages/Properties.vue` /
  `Property.vue`; `utils/comps.js` `isScratchSubject`/`compsPagePath`.
  Verified end-to-end on **staging** (stg21) — geocode, 55 comps, pick +
  Full Gut tag, save calc, reload seeds, note edit keeps geocode; lead
  comps unaffected.

- **Practice comps** — sidebar **Practice** (`/practice`). Any sales user can
  build a set of properties (picked from real leads) with an optional time
  limit — **per listing by default** (e.g. 3 min each), or whole-set (10 houses
  in 30 minutes). Existing sets with no mode stay whole-set. Each acq rep runs the set on the **same
  comps map** as a live lead; hides / picks / the offer calc write to **their
  attempt**, never to `CRM Lead.comps_hidden` / `comps_selected` or the timeline.
  Adding a house stamps a **seller-voice condition line** (`seller_note`, from
  `practice_condition.py` — real call phrasing, no repair label) so the run has
  something to price off; it sits above the calc notes. Empty rows fill on first
  read. Guarded on the column.
  Times and recordings are team-visible on submitted runs (in-progress stays
  private). **Calcs** on the set page is one table per house — one column per
  person (latest submitted run; in-progress omitted), ARV / repair / offer /
  formula as rows. Optional **screen + mic** on Start (`practiceRecorder.js`):
  choose **Browser window** (recommended, keeps Zillow tab switches inside the
  capture) or **This tab**. Property switches are locked until the server clock
  + recorder rollover finish, and recorder transitions share one queue; quick
  clicks can no longer return stale timer state or overwrite shared recorder
  globals. Chunks upload as they arrive so a 30-minute take never hits nginx's
  50m body limit. Each sequence is a separate deterministic `.part.NNNNNN`
  file: the client retains the blob until acknowledgement and retries the SAME
  sequence, so a lost response cannot drop or double-append bytes; an interrupted
  browser leaves contiguous chunks that read/finish can assemble later. Read-time
  shaping NEVER deletes them (it can run while the recorder is still producing
  the tail); only an explicit successful finish cleans sequence files. Playback
  is `stream_recording` (private File URLs 403 in `<video>`). Finalization
  deliberately does **not** insert a Frappe `File` row:
  the assembled take is commonly 40–150 MB and File enforces the site's 25 MB
  upload ceiling, which used to reject the final stamp while leaving valid bytes
  on disk. Canonical, legacy `.part`, and sequenced chunk files remain the
  recovery sources when a JSON recording stamp or finish request is lost.
  Play opens a modal player (`PracticePlayer.vue`) with speed, fullscreen, a
  comments thread, and Loom-style timestamped emoji + comments (Space play,
  ←/→ skip, [ ] speed, C comment, F full). Stored as `Comment` rows on the
  attempt so a reviewer can react without writing the runner's results. Delete-set is still manager-only. Doctypes: ops `setup_practice.py`. App:
  `crm/api/practice.py` + `pages/Practice.vue` / `PracticeSet.vue` /
  `PracticeRun.vue`. CompsView and CompOfferCalc take optional `practiceAttempt`
  / `practiceProperty` to redirect writes. Underwriting is off in practice (that
  creates a real Drive sheet). Guarded on the doctypes existing, so the app
  deploys before the ops script.

- **"Got a live one" → Mattermost** (gw446/gw447) — a zap button on the
  Lead / MobileLead header rows and a **Live one** button on `CompsView`
  (comps page + Today modal; off in practice) opens `LiveOneModal` (optional
  note) and `crm.api.live_one.alert` **posts AS THE REP into their own DM with
  Dennis** (Lance: he has to be able to reply to them). That runs on a
  Mattermost personal access token per rep: site_config
  `mattermost_user_tokens` = `{crm_login_email: token}`, minted on the
  Mattermost box with `cd /opt/mattermost && docker compose exec mattermost
  mmctl --local token generate <username> "CRM live-one alerts" --json`
  (`EnableUserAccessTokens` is on via compose env — config.json still says
  false, the env wins; no per-user role needed; the user gets a "token added"
  email). Minted for Lance/Dennis/German/Exe 2026-09-03. Adding a rep = mint +
  `bench set-config mattermost_user_tokens '<json>' --parse`. **Fallback**
  (no token, or the rep IS the closer): the `pi` bot posts into a group of
  bot + rep + closer, naming the rep and @mentioning the closer; no
  Mattermost account at all → bot→closer DM. Message: **lead name linked to
  the comps page** (no separate link line), address, every phone as a **Quo
  deep link** (`openphone://dial?number=&action=call`, the CRM's own mobile
  Call-button URL; Mattermost renders any scheme but javascript/vbscript/
  data), status, note. Site URL forced `https://` — `get_url()` off a worker
  says http. Also a Comment on the lead timeline naming the path taken.
  Recipient = site_config `live_one_user` (default `dennisszafran`); bot
  token/base shared with the standup. `get_target` names the button ("Send to
  Dennis") via the CRM User's full name, because **Dennis's Mattermost profile
  has no first/last name** and the raw username read as "dennisszafran".
  GOTCHA: a freshly minted PAT answered 401 on its first request and 200
  seconds later — retry before concluding it is bad.

- **Call-history export for refund requests** (gw446) — `crm/api/call_export.py`
  `get_call_history` / `export_call_history?lead=&fmt=csv|txt`. Every call
  linked to the lead plus unlinked logs matching its numbers, oldest first:
  date/time (CT), direction, from/to, status, duration, classifier outcome,
  rep, **recording URL**. The URLs are the Quo share links already on the
  call log (`share.quo.com/…?sig=`, older ones `storage.googleapis.com/
  opstatics/…`) — both verified publicly playable with no login, which is
  what iSpeedToLead needs; the CRM's `get_recording_url` proxy needs a session
  and is useless to them. Refund card on the lead (both states — the export
  is how a rep decides it IS refundable) has **Download CSV** + **Copy list**
  (numbered plain text with a summary line, for pasting into the provider
  form; copies synchronously in the click, Safari drops a post-fetch write);
  every Refunds-board card has a **Calls** download shortcut. No ops piece.

- **Refunds board** — sidebar **Refunds** (`/refunds`) is a kanban of
  `custom_refundable=1` leads (To Request / Requested / Waiting on us /
  Waiting on them / Complete). Marking Dead does not queue a refund; the
  Refundable control does. A **Refund** sidebar card exists on desktop AND
  mobile for every lead, so an active Follow Up can stay live while it is
  tracked here. Missing-form work has TWO explicit states: **Not in provider
  refund form** marks it refundable but leaves it To Request; **Manual support
  ticket submitted** also marks requested + Requested (and implies the first
  flag). The board labels either origin, so “missing” is never mistaken for
  “already submitted”. Inbound refund email threads always have Reply, even when
  Pi produced no draft; sending to ISTL updates Waiting on them. The host mail poller credits
  only the actual transition to Complete, not every duplicate completion email.
  Fields: ops `setup_refundable_field.py`.
  - **Non-refundable leads** (2026-09-09) — iSTL orders paid from the **Sale
    leads balance** (their `bonus` wallet; 154 such orders, zero refunds ever)
    get `custom_non_refundable=1` + `custom_non_refundable_reason`. LeadMarket
    reads each order's `take_from_balances` after every purchase sync and calls
    `crm.api.refunds.mark_non_refundable(orders=[iSTL order ids])`; the join is
    exact because `CRM Lead.vendor_lead_id` **is the iSTL order id**. Per
    ORDER, not "is there a bonus balance right now" — the webhook beats any
    balance check and orders can straddle wallets. Effects: Refund card shows
    the reason instead of *Mark refundable*; Today cards get a gray **No refund**
    chip (`get_today_board` carries `non_refundable`); `set_refund_state` throws on every
    path onto the board (clearing still works, so a request queued before the
    flag landed can be withdrawn — the board shows a red **Non-refundable**
    badge on it); the ISTL call-volume nudge returns `reason: non_refundable`.
    Nothing in the CRM ever clears the flag. Fields via the same ops script.
    The clock is the ISTL webhook (`on_istl_lead_insert` enqueues
    `ask_leadmarket_non_refundable` → LeadMarket `POST /api/istl-purchase`),
    not the purchase-receipt email — Gmail missed 2026-09-10's 08:30 buy and
    the next cron is 04:50.

- **Multiple phones per lead + Quo call backfill** — a lead can hold as many
  numbers as the rep types in. `mobile_no` stays the primary (Call / Text /
  Today / kanban); extras live in `CRM Lead.extra_phones` (JSON list of
  strings; ops `setup_lead_phones.py`). A **Phones** sidebar card (Lead.vue
  **and** MobileLead.vue) lists every number with set-primary / dial / remove
  / add; the header Call button becomes a picker when there's more than one,
  and Send Text offers the same list as To chips. Adding a number (or a new
  inbound lead arriving with one) **relinks any already-mirrored CRM Call Log**
  whose from/to matches and is unlinked, then enqueues a Quo `/v1/calls` sweep
  per workspace line and inserts missing logs **oldest-first**. Dedupes on the
  Quo call id. Attribution matches the webhook (outgoing `userId` = dialer;
  incoming `answeredBy` = answerer; unanswered inbound leaves receiver blank).
  The sequence-events webhook matches calls, texts, the ring silencer and
  sequence auto-stop against the full list, has_column-guarded. App:
  `crm/api/lead_phones.py` + `LeadPhonesCard.vue` + `utils/leadPhones.js`.
  `db.set_value` on the add/remove API so it does not fire lead save hooks;
  `after_insert`/`on_update` cover side-panel / import / webhook writes.

- **Text presets with lead tokens** (gw453, Dennis's ask; Lance picked mockup A
  of three — `docs/mockups/text-presets-mockups.html`) — a **Presets** chip
  row above every lead composer: the header **Text** modal (`SendTextModal`,
  which is also what the Today card's 💬 opens) and the **Text Messages** tab
  (`SMSBox`, also the `/texts` inbox). Tapping a chip fills the message from
  the lead — `{{first_name}}` `{{street}}` ("Maple Ave") `{{address}}` ("412
  Maple Ave") `{{city}}` `{{my_name}}` — and drops the rep in the editor with
  the cursor at the end; nothing sends without the normal Send click. **Two
  lists**: **team** (global Frappe default `crm_text_presets`, everyone reads,
  Sales Manager / System Manager write) and **mine** (user default
  `crm_my_text_presets`, dashed chip with a person icon). Same
  no-doctype/no-ops-script trick as Lead Assignment and the task due chips.
  Pencil → `TextPresetsModal` (both lists, token-insert-at-caret buttons,
  team rows read-only for non-managers). **Rendering is server-side**
  (`crm.api.text_presets.render_text_preset`, one `get_value` per tap) so the
  tokens are defined once and the Today card — which carries only
  `lead_name` — needs no extra fields. **A token the lead can't fill renders
  as a visible `[first name?]` marker, the cursor lands on it, and Send is
  disabled until it is replaced** (`hasUnfilled`) — "Hi , this is German" is
  the text a seller reads as a robot. City is re-cased when the import left
  it all-lower/upper. GOTCHA: a literal `}}` inside a Vue template
  interpolation closes it early and fails the build — the editor's
  `{{token}}` labels are built in script (`display`). `SMSBox` no longer
  collapses to one row on blur while it holds a draft. One seed team preset
  ("Intro") was saved during verification. `crm/api/text_presets.py` +
  `composables/textPresets.js` + `TextPresetChips.vue` +
  `Modals/TextPresetsModal.vue`.

- **Open Research tabs (Lead header)** — a one-click button on the Lead page
  header row (Call · Text · **Research** · ⋯ · Delete) that opens **the comps
  page + one Zillow tab + one Google Maps tab** for the lead's
  `property_address` (was two Zillow tabs; Dennis, 2026-09-10 — Zillow is now
  a reference point to confirm info, comps is where the pricing happens). Same
  button on `MobileLead.vue` (More menu). Reuses the
  same Zillow `/homes/<slug>_rb/` slug builder as More → View on Zillow and the
  same Maps `api=1&query=` URL as the address-row link (extracted into shared
  `zillowUrl`/`mapsUrl` helpers). Toasts if no address is set. Pure frontend.
  `frontend/src/pages/Lead.vue`.

- **Dispo partner badges on the lead** — the same New Western / KeyGlee /
  ezREIdispo chips the Kanban already shows (`DispoBuyerBadges`) now sit under
  the address on `Lead.vue` + `MobileLead.vue` and in the Today lead-modal
  header. Opt-in `fetch` hits `crm.api.dispo_buyers.get_dispo_buyers` (returns
  the compact `summary()` list, not the raw resolve dict) from city/state/
  county so a lead page does not need the kanban pseudo-field. Kanban still
  passes `value` and never creates a per-card resource.

- **Lance-only Team Activity board** — a **Team activity** button at the bottom
  of the app sidebar (rendered only for `lance.johnson@groundworkpro.com`)
  opens a one-day manager report. Laid out as a dense report (Lance's
  preferred style): summary line → **The team** table → **The day** timeline →
  **Today board** + **Worth knowing** panels → legend. Per person: Toggl hours
  tracked, clocked-in window, Quo calls (proportional bar + out↗/in↙), talk
  time, human-sent texts, Today cards done/skipped, tasks completed (split
  on-today's-list / other), active window, and goal %. Goals persist
  cross-device in Lance's Frappe user defaults (no custom doctype).
  `crm/api/activity_progress.py` + `frontend/src/components/ActivityProgressModal.vue`
  + desktop `AppSidebar.vue` + mobile-drawer `MobileSidebar.vue` (**both entry
  points are required** — mobile renders a different sidebar).
  - **Calls are the CRM Call Log, which IS the Quo mirror.** The `call.completed`
    webhook attributes each call to whoever actually handled it, not the line
    owner — but **the field depends on direction**: `userId` is the dialer on
    OUTGOING calls, while on INCOMING calls it is the LINE's owner and is
    identical on every inbound call no matter who picked up; `answeredBy` names
    the answerer. Reading `userId` for both put 47 of 122 inbound calls
    (2026-07-20..08-06) on one person before gw303 fixed it, and an unanswered
    call (no `answeredBy`) now leaves `receiver` **blank** rather than crediting
    the line owner for a call nobody took.
    Do NOT try to read call counts live from the Quo API: there
    is no per-user aggregate endpoint, and counting requires
    conversations → participants → `/v1/calls` per participant — minutes of
    rate-limited work, far too slow for a page load. **Measured 2026-08-05**: a
    full live enumeration of all 5 lines for one day took **48s** and returned
    **109** calls where the mirror had **147** — it found 0 the mirror lacked and
    missed 38, because a conversation whose `updatedAt` falls outside the window
    silently drops its calls. The mirror is both faster and more complete.
  - **Calls are split lead / buyer / outside / internal**, and only the first
    three count as outreach:
    - **`reference_doctype` is NOT a link test** — it has a doctype default of
      "CRM Lead", so it is set on every row whether or not anything matched.
      Only a non-empty **`reference_docname`** means genuinely linked. Reading
      the wrong field makes unlinked calls vanish (it reported 0 outside-CRM
      calls when the true figure was 101 in two weeks).
    - **outside** = external number with no linked record: cold calls, plus
      people who only became a lead/buyer later. The webhook stamps the link
      once, at call time, and never back-fills — 22 of 58 outside numbers in a
      two-week sample exist in the CRM today (mostly a later buyer import), so
      "outside" means *was not in the CRM when called*.
    - **internal** = the other party is one of our own Quo lines. Real calls,
      but not outreach, so they are excluded from the call count and shown as a
      separate "+N internal". This is why totals dropped (Aug 4: 147 → 135 + 12).
    - The workspace line list comes from one cached live Quo `/v1/phone-numbers`
      call (6h TTL) because `User.custom_quo_number` misses shared lines like
      the Backup Number; it falls back to the per-user numbers if Quo is down.
    - The split is what makes cold calling visible: 2026-08-03, Exe ran 21
      outside calls to 21 numbers against 24 buyer calls, while German's 82 were
      all leads.
  - **Toggl** is matched to CRM users **by email** (the workspace exposes the
    same addresses), so no mapping field exists or is needed. Creds come from
    site_config `toggl_username` / `toggl_password` / `toggl_workspace_id`.
    Every Toggl call is best-effort and cached (members 1h, day 120s): an
    outage degrades to "hours unavailable", never a broken board. Toggl stamps
    carry the member's own offset (the setters are on -03:00) and are converted
    with the same helper the Quo webhook uses. A **running** timer has no `stop`
    and reports negative seconds — its band runs to now instead, or today's
    board would read 0.0 h while people are working.
  - **The board flags what it can't vouch for** rather than presenting it as
    fact: a single stretch >10h (or >12h day) is called out as a probable
    forgotten timer (Dennis, 2026-08-04: 14.5h, 7:07am–9:34pm against CRM
    activity of 9:31am–4:40pm); someone active with zero tracked time is
    flagged; unattributed resolved cards are counted out loud.
  - **Cards use `resolved_by`/`resolved_at` when present, falling back to
    `done_by`/`done_at`.** `done_*` is Done-only by design, so **Skipped cards
    before the gw292 ops script have no owner and cannot be attributed** — the
    UI says so instead of showing zero.
  - Automated sequence texts are excluded rather than credited to the owner of
    the Quo line. Quo attribution support lives in the ops repo
    (`Quo Message.sent_by` / `activity_source`).
  - **Tasks "from Today" are inferred**, not stamped: a completed task counts as
    on-list when its lead had a card that day. Measured 2026-08-04: 54 of 55
    completed tasks matched, and the task/card pairs land within a minute of
    each other, so the inference tracks reality and works retroactively. Exact
    stamping would need a schema field plus prop-threading through
    Today → Activities → AllModals, and would only work going forward.

- **Comps map on the lead ("View comps")** — comparable sales around a lead's
  property, ported from the LeadMarket (`../istl-buyer`) comps view with the one
  change that matters: **LeadMarket can only draw an ESTIMATED subject location**
  (iSpeedToLead hides the address until you buy the lead) so it plots the
  centroid of the comp cloud; we own these leads, so this centers on the **real
  geocoded parcel**. Entry points: the **More ▾** menu on `Lead.vue` and a button
  in **Details** on `MobileLead.vue` (mobile has no More menu — without it the
  feature is unreachable on a phone).
  - **Recency is the signal, not decoration.** Pill opacity fades with staleness
    (smoothstep, 0d → 1.0, 360d+ → ~0.32) so the comps that actually price the
    deal are what the eye lands on without reading a date. Amber = still listed
    (an ASK), slate = off-market (a real sale). Fresher pills stack above faded
    ones so the comp that matters stays clickable in a tight cluster.
  - **Where the data comes from, and the join that took real digging.**
    `CRM Lead.vendor_lead_id` holds the iSpeedToLead **ORDER id, not the lead
    id** — the identical trap `istl-buyer/src/purchases.py` documents for the
    receipt emails, repeated in the CRM webhook and undiscovered until gw303.
    The chain is `vendor_lead_id → /orders/all order._id → order.lead._id →
    leads.db lead_id → rent.comping.comparables` (**219 of 236** CRM leads join
    to an order). Do NOT try to join `vendor_lead_id` to `leads.lead_id`: it
    matches **0**.
  - **Exact per-lead comps are thin, so the map is served from a POOLED AREA
    index.** Only ~21 of the 219 bought leads are still in `leads.db` — bought
    leads drop out of the `status=sale` feed, and iSpeedToLead has no way to
    re-fetch one (`/leads/{id}` 404s; `/leads/all` ignores id filters). So every
    comp we hold anywhere is pooled and resolved by radius: **718k comps / 604k
    unique addresses**, filtered to our leads' 515 ZIPs → **47k records / 36.6k
    addresses**, covering **92% of our leads** (median 75 nearby).
  - **Zillow freshness on every map open** (`crm/api/zillow_comps.py`). ISTL
    comps are last *asks* and go stale. After the pool loads we (A) search the
    subject's circle (`/search?coordinates=lon lat,diameter`, diameter = 2×
    radius so 2 mi → `d=4`; street `location=` returns `{zpid}` only) for
    RecentlySold (last 2 years, paged) + every ForSale (`sort=Newest`,
    price-splits past RapidAPI's 800-per-query ceiling, 7-day cache)
    and (B) `/property` the nearest 5 stale ISTL pins (30-day address cache) to pick
    up a sale Zillow recorded after ISTL last saw the house. Street-address
    `/search` returns `{zpid}` only. `dateSold` is
    epoch-ms. New pins are `zillow::{zpid}`; matching ISTL pins are updated in
    place (Street/St suffix collapse + 4-decimal lat/lng). BatchData fires only
    when ISTL has nothing in radius AND Zillow returned no priced solds
    (for-sale listings do not count). Merged in, not a replace. Quota reserve 500 unchanged.
  - **Every RapidAPI call runs in a THREAD POOL, and that is the whole load
    time** (gw352). None of the wait was ever computation — measured on prod, a
    2-mile circle was **30 consecutive HTTP calls and 40.5s**, and a lead needing
    pin refreshes was 27.8s of which **21.3s was twelve `/property` calls in a
    row**. Same work, run together: **2 mi 40.5s → 13.3s**, **½ mi 27.8s →
    5.5s**, **pins 21.3s → 0.6s**, warm **0.21s**. `zillow.fetch_many()` is the
    one entry point.
    - **`FETCH_WORKERS = 4`, measured, do not raise it.** Nineteen pages fetched
      **eight** at a time came back with **thirteen failures — HTTP 429**, each
      silently dropping ~40 comps *and* marking the circle incomplete. Sweep:
      1→8/8 in 9.5s, 2→8/8 5.0s, 3→8/8 3.3s, **4→8/8 2.2s**, 6→8/8 2.2s
      (no gain), 8→7/8 1.6s + a 429. Four is where the curve flattens. The key
      is shared with istl-buyer's ZIP job, so the limit is not ours alone.
    - **GOTCHA — `frappe.local` is a THREAD-LOCAL.** A worker thread has no site,
      no database and no cache, so `frappe.conf` / `frappe.cache()` /
      `frappe.log_error` RAISE there instead of degrading. `_raw_get` is
      deliberately pure urllib; the key, the quota guard, the quota write and the
      error logging all happen on the request thread, before and after the pool.
    - A 429/5xx **retries once** (1.5s), and a circle that still comes back
      partial is now **cached for a day** rather than discarded. Discarding it
      meant `complete=False` → no cache write → the full search was re-charged on
      **every** open forever (13s, every time, for one dropped page).
    - `PIN_REFRESH_CAP` stays **12** even though it is now nearly free in time.
      The cap is about SPEND — each is a billed call — so raising it is a
      deliberate one-line dial, not a side-effect of going faster.
  - **The Today board PREWARMS its comps circles** (gw353,
    `today_board.warm_today_areas`). A cold map is 5–14s and the board is cold far
    more often than you would guess — measured on a real 176-card day, **61 of the
    96** distinct leads had never had their comps opened, and reps open these one
    after another. Measured per open: **prewarmed median 2.14s / 3.4 calls vs cold
    median 4.95s / 7.0 calls**.
    - **The AREA SEARCH ONLY.** Of a cold open's ~8.3 calls, ~3.3 are the circle
      and the rest are per-pin `/property`. The circle is the **slow** half (it
      pages and price-splits SERIALLY — each step needs the previous one's
      `totalPages`), the pins are the **expensive** half and are already fast in
      parallel. So this buys most of the wait for a third of the spend: **~310
      calls/day, ~9k per cycle** against the shared 57k plan.
    - **Warmed off the BOARD, not off lead creation** — the board is the actual
      work list. Leads return to it on cadence weeks after they arrived, by which
      point a creation-time warm has expired (area cache is 7 days), and a lead
      that never reaches the board costs nothing. (Context: 16 leads/day arrive,
      median **5h** to first call, **100% within 7 days**.)
    - **The work list is DERIVED from the board every run, never queued.** Nothing
      to drift, nothing to race, no state to reset, and it self-heals — a lead
      whose warm failed simply still looks cold next run. Affordable because
      `zillow_comps.area_is_cached()` is a **Redis read with no HTTP**, so a sweep
      over an already-warm board is free. `AREA_QUERIES` is the single definition
      of what a circle IS, so the probe and the fetch cannot check different keys
      and report everything warm while prewarming nothing.
    - Bounded **12 leads or 60s per run** (it runs every 5 min and does not need
      to finish in one pass — a 93-lead board is ~6 min of network), and leads are
      warmed **serially**, for the same reason the pool is capped at four.
    - **Enqueued to the `long` queue, deduplicated per day, never inline.**
      `run_today_sync` is on the **short** queue and a rep's board sync rides on
      it; a minute of third-party network there makes real work wait on the least
      urgent thing in the system. Dedup also does the serialising for free, since
      that sync fires from the 5-min scheduler AND from every new lead/task commit.
    - **No ops step** — it hangs off the already-registered `run_today_sync`, so
      there is no new scheduler hook and no `bench sync_jobs`.
    - `today_board.warm_status(for_date)` reports warm/cold/unlocated, so "is it
      working?" has an answer. Verified on prod: **8 → 20 → 49 → 60** across ticks,
      0 failures, 0 429s, no overlapping jobs.
    - **FIXED (gw365) — a deploy no longer empties the warm cache.** It used to:
      `build_image.sh` ends in `bench clear-cache`, and Frappe's `clear_cache()`
      deletes every site key EXCEPT prefixes named in the `persistent_cache_keys`
      hook — which was never declared (observed: 60 warm → 0 after gw353; ~300
      RapidAPI calls rebought over ~40 min). `crm/hooks.py` now declares
      `zillow_area` / `zillow_pin` / `crm:comp-detail` / `zillow_quota_remaining`
      (verified on prod: 650 circles + 6,106 pins survive a real clear-cache).
      CONSEQUENCE: the version constants baked into the keys
      (`AREA_CACHE_VERSION` etc.) are now the ONLY invalidation path — changing
      the shape of a cached row means bumping the matching constant; a deploy no
      longer papers over forgetting.
    - **A version bump no longer re-buys the world** (2026-09-04). Bumping
      `AREA_CACHE_VERSION` v7→v8 re-bought ~300 circles on a key that ran dry a
      week later, for a change that was "blank one field on sold rows". Each
      cache now has a **migration ladder** (`AREA_MIGRATIONS` /
      `PIN_MIGRATIONS` in `zillow_comps.py`, `DETAIL_MIGRATIONS` in `comps.py`):
      `{from_version: fn(data) -> data | None}`. On a current-version miss,
      `_cache_get_migrated` walks down while a step exists, applies the chain,
      and re-writes under the current key **with the old timestamp** (a migrated
      circle expires when the original would have). **Bump AND add a step**;
      omit the step only when the old blob genuinely cannot answer (pin v3
      dropped the raw priceHistory v4 is built on — nothing to carry). Verified
      on prod against a synthetic v7 blob: sold rows lose `days_on_market`,
      active rows keep it, `t` preserved, expired generations not resurrected.
      **v9 (2026-09-22): unpriced rows never board** (Lance) — ND-state
      solds, price-less listings/rentals drop in `_shape_search`, `comps.py`
      re-filters the merged board as a safety net, and `_migrate_area_v8`
      strips them from cached circles without a re-buy. **Auctions are
      exempt** (Lance, same day): a $0-ask auction stays as an orange
      deal-flow pin even though it comps nothing.
    - **Sold circles are served 14 days, for-sale 7** (`AREA_SOLD_HIT_DAYS` /
      `AREA_HIT_DAYS`, carried on `AREA_QUERIES` as a 4th field). The nightly
      prewarm was ~500 calls re-buying week-old solds that had not changed; at
      the moment of the change **301** complete sold circles were 7–14 days old.
  - **RapidAPI spend audit (2026-09-04) — read this before adding a Zillow
    call.** The key ran DRY: 485 of 57,000 left ten days before the Sep 14
    renewal, both apps pinned on their reserves. Neither app keeps a call
    ledger; the numbers were reconstructed from the `X-RateLimit-Remaining`
    header leadmarket logs every 10 min (`/var/log/istl-zillow-api.log*` on
    the app box), minus its own counts. Cycle Aug 14→Sep 4: ~56.5k spent,
    **leadmarket ~14.5k, CRM ~42k**. CRM was 0–300/day until Aug 18, then
    **~2,600 per business day, zero on weekends** — i.e. reps opening comps,
    not the scheduler (prewarm is the ~500/night off-hours band). Biggest
    line by far is **`attach_sale_history`: one `/property` per comp on the
    final board, `MAX_COMPS = 50`**, keyed on the FILTERED set so every
    filter change bills the newly-visible comps — **left at 50 deliberately
    (Lance, 2026-09-04)**; the calls are the feature. Also fixed that day:
    thin galleries re-billing Zillow hourly (below), the version-bump re-buy
    (above), the 7-day sold TTL (above), and leadmarket spending 2 calls per
    10-min run *past* its own reserve (620 in two days — it now persists the
    last reading and re-probes at most every 6h). Verified NOT the problem:
    Redis (no eviction), failed-but-billed calls (a handful), devproppy
    (dormant since July), leadmarket's zolo comps (~350 total). Aug 24's
    8.6k was the pre-gw365 `clear-cache`-on-deploy bug. A per-`(day, path,
    caller)` `INCR` in the request-thread caller of `_raw_get` would make
    this a five-second question next time; it does not exist yet.
  - **Pending / under contract is asked for explicitly** (gw352):
    `isPendingUnderContract=1` on the ForSale search. Default ForSale **hides**
    them — measured Davenport **97 → 156** listings, Indianapolis **281 → 359**.
    - **It must be a NUMBER.** `true` returns `{"errors": ["Is Pending Under
      Contract must be a number."]}` inside an **HTTP 200**, so it fails as an
      empty result rather than as an error.
    - Vocabulary measured over 482 rows: `listingStatus` ∈ FOR_SALE / PENDING /
      RECENTLY_SOLD, `contingentListingType` ∈ null / UNDER_CONTRACT /
      FORECLOSURE. Read the ROW's status, not the query it came from — a
      RecentlySold page returned a PENDING row in the first sample.
    - **`status` still says Active; the new `listing_state` carries
      sold/pending/for_sale/off_market alongside it.** Additive on purpose, so
      every existing filter, colour, count and `isActive` check keeps working. A
      pending home has NOT sold, so it must never be counted as a sale — but its
      price is one two parties AGREED, on a deal happening now, which is the
      strongest read on the board. Violet pills, and the word written out on the
      pill/card/popup/gallery because violet is not far enough from the red in
      LIGHTNESS to carry it alone.
    - **GOTCHA — `daysOnZillow: -1` means "unknown", not zero.** It rendered as
      "Under contract · listed **-1 days**". Stripped server-side, and guarded
      again at the render sites because a week-old cached circle still holds them.
  - Comps carry a street address but no coordinates, so they ship pre-geocoded
    (Census BATCH endpoint, ~95% match — the one-at-a-time endpoint istl-buyer
    uses would take ~50min for this volume). The **subject** is geocoded on
    demand and cached on `CRM Lead.property_lat/lng` (`update_modified=False`, so
    caching a coordinate never looks like a human edit).
  - **GOTCHA — Frappe's `between` filter means DATES.** It routes through
    `get_between_date_filter`, so a numeric lat/lng bounding box comes out as
    malformed SQL. Use explicit `>=`/`<=`. Bounding box first (indexed), then
    haversine to trim the box's corners to a true circle.
  - **Kept fresh by a nightly job, NOT a one-off import.** `CRM Comp` is a
    projection of `leads.db`, so left alone it is a snapshot that silently ages.
    `../istl-buyer/scripts/sync_comps_to_crm.py` (cron **05:20**, after the 04:50
    `src.purchases sync`) re-extracts, geocodes only genuinely new addresses
    (cached in `geocode_cache`) and upserts. It runs ON the app server, because
    `/opt/istl-buyer` and the CRM container share a box — no transport, no second
    copy to drift.
    - **GOTCHA — the laptop's `leads.db` is NOT authoritative.** The first import
      was taken from a local copy that was **six weeks stale** (32,545 leads,
      newest `last_seen` 2026-06-24) against a live 1.57GB server DB: 36,599 comp
      addresses versus **52,191** for the same ZIPs. Always read
      `/opt/istl-buyer/data/leads.db`.
    - It **fails loudly**: a run producing <50% of the rows already in the CRM
      refuses to import and emails Lance (via `src.alert`), as does a failed
      import or an uncaught exception. A silently-failing cron would recreate
      the exact staleness bug it exists to prevent. Cheap to run nightly — the
      geocode cache means a normal run pays for only genuinely new addresses
      (measured: 66 of 52,257).
  - **Per-lead exact comps are NOT achievable, and this is settled — don't
    rebuild it.** The pooled index is the architecture, not a workaround.
    Measured 2026-08-07:
    - Since the scraper started (2026-06-24) **every** bought lead is captured
      in leads.db (July 114/114, Aug 32/32); the historical gap was purely
      pre-scraper (June 14/74). So capture is not the problem.
    - But only ~73% of bought leads carry comps, because **iSpeedToLead's
      autocomping runs late — over days, not hours**: 58% of 1-2 day-old leads
      have comps, 60% at 2-7 days, 79% after a week; live leads sit at 66% while
      retired ones reach 94%. We buy fast (that is the product), so we snapshot
      a lead early and it then leaves the feed, freezing it.
    - And a bought lead is **permanently unreachable**: `/leads/{id}` 404s,
      `/leads/all` ignores id filters, and every filter variant tried
      (no filters, `status=sold`, `unholded=false`, `my_lead`, `bought`)
      returns the same newest page containing none of our purchases. The
      **orders API carries no comps either** — `autocomping` and
      `autocomping_price` are None on all 200 orders, `arv`/`mao` are 0, and
      the embedded `order.lead` has no `rent` key at all.
    - Conclusion: a large share of bought leads will never have their own comps.
      That is the vendor's pipeline. The pooled area index covers ~92% of leads
      and is what makes the feature work.
  - `crm/api/comps.py` (**new**: `get_lead_comps` / `import_comps_file` /
    `address_key`) + `frontend/src/components/Modals/CompsMapModal.vue`
    (**new**, Leaflet — already a dependency, no new package) + both Lead pages.
    Everything is guarded on the doctype existing, so the app is safe to deploy
    before the data. Ops: `scripts/setup_comps.py` (CRM Comp doctype, autoname
    `format:{address_key}` so re-import updates in place; + the lead lat/lng
    cache fields).

### BatchData fallback for leads with ZERO comps (`crm/api/batchdata_comps.py`)

  - **The gap it fills, measured:** sampling the 45 most recent leads,
    **8 (18%) returned zero comps** — Albany/Brooklyn/Rochester NY, High Point
    and Glade Valley NC, Avondale AZ, Newnan GA, Warsaw MO. Those reps open the
    map and get nothing, the one outcome the ladder in `_preset_tiers` exists to
    avoid. When the pooled index holds *nothing at all*, no amount of loosening
    helps.
  - **Fires when Zillow RecentlySold has no priced sales** — Louisiana and the
    other ND states. ISTL last-asks used to suppress it (`istl_has_comps`); those
    are last LISTs, so a NOLA map could show 944 pins and **0 Sold**. Only a
    priced `zillow::` sold skips the spend. Pin-refreshed ISTL rows keep their
    ISTL name and do not count. Zillow for-sale listings still do not count.
  - **Trigger is still `not out` for the empty-map case** — a tight preset
    matching nothing must never spend money; that is the ladder's job.
  - **Cost: $0.03/row, `take=5` → $0.15 per lead**, verified by wallet-balance
    deltas. Cached, so each lead is paid once. Uses the dedicated
    `batchdata_comps_api_key` (Basic Property Data + Comparable Properties only).
    **Do NOT point it at the general BatchData key** — that carries all 13
    datasets and bills **$0.64/row**, 21x more, for fields this never reads.
  - **A no-match is free.** Avondale AZ returned an empty pool and cost **$0.00**
    — you are billed per row returned, so a miss costs nothing but the round trip.
  - **Sale window is applied SERVER-SIDE** (`sale.lastSaleDate.minDate/maxDate`,
    2 years) so we only pay for rows already inside it — 24% cheaper than pulling
    25 and discarding stale ones. `minDate`/`maxDate` is the ONLY accepted shape:
    `min/max`, `start/end`, `from/to`, `gte/lte` and ISO datetimes all return
    `"Invalid Date"`, and **an unrecognised key is silently ignored**, which would
    mean paying for stale rows and never being told.
  - **Negative results are cached** (14d, vs 90d for a hit) — same lesson as the
    Zillow cache: an address the provider cannot match is otherwise re-billed on
    every modal open. Written with `update_modified=False`, so a cached lookup
    never looks like a human edit.
  - **Rows are shaped to the existing comp contract** (same keys, `status`
    `"Inactive"`, `removed_date` = sale date) so the map, table and pill grammar
    work untouched. They carry `source: "batchdata"`, and the response carries a
    `fallback` block — these are *recorded sales*, not our pooled listing index,
    and the rep has to be told that rather than left to assume.
  - **Verified live against the real empty leads** (4 of 5 filled, 10 usable comps
    each, $1.20 total): Albany pool=38, Brooklyn 234, High Point 45, Newnan 64,
    Avondale 0. Harness: `tmp/verify-fallback.py`.
  - **Ops before this does anything:** set `batchdata_comps_api_key` in site
    config, and add `batchdata_comps` (Long Text) + `batchdata_comps_fetched_at`
    (Datetime) to CRM Lead. Absent either, it degrades quietly — no key means the
    fallback reports `not_configured`; no fields means it works but re-bills.
  - **GOTCHA — Frappe declares Int/Float/Currency columns NOT NULL.** Plenty of
    comps have no year built or square footage, so the importer coerces missing
    numerics to 0 and text to "" (dates stay nullable — a live listing genuinely
    has no removal date). 0 is falsy, so the popup omits the fact rather than
    printing "0".
  - **GOTCHA — frappe-ui renders `type="select"` as a button-driven combobox**,
    not a native `<select>`, so `@change` on it is not reliable; watch the
    v-model instead. The modal also loads `onMounted` when `show` is already
    true, or a v-if host / hot reload leaves an empty map claiming "no comps"
    (the same trap `ImportBuyersModal` hit).
  - **Filters, PRE-SET around the property.** An unfiltered 2-mile dump is not a
    comp set (234 pins of condos and mansions around a 900sqft bungalow), so the
    map opens with status / recency / beds / baths / sqft / year / price / type
    already set from the subject, in the CRM's own Filter idiom (button + count
    badge + popover + clear). Filtering happens **server-side BEFORE the 200-pin
    cap** — capping first would take the 200 nearest and then filter those,
    silently hiding better-fitting comps further out.
    - **The preset is a LADDER, not a filter** (`_preset_tiers`): `Recent ·
      similar` (180d, beds ±1, sqft ±25%, year ±20, same type) → `Last year ·
      similar` → `Last 2 years · loosely similar` → `Everything nearby`. The
      tightest tier yielding ≥`MIN_USABLE_COMPS` (5) wins, and the response says
      which tier ran (`preset`/`relaxed`/`fell_through`) so the UI states it out
      loud instead of showing a map full of houses nothing like the subject.
      Measured on prod: 7 `similar`, 1 `wider`, 3 `all` of 11 leads — and all
      three `all` cases had ZERO comps in radius, so nothing was being hidden.
    - **Touching any control switches to explicit mode** (`auto=0` + `filters`):
      from then on the server runs exactly what is on screen even if it matches
      nothing. Quietly widening a deliberate filter is how a tool stops being
      trusted. `auto` defaults **off** server-side so the endpoint stays
      byte-compatible for any caller predating this (verified: 0 mismatches over
      11 leads), which is what let the backend deploy independently.
    - **Lead beds/baths/sqft/year are pick-list TEXT, not numbers** — the whole
      measured vocabulary is `"3 Bedroom"`, `"1.5 Bathroom"`, `"More than 5"`,
      `"1000 - 2000"`, `"5000+"`, `"1900-1950"`, `"None"`. `_parse_band` returns
      the **interval the source named**, never a midpoint: collapsing
      `"1000 - 2000"` to 1500 and then widening ±25% invents precision the data
      does not have. So a vague seller answer yields a loose filter and a
      listing-sourced number a tight one — confidence follows the source.
    - **A missing numeric is 0, not zero bedrooms**, so an unknown value PASSES
      every range filter. Excluding it would drop real sales for missing
      metadata.
  - **The subject pin shows the property's own facts** (beds/baths/sqft/year,
    type, condition, **what it last SOLD for**, last ask, assessed/tax/Zestimate).
    Sources are merged best-first and **labelled**: **Zillow** > the property's
    own row in the comp inventory > the lead's pick-list fields > the tax pull.
    - **`crm/api/zillow.py` (new) is the good source.** RapidAPI
      `us-property-market1` `/property?address=` resolves our ordinary address
      strings (3/3 test leads, incl. a manufactured home) and returns REAL
      beds/baths/livingArea/yearBuilt/homeType/coords — replacing pick-list bands
      with numbers, which is what makes the preset filters tight. Measured: the
      Aurora lead went 16 matched → **6** once the sqft came from Zillow (1155)
      instead of a band. Bruno collection + `QUIRKS.md` live in
      `~/Projects/bruno-collections/Zillow RapidAPI`.
    - **A real sale, at last.** `priceHistory` carries `event: "Sold"` rows
      (e.g. Aurora `$97,000` 2010-12-06, `source: "Public Record"`), which IS a
      verified transaction — shown as **Last sold**, separate from and above the
      comp inventory's last ask, with the row's `source` printed because
      `Agent Provided` is weaker evidence than `Public Record`. Not every home
      has one (Orlando has none), hence "if any".
    - **Each fact falls through INDEPENDENTLY**, because Zillow nulls individual
      facts (Aurora: `bathrooms 1.5` but `bedrooms None`, so beds came from the
      listing record). Picking one source for the whole set throws away good data.
    - **Facts often contradict the seller.** Orlando lead 00016: the form said
      "1000 - 2000 sqft / 1970-1980"; Zillow says **924 sqft, built 1993,
      Manufactured**. That is underwriting-relevant, which is the point of
      labelling every fact with its source.
    - **Cached on the lead** (`zillow_facts` JSON / `zillow_fetched_at` /
      `zillow_zpid`, 30-day TTL, `update_modified=False` so it never looks like a
      human edit — verified: `modified` stayed 08-04 while `fetched_at` was 08-07).
      **Negative results are cached too**, or an address Zillow cannot resolve is
      re-billed on every modal open.
    - **OUR spend is a few hundred a month** (1 lookup per lead, cached 30 days,
      ~764 leads). The plan's 57,000/cycle ceiling is NOT our budget — **the key
      is SHARED**: `istl-buyer/src/zillow_api.py` runs a background ZIP-market job
      on the byte-identical key (Infisical exposes it twice, as
      `RAPIDAPI_ZILLOW_API_KEY` and `ZILLOW_RAPIDAPI_KEY`) and is the heavy
      consumer — 8,414 of 57,000 spent on day one at ~350/day, against ~10 from
      the CRM. The cycle renews on the **14th**, anniversary-billed, not the 1st.
      That job stops at 5,000 remaining "to leave headroom for the other
      Zillow-backed app" — which is now us — so the CRM may use that band but
      keeps its own `QUOTA_RESERVE = 500` floor (read live off the
      `X-RateLimit-Requests-Remaining` header, cached 15 min, so it sees the ZIP
      job's spending too). Below the floor it degrades to the older sources.
    - Needs site_config **`rapidapi_zillow_key`** (Infisical
      `RAPIDAPI_ZILLOW_API_KEY`) + ops `scripts/setup_zillow_facts.py`. Absent
      either, everything is has_column-guarded and degrades to the older sources.
    - **GOTCHA — `frappe.cache().set_value(..., expires_in_sec=N)` is not readable
      by `get_value()` in the same request once a MISS has been read first.**
      `get_value` memoizes a miss as `None` into the per-request
      `frappe.local.cache`, but the `expires_in_sec` path of `set_value` writes
      **only to Redis** — so the poisoned local `None` shadows a value that is
      demonstrably in Redis (`b'\x80\x04K*.'`) and the read silently returns None
      forever in that process. `get_value(..., expires=True)` does **not** fix it
      (it checks the local dict first regardless). This made the quota guard
      never fire, twice, while looking correct. Fix used here: store **without**
      a TTL (that path DOES populate the local cache, overwriting the poisoned
      entry) and keep a timestamp in the value to judge freshness yourself. Read
      order matters: set-then-get works, get-then-set does not.
    - **GOTCHA — top-level `dateSold`/`lastSoldPrice` are null even on homes that
      have sold.** Read `priceHistory`. And **`price` mirrors `taxAssessedValue`
      on an off-market home** (Macon: both 6005), so it is NOT a list price and is
      deliberately never rendered as one.
    - **~5% of leads have their own address in `CRM Comp`** (13 of a 250 sample),
      which is the fallback "last ask" — and it means the subject **was comping
      against itself**: its own row rendered as a pill at distance 0 under the
      subject dot, inflating the count. Now excluded by `self_comp_key`.
  - **Filters are VISIBLE, not a popover** (gw315) — they are the point of the
    tool, so a rep should not have to find a button before widening a beds range.
    One wrapping bar above the map; it wraps to more rows at 390px.
  - **Recency gates OFF-MARKET comps only** (gw315), so the control is labelled
    **"Sold within"** and defaults to **12 months** (`DEFAULT_WITHIN_DAYS`). A
    sale from 3 years ago is a different market, but a house that has sat listed
    for 18 months is live evidence about what is being asked TODAY — dropping it
    as "old" would hide exactly the stale listings that say an area is not moving.
    Measured on prod at `within=90`: the board keeps **124 active listings, 62 of
    them listed >90 days**. The ladder spends SIMILARITY before recency (loosens
    shape twice at 12 months before reaching back to 2 years) because a
    poorly-matching recent sale beats a well-matching stale one.
  - **Pills carry beds/baths/sqft/year** — price bold, facts beneath. Chosen from
    three mockups rendered against a real 418-comp board: the one-line variant
    measured **186px** wide vs **113px** two-line, the difference between readable
    and a wall of overlap. **`D` toggles the facts off** (54px price-only pill),
    persisted per user in `localStorage['compsPillDetail']`.
  - **Parcels are their own toggle** (`P` / a checkbox next to Details), not a
    side-effect of Nearby. Nearby is every home around the subject; lot lines are
    where each lot ends — a rep zoomed in to judge a comp should not also have to
    turn on 1,800 context dots. Off by default, fetched only when flipped on,
    and only above zoom 16 (below that a city lot is a smudge). Persisted in
    `localStorage['compsShowParcels']`. Uses `crm.api.geo.get_parcels` (bbox, the
    same endpoint the desk already had). A fetch that loses a race with toggle-off
    or a map rebuild does not redraw.
  - **Street View toggle** (`S` / a checkbox next to Parcels) overlays the Maps
    Embed panorama on the map. Same key as the lead-desk mockup (`streetView.js`,
    project `claude-code-486305`, referrer-restricted). `location=` is lat,lng
    only; iframe uses `referrerpolicy="origin"`. Subject by default, last clicked
    pin otherwise. Off by default (`localStorage['compsShowStreet']`). A cold
    rural load says "Loading…", never "could not load", for 30s. Per-comp: the
    compass on a tray card, Street View in the photo modal, or the subject-pin
    popup all call `openStreetView` (closes the gallery so the overlay is visible).
    - **Year rides on the TOP line beside the price** — a width decision, not a
      cosmetic one. A pill is as wide as its widest line and the facts line was
      it; moving the four year digits up beside the short price shortens the line
      that set the width and lengthens the one that did not. Measured over 400
      real comps: avg **120 → 82px (32% narrower)**, 31% less total pill area;
      live DOM confirms **113x33 → 76x33**. Full facts stay in the `title`.
  - **Comps is a PAGE, not a modal** (`/leads/:leadId/comps`, opened in a new tab
    from both `Lead.vue` and `MobileLead.vue`). `components/CompsView.vue` holds
    the whole thing; `pages/Comps.vue` is a thin wrapper. When it stopped being a
    modal, `show` became a plain always-true ref so every existing guard, watcher
    and shortcut gate kept working untouched.
  - **Zillow layout: filters on top, map LEFT, photo tray RIGHT** (gw345, Lance's
    ask). The property table is GONE — a table row cannot carry a photo, and a
    photo is the fastest way to know a comp is not comparable, since square
    footage says nothing about a gutted shell beside a renovated flip.
    `CompTrayCard.vue` + `CompSubjectCard.vue` (**new**) are the tray; the subject
    rides at the top of it in the same card grammar as the comps, so "bigger or
    smaller than mine" is a glance rather than a memory test.
    - **Photos cost NOTHING, and that is the whole reason this works.** Zillow's
      `/search` — already being called by the area refresh — returns `imgSrc` on
      every row (measured: 41 of 41 on a St Paul page); `_shape_search` was simply
      throwing it away. Wiring it through gives **62% / 46% / 96% / 100%** photo
      coverage on four real prod leads, for **zero** extra API calls. The
      alternative, `/property?address=` per comp, is one BILLED call each — 200
      comps would be 200 calls per open, on a key the ISTL ZIP job already leans on.
    - **Realtor fills a thin Zillow gallery** (`crm/api/apivex.py`). Off-market
      solds often come back with one leftover Zillow frame. Opening the gallery
      then asks Apivex `/realtor/property/photos` by address and hotlinks
      `ap.rdcpix.com`. Tray stays on Zillow `imgSrc`. Key: site_config
      `apivex_api_key` (Apivex Plus). Cache v3 retries ≤1 photo rather than
      freezing a thin gallery for 30 days — **but only the Realtor/Redfin rungs
      retry** (2026-09-04). The two BILLED Zillow calls behind a gallery live in
      their own sub-cache (`crm:comp-detail:zillow:v1:{name}|{zpid}`, 30 days
      the moment Zillow ANSWERS, retry-window only on a failed call). Before
      that, every re-click on a one-photo off-market sold — most of them —
      re-bought `/property` + `/photos` every hour, forever.
    - **The Redfin LINK no longer holds the gallery open hostage** (2026-09-10,
      Lance: "slow loading pictures"). Measured on prod: Zillow facts + photos
      **0.39s**, then redfin-scraper-api `/url` **6.3s** (its KNN ordering)
      run SERIALLY after them — ~7s per cold gallery, for a link. Now
      `_start_redfin_url` starts it on a thread BEFORE Zillow and
      `_finish_redfin_url` waits `REDFIN_URL_BUDGET` (1s) past it; a late
      answer caches as `redfin_url_pending` and `fill_detail_redfin_url`
      (short queue) patches the entry, keeping its remaining TTL (raw redis
      `ttl` needs `cache.make_key`). Cold gallery **7.2s → 1.4s**. The
      thread calls the pure `redfin._fetch_listing_url(base, …)` — config is
      read on the request thread, the `frappe.local` GOTCHA again (first cut
      called `redfin_listing_url` in the thread, which reads `frappe.conf`,
      raised, and silently returned no link every time). Tests:
      `unit_test_comp_redfin_url.py`.
    - `_merge_one` also stamps the photo/zpid onto a matched ISTL pin, because a
      photo is not "newer" data — it is data the pooled index never had — so it
      rides along on ANY match instead of waiting for a price to change. Comps with
      no Zillow match render a labelled placeholder rather than a broken frame.
    - **The SUBJECT's photo is free twice over**: `_normalize` now keeps
      `cover_photo`, and failing that `apply()` grabs the thumbnail off the
      subject's own row in the area search before discarding it (a house is not a
      comp for itself, but its picture is still ours). `REQUIRED_FACT_KEYS` in
      `zillow.py` treats a cached fact blob missing a listed key as stale, which
      spends ONE lookup per lead and then rides the normal 30-day cache — a
      remembered negative (`{}`) is deliberately exempt, or every unresolvable
      address would be re-billed on every open.
  - **"Discard" grays a comp out in place and offers Undo**, replacing a hide that
    made the card vanish behind an `N hidden` button. Same backend
    (`set_comp_state` / `comps_hidden`), same team-wide semantics, and discards
    still leave the pool entirely — but **`include_hidden` no longer merges them
    back in before the tier ladder**, which is what let discarded junk keep a tier
    "usable" and suppress the widening the rep needed. They now travel in their own
    `discarded` list, capped and distance-sorted, so the tray can show them without
    touching the ladder, the counts, or what gets underwritten. The drawer opens
    itself on the first discard — otherwise the card just disappears, which is the
    behaviour this replaced.
  - **CompsView's root is `overflow-y-auto` in the compact layout too**
    (2026-09-10, Lance on a phone: "lien table won't go down"). Compact was
    `overflow-hidden` so the map filled the screen, but the auction calc opens
    ~600px tall and the Tax / liens card with Records unfolded taller still,
    and everything past the first screen — the rest of the card, the map — was
    unreachable. The map keeps its `min-h-[16rem]` floor, so the folded-calc
    case paints exactly as before; an overflowing header stack scrolls.
  - **PostHog surveys are OFF in the CRM** (`telemetry.js` `disable_surveys`).
    The project is SHARED with Relay, whose "Report a problem" survey is a
    widget there — so PostHog drew Relay's black right-edge tab on every CRM
    page. Not in this repo's source, not in Frappe, not in nginx; it only
    showed up by grepping the Relay bundle for the survey id.
  - **The split is measured on the COMPONENT's width, not the viewport's**
    (`SPLIT_MIN_WIDTH`, ResizeObserver on the root). The three hosts get wildly
    different widths at the same viewport — comps page ~800px, the Today modal's
    right pane ~620px, a phone ~260px — so a `lg:` breakpoint put a 330px rail
    beside a **266px** map inside the Today modal and called it a split. Under the
    threshold the two stack, the map spans full width, and the filter bar collapses
    behind the existing Filters toggle (stacked, it was eight rows of controls
    standing between the rep and the map on a 390px phone).
  - **GOTCHA — Vue's class patching DESTROYS a Leaflet map.** Vue writes the whole
    `class` attribute from its static + bound parts, silently discarding the
    classes Leaflet adds imperatively (`leaflet-container`, `leaflet-touch`, …).
    This was harmless while the map div's `:class` only keyed off a prop that never
    moved at runtime; the moment it keyed off a resize-driven ref, crossing the
    breakpoint wiped the map. Sizing classes now live on a WRAPPER and `mapEl`
    keeps a static `size-full`. Any runtime-varying `:class` on a
    third-party-decorated element is the same bug.
  - **GOTCHA — CompsView has TWO root nodes** (the layout div and
    `CompDetailModal`), so it is a fragment and Vue does **not** inherit a `class`
    from its host. `pages/Comps.vue` passing `class="min-h-0 flex-1"` was silently
    dropped and the split grew to ~8,000px tall. Height has to be decided inside
    the component, from its own props.
  - **Each comp card carries +/- deltas against the subject** (`+1 bd`, `−309
    sqft`, `−7 yr`) — the actual question being asked of a comp. Without them the
    rep reads "1,744 sqft", has to remember the subject was 1,749, and subtract,
    for every card. Only NON-ZERO differences render: a comp that matches on beds
    says nothing by saying "+0 bd". Computed from the subject's **exact** numbers
    only (`beds_exact` etc.) — the lead's pick-list bands would turn a midpoint of
    "1000 - 2000" into a hard "+244 sqft", inventing precision the source never
    had, so a band simply yields no chip.
  - **The nav sidebar collapses itself on this page** (`sidebarCollapsedOverride`
    in `composables/settings.js`; `AppSidebar`/`CommandPalette`/`GlobalModals` now
    read the `sidebarCollapsed` computed). It is an **override, not a write to the
    stored preference** — comps opens in its OWN TAB, so writing
    `isSidebarCollapsed` on mount would leave every other CRM tab collapsed after
    this one is closed. Writing the computed (any deliberate human toggle) drops
    the override and updates the real preference, so Expand still works here and
    wins from then on. Verified: pref `false` → comps 60px, `/leads` 220px, pref
    untouched; Expand on comps → 220px and stays.
    - **GOTCHA — `:isSidebarCollapsed` is a frappe-ui PROP name**, on
      `SignupBanner` / `TrialBanner` / `GettingStartedBanner`. A blind rename of
      the identifier in `AppSidebar.vue` renames the prop too and silently breaks
      those three banners.
  - **The top is deliberately dense**, because every pixel it holds is a pixel the
    map does not get: address and counts share ONE line with the controls (they
    were stacked), the filter strip uses inline labels and tighter gaps, and
    Reset/Clear lost the `ml-auto` that was forcing them onto a third row alone.
    The underwrite button says **"Underwrite"**, not "Select comps to underwrite"
    — 430px of a shared line to say what the disabled state and tooltip already
    say. Measured on the comps page: header block **243px → 142px**, filter strip
    3 rows → 2, map **514px → 621px**.
  - **The map claims a FLOOR, and the calculator folds** (gw352). `flex-1` alone
    means "whatever is left", and what was left measured **342px** on a 919px
    window because `CompOfferCalc` above it took **358px** — the tool was smaller
    than the form sitting on it. Now `min-h-[32rem]` on the split, and the calc
    collapses to a one-line "Cash offer · N picked" button (persisted in
    `localStorage['compsCalcOpen']`, shortcut **C**, **open by default** — hiding
    a thing he just built is not our call to make silently). Map **284 → 510px**
    on the page (598px with the calc folded) and **384 → 544px** in the Today
    modal. The collapsed state names its pick count so folding it never hides
    whether the calculator still holds anything.
  - **GOTCHA — a min-height under `overflow-hidden` SILENTLY EATS the overflow.**
    `pages/Comps.vue` deliberately did not scroll (so the tray has a bounded
    height to scroll inside), which was fine while the map took only leftovers.
    Give it a floor and the content hit **1,030px in an 856px host**: the bottom
    **174px — including the whole legend — became unreachable**, with no
    scrollbar to say so. It is `overflow-y-auto` now; the tray keeps its own
    bounded height either way, so it still scrolls internally. Same trade the lead
    desk already documents: a scrollbar beats clipping.
  - **A hovered pill goes above EVERYTHING and stays up** (gw352). The old
    `el.style.zIndex = 900` failed twice over: the subject carries
    `zIndexOffset: 1000` and Leaflet computes each marker's z as **latitude +
    offset**, rewriting the inline style on every pan — so 900 was both too low to
    clear the subject and erased by the next map move. It goes through
    `setZIndexOffset` now (the only thing Leaflet respects), and a pill you have
    looked at **stays** above its neighbours afterwards rather than diving back
    under — the reason you hovered it was to get it out from under them, and
    dropping it back makes a dense cluster feel like whack-a-mole. Measured:
    **401 at rest → 10262 hovered → 5262 after**, against a subject at 1273. The
    bands are far apart because the pixel-y term can reach ~1,000. `placePin`
    restyles through `restZ`, or a use/discard/filter change would undo it.
  - **Pins distinguish sales from removals** — for sale RED (`#d92d20`), confirmed
    sold YELLOW (`#f5c518`), listing removed/off-market SLATE (`#cbd5e1`, dark ink),
    subject BLUE (unchanged). Separate legend entries; removal is not a confirmed
    transaction. One palette in `utils/comps.js`
    (`COMP_COLORS`) feeds the pills, the tray chips and the legend, because those
    three surfaces cannot read each other's styles. Yellow pills print **near-black
    text**, not white — white on `#f5c518` is unreadable. The popup headline uses
    a darker `onLight` gold, because the yellow fill itself vanishes on white.
    Replaced a blue/amber pair chosen to be dichromat-safe; red vs yellow is a
    weaker hue signal, so they are also far apart in LIGHTNESS, and status is
    written in words on every card and popup so colour is never the only carrier.
    - **GOTCHA — fade the FILL, never the text.** The old `opacity` on the whole
      pill washed white-on-red into the basemap at 2.5:1 and made faded yellow
      vanish at 1.9:1. `withAlpha` now fades only the fill (and only off-market
      pins — a live listing is current by definition), so the recency fade can
      stay deep (0.32 floor) while every pill clears 4.5:1. Measured: 0 of 21
      below the bar after, 6 of 21 before. The legend now says "Fainter = older
      sale".
  - **GOTCHA — `1rem` is 20px in this app**, not 16px, so a `w-[21rem]` rail
    renders at **420px** and out-sizes the map it is meant to accompany. The tray
    is sized in px deliberately.
  - **GOTCHA — ResizeObserver and rAF do not fire in a BACKGROUND tab.** A
    hand-attached probe RO recorded zero callbacks, including the initial one, and
    a container-width layout therefore looked completely broken while being
    correct. Activate the tab before concluding anything about resize behaviour —
    the same trap the kanban notes for paint timing.
  - **The subject is a PILL too**, in the same two-line grammar as the comps
    (`Subject 1910` / `2/1 · 1118sf`), blue with a heavier white ring so it never
    reads as one of them. It was an 18px dot, which marked the spot but said
    nothing — you had to click it to learn what you were comparing against.
    Putting its numbers in the same shape as the comps' makes "is this one bigger
    or smaller than mine" a glance instead of a memory test. It honours the `D`
    toggle (collapsing to a bare "Subject") and keeps the centre anchor, so the
    pill's middle still marks the real parcel — verified: pill centre == marker
    centre == the geocoded point.
  - **A property list sits under the map**, one row per comp, and hovering either
    a row or a pin highlights the other. A map answers "where", a list answers
    "which". Every pill also carries a hover-only ✕ that removes it, wired to the
    same handler as the popup's Hide.
  - **The cash-offer calculator picks its repairs by NAME and its formula by
    toggle** (`CompOfferCalc.vue`, gw360/gw361/gw362). Repairs is a four-rung
    ladder — Paint & carpet $10 / Kitchen & baths $30 / Full rehab $50 / Down to
    studs $75 — each row carrying what that level costs on THIS house ($13k /
    $38k / $63k / $95k at 1,260 sf), because a rep on the phone hears "kitchen &
    baths", not "$30/sf". "Other…" keeps the raw $/sf one click away.
    - **The tier is DERIVED from the $/sf, never stored** — nothing to drift, an
      old calc names itself, and typing 30 into "Other…" IS Kitchen & baths.
      **`mult` IS stored**, because it genuinely cannot be derived: a 70% column
      does not imply a single deduction once the percentage is editable.
    - **Cash, novation, list-it, or rental**, cash by default. Independent notebooks — switching
      does not rewrite the other kind's numbers or its comps, and a save writes
      only the kind on screen. Novation, list-it and rental start empty and only take pins
      picked while that kind is on; cash still follows the sale map until edited.
      A **For sale / Rentals** map toggle flips the inventory; cash/novation/list-it
      default the map to for-sale/sold, rental defaults it to Zillow `ForRent`
      (`inventory=rentals`, names `zillow-rent::{zpid}`, never prewarmed, never
      mixed into the sale circle or BatchData). Rental calc is **MAO = MIR × 80%
      − repairs − fee**. Teal pills, `$1,450` not `$1k`. The
      novation 10% has a hover `?` (6% Realtors / 2% closing / 2% minor repairs). Novation is **Current value − 10% − fee ($40k
      default)**; no repairs, because we list the house as-is. **List it** is
      **as-is − 6% commission − 2% closing − 2% concessions** — no fee, no repairs;
      the number is the seller's takeaway if they listed themselves. The three
      rates stay editable; takeaway back-solves as-is. Current value / as-is
      soft-fills from that kind's comps-table average, same suggested-ARV cash
      uses. Every figure is editable (After % / Offer back-solve value / fee).
      Old saved calcs have no `kind` and stay cash. Server recomputes; the
      timeline card titles itself "Novation offer" / "List it" so a $40k fee
      without a rehab line, or a 10% net sheet with no fee, does not look like a
      missing row.
    - Two cash formulas: **`2× repairs` (ARV × 90% − 2×repairs − fee, the default)** and
      **`Classic` (× 70% − repairs − fee)**. The first is not new — it is what
      `OfferRail.vue` already runs on the lead desk, so this borrows the rail's
      wording (`Repairs × 2`) rather than inventing a second name for the same
      arithmetic. Picking one sets its canonical %, which stays editable: the
      toggle owns the SHAPE, the rep owns the number.
    - **The Rehab row is the DEDUCTION, not the repair bill.** At 2× they differ,
      and the number that reaches the offer has to be the visible one or the
      column stops adding up. Typing a total back-solves $/sf THROUGH the
      multiplier ($75,600 at 2× is $30/sf, not $60).
    - **Anything saved before the toggle has no `mult` and reads as Classic** —
      what those numbers meant when written. The rule is applied identically in
      three places (seed, localStorage draft, server) so an old calc cannot
      render one way on the timeline and another when reopened. The server
      RECOMPUTES the multiplier rather than trusting the client, and the timeline
      card names the formula, or a doubled deduction reads as a mistake later.
    - **One column by default; "+ Compare" inherits whatever the rep is on.**
      Seeding the second column with the OTHER formula was built and removed — it
      decided for them what was being compared, when the commoner comparison is
      one formula at two percentages. With both priced, the higher offer wears a
      `+$800` badge (numbers stay right-aligned; the badge grows leftward). With
      one column the other formula reports itself in a line under the Offer, at
      ITS own percentage, so "which pays more" never costs a toggle-and-remember.
    - **GOTCHA — the picker's menu is anchored to its RIGHT edge, not the grid
      cell.** Alignment between the trigger's value column and the menu's is a
      right-edge relationship (trigger reserves 6px + a 10px caret inside 7px of
      padding; menu rows carry 20px of right padding inside the 3px popover pad).
      In the Today modal at 390px the cell is 160px, which wrapped every row in
      half. Container queries drop the total, then the rate, off the trigger —
      the NAME is what has to survive.

  - **Underwrite straight from the comps you picked** —
    `underwriting.create_underwriting_from_comps(lead, comps)`. The template needs
    NO change: its comp block is rows **14–20, column A = a Zillow link**, and the
    sheet computes address / sale date / distance / sqft / price / $/sqft itself,
    averages G14:G20 at row 21 and turns that into the ARV at row 22 that drives
    the offer. Verified end-to-end on prod: 4 links in → `Average $/SF $101`,
    `Subj Price Based on Average $113,419`.
    - **It ALWAYS creates a NEW sheet** (Lance's call), unlike
      `create_underwriting_workbook`, which is one-per-lead and re-opens the
      existing one. A colleague may already have comps in theirs and silently
      overwriting their work is worth a few cents of Drive storage to avoid.
      Second and later sheets are named `<address> (N)`.
    - **GOTCHA — the sheet's `z*` functions only accept a `/homedetails/…_zpid/`
      URL**, never the `/homes/…_rb/` search URL `zillowUrl()` builds. Comps are
      resolved through `_zillow_detail_url()` (1 API call each, ≤4 per sheet), and
      any that fail to resolve are REPORTED to the user rather than written as a
      link that would never populate.
    - **NOTE the sheet's numbers legitimately differ from the map's.** Our comp
      inventory is the last LIST price from the iSpeedToLead feed; the sheet pulls
      Zillow's actual sold data. On the verification run 2538 N Talbott read
      $289k/1,395sqft on the map and $225,000/2,040sqft in the sheet. That is two
      sources, not a bug — but it will be asked about.
    - `rapidapi_key` and `rapidapi_zillow_key` in site_config are the SAME key
      (verified identical); underwriting reads the former, `crm/api/zillow.py` the
      latter.
  - **The pin popup leads with the metric that matters for that STATUS.** "99
    days" means opposite things on the two kinds of pin — 99 days ON the market
    for a live listing (it is not selling) versus 99 days SINCE it left for an
    off-market one (how current the evidence is) — so they are not rendered the
    same way:
    - off-market: `Off-market Jul 31, 2026 · 9d ago` then `Listed May 15 · 77d on
      market` (when it left is the headline, DOM is context);
    - for sale: `For sale · 50d on market` then `Listed Mar 30, 2026`.
    Deliberately **"off-market", never "sold"** — this inventory is the last ASK
    and leaving the market is not a confirmed close. Data supports it with no
    gaps: 42,230 inactive comps are **100%** populated for `removed_date`, and
    10,009 active are 100% populated for DOM.
  - The details toggle is a **checkbox**, not a button: a button reading "Details
    off" is ambiguous about whether that is the state or the action.
  - **Hide / use a comp** (`set_comp_state`; `CRM Lead.comps_hidden` /
    `comps_selected` JSON; ops `setup_comp_selection.py`). **TEAM-WIDE, not
    per-user**: a junk comp is junk for everyone, and "the comps we priced off" is
    a deal artifact the next person needs. Three rules earn their keep:
    - a **hidden** comp leaves the pool entirely, including the tier count —
      otherwise junk keeps a tier "usable" and suppresses the widening the rep
      needs;
    - a **selected** comp is PINNED past every filter and never faded — an
      explicit human pick outranks a derived filter, the Today-board rule that the
      machine decides what LANDS and the human owns it afterwards;
    - the two are mutually exclusive, so picking a comp you hid un-hides it.
    Shortcuts **U** / **H** act on the open popup.
  - **Picking a comp ASKS what kind it is** (gw433/gw437/gw438): New Build /
    Fix and Flip / Move-in Ready / Remodel / Full Gut / Teardown (Lance's
    order and wording — the gw433 vocabulary lasted hours; `LEGACY_CONDITION_
    TYPES` maps anything stored under it on read). `CompConditionModal` opens
    on every pick from any surface (tray, pin, gallery, `U`) with "Skip for
    now"; not on un-pick or when already tagged. The native `<select>` chip on
    the tray card + detail modal edits it later (native on purpose — the
    reka-ui empty-value trap). `set_comp_type`; `CRM Lead.comps_types` JSON map
    (docname → label), team-wide, ops `setup_comp_selection.py`, has_column-
    guarded. A tag survives un-picking and re-renders on re-pick (forgiving).
    CompOfferCalc's status cell reads "Sold · Full Gut".
  - **The subject's sqft is editable** (gw433, German's ask — Zillow is
    sometimes simply wrong about the one house being priced). Pencil on the
    subject tray card → `set_subject_sqft` → `CRM Lead.sqft_override` (Int,
    0 = none, ops `setup_comp_selection.py`). Applied at the ONE derivation
    point (`_subject_facts`, which relabels `source.sqft` to "manual"), so the
    card/pill, the ladder's sqft tiers, tray ± deltas and the calc's repair
    $/sf totals all read it with zero per-consumer changes. Reset reverts to
    Zillow/listing.
  - **Redfin cross-check on the subject** (gw433/gw434): `get_lead_comps` joins
    a pure-requests thread (≤1s budget; miss → background `warm_subject_check`,
    so a cold lead's flag usually appears on the SECOND fetch) that fetches the
    subject's Redfin record from redfin-scraper-api and compares beds/baths/
    sqft/year vs the Zillow facts (`crm/api/redfin.py`; sqft >5%, baths >0.25,
    year >1). Material disagreement → `subject.redfin_check` → amber
    `CompDiscrepancyFlag` on the subject card + pin popup ("Zillow 1,144 ·
    Redfin 1,694 sqft"). Only facts sourced "zillow" AND exact compare, so a
    manual sqft override drops the flag — a settled question is not reopened.
    **GOTCHA (gw434): cache the RECORD, never the verdict** — v1 cached the
    computed comparison and a later override kept serving the stale flag for
    14 days; `redfin_subject:v2` stores the record and recomputes per request
    (pure, microseconds). Prefix declared in `persistent_cache_keys`.
    Config-gated on `redfin_scraper_url`; absent → silently no flag.
  - **ONE unlabelled value on the subject tile** (gw446–gw449, Lance):
    the first of **Zestimate → Realtor → Redfin → tax assessed** that exists,
    under Last sold on `CompSubjectCard`; nothing if none. They are FALLBACKS,
    not a comparison panel (a labelled three-up line was built and pulled the
    same day). The tooltip names the source (Realtor's carries range + its
    other two AVMs) and the Realtor value links to the Realtor page. The pin
    popup still lists all of them labelled. Sources: Zestimate is on the
    Zillow facts; the **Redfin Estimate** rides on the same `/facts` record
    the discrepancy flag uses (redfin-scraper-api returns `avm.estimate` —
    `price` there is the last SALE; `redfin_subject` cache v3); the
    **Realtor estimate** is `crm/api/apivex.py` `start/finish_realtor_estimate`
    — Apivex `/realtor/property/details?address=` → `property_id` →
    `/property/estimates` (does NOT resolve an address itself; both calls
    billed, ~1.5s, 4/4 hits measured incl. off-market), `isbest_homevalue`
    model wins. Same thread + 1s join budget + background warm shape as
    Redfin, so a cold lead shows it on the SECOND open; cached 30d (miss 7d)
    under `crm:realtor-estimate` (in `persistent_cache_keys`). Street-key
    match required — the neighbour's estimate is worse than none — but LOOSE
    on a trailing directional/suffix (`_same_street`: same house number +
    prefix): Realtor answered "4205 NC 210 S" for "4205 NC-210" and the exact
    test threw the right house away (cache v2). **Zillow has no Zestimate for
    plenty of rural parcels** (NC-210: null, with a rent Zestimate present) —
    that is what the fallback chain is for.

- **A stale-copy save retries itself** (2026-09-10, Dennis: "I have to
  refresh the page before I can change the status"). `document.save`
  posts the WHOLE doc, `modified` included, and Frappe refuses it with
  `TimestampMismatchError` once anything has touched the row since the
  page loaded it — and on this CRM that is routine: **adding a comment
  bumps the lead's `modified` in a background job a second later**
  (upstream `on_comment_insert` → `update_modified_background`, on via
  FCRM Settings `update_timestamp_on_new_communication`), the Today
  board's outcome comment does the same, Quo webhooks relink call logs,
  contract parsing writes terms, two quick edits race each other. So
  comment-then-change-status failed every time and Lance could not
  reproduce because he does not comment first. `data/document.js`
  (`submitWithMismatchRetry`) now refetches, re-applies only the fields
  that save changed (diff vs `originalDoc`) and submits once more; the
  toast is held until the retry itself fails. Covers every
  `document.save.submit` caller (Lead/MobileLead/Deal/side panel/lost
  reason). Diagnosis tip: a lead whose `modified` moved with **no Version
  row** was bumped by a `db.set_value`, not a person.
- **Every lead view reads newest-first** (gw303) — only the Activity timeline was
  most-recent-first; Comments, Calls, Tasks, Notes and Attachments made you
  scroll to the bottom to find out what just happened. `Activities.vue` now
  reverses the whole merged feed. **Text and WhatsApp are deliberately
  excluded** — they render from their own chat resources (`smsMessages` /
  `whatsappMessages`, both `sortByCreation` ascending) and keep chat's
  convention, where the newest message belongs at the bottom next to the
  composer. The mount-scroll lost its per-tab special case with it, since
  "newest" is now always the first element. Applies to the Buyer page too, which
  mounts the same component.

- **Activity feed no longer auto-scrolls on every reload** — the Lead/Deal
  Activity timeline used to yank the viewport on every action (adding a
  comment/task, sending a text) and on every realtime reload from a teammate,
  because each feed resource re-ran `scroll()` in its `onSuccess`, the comment
  composer emitted `@scroll` on send, and the attachments `@reload` chained
  `scroll()`. On the Comments tab that forced the feed to the bottom; on Activity,
  to the top — and the user then had to scroll back to the To-do/quick-add at the
  top. Now auto-scroll happens **only on mount** (deep-link to a `#comment` hash,
  or no-hash → newest entry). Removed the `onSuccess` scroll from all six feed
  resources (`all_activities`, `whatsappMessages`, `smsMessages`, `taxPulls`,
  `agreements`, `underwritingWorkbooks`), the `CommunicationArea` `@scroll`
  handler, and the attachments `@reload` scroll; kept the SMS/WhatsApp chat-tab
  send-scroll (standard chat behavior) and the mount `scroll(hash)`. Pure
  frontend. `frontend/src/components/Activities/Activities.vue`. Requested by
  Lance (the feed "scrolls to the bottom" every time he did anything).

- **Lead name auto-formatting** — inbound leads (iSpeedToLead webhook, manual
  entry, imports) often arrive oddly cased ("joe cholock", "priscilla Diaz",
  "JOHN SMITH"). `crm/api/name_format.py` `format_person_name()` title-cases a
  person name but only re-cases words that look machine-mangled (all-lower /
  all-upper), preserving already-cased names (McDonald, DeAngelo, O'Brien);
  handles Mc-, the O'/D' apostrophe family, hyphenated names, Jr/Sr/Roman-numeral
  suffixes, and keeps connectors lowercase ("Carol and Charles"). Idempotent.
  Wired as a `CRM Lead` `before_validate` hook (`normalize_lead_names`) that
  re-cases `first/middle/last_name` **on creation only** (later manual edits are
  respected); `validate()` rebuilds `lead_name` from the normalized parts.
  `backfill_lead_names(dry_run=1)` is a bench-executable backfill (writes via
  `db.set_value`, `update_modified=False`, no `doc.save` → no side-effects;
  dry-run by default). Pure app code — no ops/server-script piece. Backend only
  (no `.vue`). `crm/hooks.py` + `crm/api/name_format.py`.

- **Lead-owner round robin (German ↔ Exe ↔ Dennis) on new inbound leads** — every
  ownerless lead used to be stamped with one hardcoded owner by the ops server
  script `Lead Default Owner`, which is why `lead_owner` said Dennis on ~99% of
  leads even though German and Exe do the calling. A `CRM Lead` **`before_insert`**
  hook now hands each new inbound lead to the next setter in the rotation.
  Setting `lead_owner` is the whole job — `CRM Lead.after_insert` then does the
  DocShare + `_assign` ToDo (verified end-to-end on prod, incl. the **Guest**
  path the webhooks actually insert through). `crm/api/lead_round_robin.py`
  (**new**) + `crm/hooks.py`.
  - **Why `before_insert` in app code beats the server script**: `run_method`
    calls `Document.hook(fn)` (all `doc_events`) and only *then*
    `run_server_script_for_doc_event` — frappe `document.py` lines 1011 vs 1015.
    So app hooks always win, and `Lead Default Owner` (`if not doc.lead_owner`)
    degrades into a **safety net** that still stamps Dennis if the roster is
    empty/disabled or this code raises. The hook swallows every exception on
    purpose: a misrouted lead beats a lost lead.
  - **No stored counter — the rotation is derived from the leads themselves**:
    whoever holds fewer of *today's* leads gets the next one, ties broken by
    alternation from whoever got the most recent one. Nothing to drift or reset,
    and no read-modify-write to race on (two simultaneous webhook leads can both
    pick the same person; the next lead self-corrects — verified). The daily
    reset is the point: an all-time balance would mean a week off creates a debt
    that dumps the next hundred leads on whoever came back. Alternation still
    carries across midnight via the "who got the last one" tiebreak.
  - **Scope**: only leads created with **no owner** — the inbound webhooks
    (iSpeedToLead / Red Panda / PropertyLeads / Leadzolo), which insert as Guest.
    UI-created leads already carry `lead_owner = current user` (`LeadModal.vue`)
    and are left alone. **Bulk imports are excluded** (`import_hidden`): the
    importer has its own "split between" picker, and a 500-row LeadPack would
    swamp a daily rotation for leads that are parked rather than worked. Parked
    leads are excluded from the *tally* too, via the NULL-safe
    `import_hidden.isnull() | != 1` form (`!= 1` alone silently drops NULL rows —
    the same trap `leads_dashboard.live()` documents).
  - **Dennis joined the rotation 2026-08-21** and it needed no code — the roster
    is a config key, so it was one `bench set-config`. What made it necessary was
    visible in the data before anyone asked: he had spent the morning
    hand-taking leads, reassigning 5 of that day's inbound one at a time off Exe
    and German at 12:10–12:12. The rotation was working perfectly; he simply
    wasn't in it. **Adding someone mid-day deals them a catch-up burst**, because
    the balancer works off the DAY's counts — Dennis was 11 behind German, so the
    next 7 leads went straight to him before alternation resumed (simulated
    before applying: 18/18/17 after 20 leads, clean G→E→D from the next morning).
    That is the daily reset doing its job, not a bug, but it is worth saying out
    loud before flipping it. Reverting is `bench set-config` back to two names.
  - Roster + kill switch live in site_config (`lead_round_robin_users`,
    `lead_round_robin_enabled`); disabled Users drop out automatically, so
    disabling a CRM login is the vacation lever. `round_robin_status(source)` is
    the whitelisted read-only "who's up next and why" (it now takes the source,
    because rules are per-source — see **Lead Assignment settings** below).

- **Lead owner reservations (LeadMarket names the owner BEFORE the vendor
  webhook creates the lead)** — `crm/api/lead_reservation.py` (**new**) +
  a `reserved_owner_for(doc)` check at the top of the round-robin
  `before_insert` hook + `stamp_matched_lead` on `after_insert`. LeadMarket's
  Buy button says "this one is Dennis's" at click time; iSpeedToLead's webhook
  then creates the lead **already owned** by him — no post-hoc reassign, no
  double `_assign`, no sequence auto-enroll under the wrong setter, and
  LeadMarket being down at webhook time just means the rotation decides.
  Rotate = no reservation (the CRM's own per-source rule decides).
  - **Match key is the marketplace `published_date` (epoch ms)** — the ONE
    pre-purchase datum both systems share (the feed exposes no address/phone
    before you buy). iSTL's webhook sends it as `lead_published_date`, verified
    identical to the millisecond on prod; the ops webhook script copies it to
    `CRM Lead.vendor_published_date` (Custom Field, hidden). ZIP is a guard
    when both sides have one. Both sides normalise to an epoch-ms string.
  - **Whitelisted**: `reserve(source, lead_owner, marketplace_lead_id,
    published_date, zip?, requested_by?, ttl_hours=24)` upserts on the
    marketplace id; `release(id)`; `status(ids)`. Sales roles.
  - **Races** (all verified on staging 2026-09-05 with guest webhook POSTs):
    reservation first → lead born with the owner + `matched_lead` stamped;
    webhook first → `reserve()` moves the lead via
    `lead_owner_backfill._reassign` (old ToDo Cancelled, new one Open) and
    records `previous_owner`; ZIP mismatch → rotation; `release` → rotation;
    release of a matched reservation is **refused** (it would not un-own the
    lead); vendor retry/duplicate insert → same person, because a reservation
    is **never consumed** — it stays live for its TTL.
  - A reservation applies even while the rotation is paused: it is an explicit
    human choice. Hard skips (owner already set / bulk import / migrate) still
    win. Nothing in the insert path raises.
  - Guarded on the doctype + column existing. Ops:
    `scripts/setup_lead_owner_reservation.py` (doctype `CRM Lead Owner
    Reservation` + the lead column) then
    `scripts/sync_server_scripts.py ispeedtolead_webhook`. Unmatched rows are
    pruned after 14 days on the next `reserve()`; matched rows are the audit
    trail. GOTCHA: a matched reservation Links the lead, so deleting a test
    lead needs the reservation gone first (or `frappe.delete_doc(force=1)`).
  - Consumer: `../leadmarket` `src/assign.py` (calls production via bench
    regardless of its staging `CRM_HTTP_BASE` override; mirrors the rows
    locally so cards can show "→ Dennis" / "✓ Dennis · CRM-LEAD-…" / "⚠ CRM
    gave it to Germán" without a round-trip per page).

- **Lead Assignment settings page (per-source rules)** — Settings → Automation &
  Rules → **Lead Assignment**: one row per lead source, in plain language
  (*iSpeedToLead → rotate between German, Exe* · *Leadzolo → always Lance*), plus
  a catch-all for everything else and a pause switch. Replaces editing
  `lead_round_robin_users` with `bench set-config`, and gives Leadzolo a home in
  the CRM instead of LeadMarket's hardcoded `LEADZOLO_CRM_OWNER`.
  `crm/api/lead_assignment.py` (**new**) + `LeadAssignmentSettings.vue` +
  `LeadAssignmentRuleRow.vue` (**both new**).
  - **The split of responsibility is the design**: `lead_assignment` decides
    *whose turn it is among whom* (the per-source roster), `lead_round_robin`
    still decides *whose turn it is* (fewest-today-wins, ramp, disabled-user
    filtering, swallow-every-error). `_apply_rule()` is the single place a rule
    becomes a person, so hook / preview / status cannot disagree. One-way
    import: `lead_round_robin` imports `lead_assignment`, never the reverse (the
    settings endpoint imports back **inside the function** to avoid the cycle).
  - **Upstream's "Assignment Rules" page is HIDDEN, not deleted**
    (`SHOW_UPSTREAM_ASSIGNMENT_RULES = false` in `Settings.vue`). Core Frappe's
    `Assignment Rule` writes `_assign` on **after_insert**; this CRM keys
    everything off `lead_owner` on **before_insert**, so running both is two
    deciders racing across two hooks and a lead carrying both fields is the
    double-assignment bug `lead_import` warns about. There were **0** rules on
    prod, so nothing was lost. Keeping the import means a rebase that touches
    that page still compiles.
  - **Modes are `rotate` / `fixed` / `off`.** `fixed` is not just a
    one-person rotation — saying so out loud lets the UI render it as a choice,
    and switching rotate→fixed **narrows the picker visibly** rather than
    silently keeping four names and using the first. `off` leaves the lead
    ownerless, i.e. exactly the pre-rotation behaviour, and the legacy
    `Lead Default Owner` server script still catches it.
  - **Stored as one JSON blob in a global Frappe default** (`frappe.db.get_default`,
    key `crm_lead_assignment_rules`) — the same no-new-doctype trick the Team
    Activity goals use. **No ops script**, readable by **Guest** (which is how
    every inbound webhook inserts), and one query on a hot insert path.
  - **Unconfigured ≠ empty.** `rule_for()` returns None until a human saves, and
    everything then falls back to the site_config roster, so **deploying this
    changes no behaviour at all**. A corrupt blob degrades to the same fallback
    rather than breaking lead creation.
  - **A rule with nobody in it is REFUSED, not ignored**: silently falling
    through to the default looks identical on screen to a rule that is working.
    Same for an unknown user or an unknown source.
  - **A rule whose every named person is disabled assigns NOBODY** rather than
    falling through to the default — someone configured that source, and quietly
    routing it elsewhere is worse than leaving it for the legacy default owner.
  - **GOTCHA — `frappe.db.get_default` can serve a STALE value to a
    `set_default` in the same process.** Saving `enabled: false` and immediately
    inserting a lead still assigned an owner, while the identical write in a
    fresh process paused correctly — i.e. it fails *intermittently* and reads as
    a logic bug. `frappe.defaults._clear_cache("__default")` after the write is
    the fix. Note the helper is **private and singular**; `frappe.defaults.clear_cache`
    does not exist, and calling it inside a whitelisted method makes
    `bench execute` swallow the AttributeError and raise a completely unrelated
    **`NameError: name 'crm' is not defined`** (`frappe/commands/utils.py` falls
    back to `eval` when `get_attr(...)()` throws). That message means "this
    function threw" — never trust it at face value.
  - **GOTCHA (again) — reka-ui drops a Select item whose value is `''`.** The
    `{label:'Select a person', value:''}` placeholder option simply never
    rendered. Use the `placeholder` prop. Third time this trap has been hit in
    this repo.
  - Verified on **staging** end-to-end (`ENV=staging ./scripts/build_image.sh`),
    inserting as **Guest** the way the webhooks do: Leadzolo→Lance twice,
    Website→German then Exe, hand-created-with-owner untouched, `off`→no owner,
    paused→no owner, all four validation refusals, corrupt blob→roster fallback,
    and the UI round-tripping a save across a full page reload.
  - **NOTE `build_image.sh` ships TRACKED FILES ONLY** (`git stash create` +
    `git archive`). New files must be `git add`ed or they silently do not deploy
    — the app then imports a module that isn't there.
  - **Catch-up ramp** (`lead_round_robin_ramp_user` / `_ramp_count` /
    `_ramp_since` in site_config) — hands the first N leads after a moment to
    one person before alternation starts, because the continuity backfill lands
    lopsided (70/36) and the person behind should get the next few. Same
    derived-from-data rule as the rotation: the ramp leads **are** the first
    `count` leads owned by that user created at/after `since`. Nothing is
    decremented — no counter to drift, no read-modify-write to race on, and it
    self-expires (the keys can then be deleted at leisure).
    - **Shipped 2026-08-05 as 5 leads to Exe** (`since` 18:36:08). Because the
      count is read from config on every call, it is tunable live —
      `bench set-config lead_round_robin_ramp_count N`, effective on the next
      lead, no deploy. It was set to 10, then changed to 5 before any lead had
      been delivered. site_config stores it as a **string**; `_ramp_state`
      coerces with `int()`.
    - **GOTCHA — ramp deliveries must be netted OUT of the daily tally**
      (`_todays_counts` subtracts `delivered_today`). Without that the ramp
      undoes itself inside one day: N leads to Exe, the tally then reads
      Exe N / German 0, and the balancer sends the next N to German for a net
      effect of **zero**. Verified on prod with real inserts (rolled back) at
      both settings: `EEEEEEEEEE`/`EEEEE` followed by clean `GEGEGE…`
      alternation, with Exe still +10/+5 at the end and
      `round_robin_status().why` back to "normal rotation".
    - Every caller routes through `_choose(roster)` so the hook, `next_owner()`
      and `round_robin_status()` cannot disagree about whose turn it is.
  - **GOTCHA — `_last_owner` must exclude parked imports** (it now shares
    `_exclude_parked` with the tally). The June LeadPack put **514** parked leads
    on the two setters with recent `creation` stamps, so the "most recent lead"
    tiebreak was otherwise decided by whoever happened to be last in a
    months-old bulk job.
  - **Ops consequence — `lead_ring_alert.py` `DEFAULT_OWNER` had to change.**
    `PUSHOVER_KEYS` only maps Lance and Dennis, and an unmapped owner fell back
    to **Lance**. That fallback was previously unreachable (every lead was
    Dennis's); with the rotation it would have fired on *every* new lead,
    ringing Lance at priority 2 with 5-minute re-rings for an hour. Fallback is
    now Dennis — exactly who has been getting these alerts all along. To give the
    setters their own ring, add their Pushover user keys to Infisical and map
    them; the lookup already keys on `lead_owner`.
  - **Ownership now genuinely moves**, so things keyed on `lead_owner` follow it:
    sequence call-tasks (`crm_sequence_runner_core.py`) and agreement
    view/sign notifications (`agreement_notify.py`) go to the setter who owns
    the lead instead of Dennis. The Today board is scoped by it (see below), and
    German's/Exe's `/dashboard` populates for the first time.
  - **And the WORK on the lead moves with it** (`crm/api/lead_owner_change.py`,
    a `CRM Lead` `on_update` hook). Two things used to stay behind:
    - **Open tasks kept the old owner**, who can no longer see the lead — so the
      task was invisible to both of them while still counting as due work
      against the wrong person. Only OPEN tasks move (a Done task is a record of
      who did it, and rewriting it would rewrite history), and only tasks that
      were *following* the old owner — assigned to them or to nobody. One
      deliberately handed to a third person is a human decision and stays.
      Moved with `doc.save()`, not `db.set_value`: `CRM Task.validate()` is what
      unassigns the previous user and creates the new ToDo, and
      `notify_task_update` is what refreshes every open board and to-do block. A
      silent column write would move the task on paper only.
    - **The old owner stayed assigned to the lead.** `CRM Lead.validate()`
      already shares and assigns the NEW owner on an owner change, but
      `assign_agent` **only ever ADDS** — there is no removal half — so a lead
      that changed hands twice ended up assigned to three people and every
      previous owner kept seeing it. This is the same double-assignment
      `lead_owner_backfill` had to fix up by hand for its bulk run; it is now
      fixed once, for every path that changes an owner. Only ever the previous
      *owner* is dropped; anyone else was put there by a person.
    - A **cleared** owner is deliberately not treated as a handover: stripping
      the old owner's assignment would leave the lead owned by nobody AND
      assigned to nobody, i.e. invisible on every board — strictly worse than
      leaving it until someone picks it up. Everything is best-effort and
      swallowed: a hygiene hook must never be why a rep cannot save a lead.
    - NOTE `lead_owner_backfill` writes with `db.set_value`, which fires no doc
      events, so a **bulk backfill does NOT move tasks** — deliberate (it fixes
      assignment itself, and re-running SLA over hundreds of leads is what that
      module exists to avoid), but remember it if the two ever disagree.
  - **Backfill** — `crm/api/lead_owner_backfill.py` (**new**,
    `backfill_lead_owners(dry_run=1)`) moves EXISTING leads onto the setters.
    Required, not optional: the scoped Today board shows German and Exe nothing
    until it runs (all 81 of 2026-08-05's cards belonged to the old default
    owner). Scope is **live workable leads only** — converted / dead / won /
    parked-import (`import_hidden`) are excluded, and only leads on the *default*
    owner or with no owner move, so a lead a human deliberately took is left
    alone. Of 747 leads on prod, **106** qualify.
    - **Two strategies, because prior contact is 70 German / 6 Exe** and
      continuity genuinely conflicts with balance. Measured dry runs:
      `continuity` (default) → **70/36, 0 relationships broken**;
      `even` → **53/53, 38 broken**. Attribution reuses the activity report's
      exact `caller`→`receiver`→`custom_quo_number` chain so the two can't
      disagree about whose call it was; a lead is only claimed when ONE setter
      leads outright.
    - Writes `lead_owner` via `db.set_value(update_modified=False)` (no `doc.save`
      → no SLA re-application, no disturbed `modified`), so **there is no Version
      row and no timeline entry** for a moved lead — deliberate for a bulk admin
      action, but remember it when someone asks why a lead changed hands.
    - **Assignment is fixed up explicitly** because `CRM Lead.assign_agent` only
      ever ADDS: left to itself every moved lead would be assigned to the old
      owner AND the new one (the double-assignment `lead_import` warns about).
      The old owner's ToDo is removed only when they were the previous
      `lead_owner`; any other assignee was put there by a person and stays.

- **Realtime task auto-refresh (no page reload), site-wide** — `CRM Task` now
  broadcasts a `crm_task_update` realtime event (with
  `reference_doctype`/`reference_docname`) on `after_insert`/`on_update`/`on_trash`,
  mirroring the `quo_message` pattern. The Lead/Deal **Activity feed** reloads
  `all_activities` when the event matches the open doc (To-do block +
  completed-task history update live); the **Leads & Deals Kanban** refetch the
  board (to refresh the server-computed `_next_task_due` badge) but only when on
  the Kanban view AND the affected record is currently on the board.
  SMS→Activity was already live via the existing `quo_message` listener (the
  unified timeline reads `smsMessages` reactively).
  - **Site-wide delivery is automatic, not extra work**: `frappe.publish_realtime`
    with no `room`/`user` broadcasts to the site room (`get_site_room()` → `"all"`),
    and the socketio handler (`frappe_handlers.js`) joins *every* logged-in System
    User to that room on connect. So one user's task/text update reaches all
    other users' open boards/leads. (Do NOT pass `doctype`/`docname`/`user` — that
    would *narrow* delivery to a single room.)
  - **`after_commit=True` on both publishes** (`crm_task_update` AND `quo_message`)
    — defers the emit until the DB transaction commits, so a listener's reload
    can't race ahead of the commit and read stale data (the old default
    `after_commit=False` is the likely cause of the "text doesn't show until I
    refresh" flakiness: the emit fired pre-commit, the client refetched, and the
    new row wasn't visible yet).
  - `crm/fcrm/doctype/crm_task/crm_task.py` — `notify_task_update()` +
    `on_update`/`on_trash` hooks (publish `crm_task_update`, `after_commit=True`)
  - `crm/api/sms.py` — `on_quo_message_insert` now uses `after_commit=True`
  - `frontend/src/components/Activities/Activities.vue` — `crm_task_update`
    listener → `all_activities.reload()`
  - `frontend/src/pages/Leads.vue` + `pages/Deals.vue` — `crm_task_update`
    listener → `reloadKanban()` (Deals gained a `reloadKanban` helper); on-board
    membership guard to avoid needless reloads
- **"Today" board** (`/today`, top of the sidebar) — the surface the setters
  work the day from; the 5am DM describes it, this is where German and Exe do it.
  Three columns (**To Call / Done / Skipped**) built from the SAME cadence
  definition as the standup DM, so the morning-call list and the worked list are
  the same list. `frontend/src/pages/Today.vue` + `crm/api/today_board.py`.
  - **Scoped to YOUR leads, with a board switcher** (was one shared pile until
    ownership was split). `get_today_board(owner=...)` defaults to the caller;
    a dropdown next to the title opens anyone else's board or `owner="all"`
    (the whole team — what everyone saw before). **Ownership is read off the
    lead at request time, never stamped onto the card**: a card stamped at 5am
    would keep pointing at the old rep the moment a lead was reassigned, and
    reassigning is exactly what the round robin and the backfill do. Generation
    is untouched — what LANDS on the board is unchanged, only the view is scoped.
  - The selector is built from the **day's cards**, not the user list, so it
    can't offer an empty board and an unexpected owner (a lead still on the old
    default) is visible rather than silently unreachable. It's counted **before**
    the owner filter so a rep whose own board is empty can still see where the
    work is; everything after the filter (status counts, columns, totals)
    describes the board actually on screen.
  - **Not persisted across reloads** — unlike `dispoView`/`activityScope`, which
    are view modes. This is *whose work you are doing*, and silently reopening on
    a teammate's list is the one mistake here that costs real calls.
  - **Cadence tracks every live status**, not just New / Called No Answer /
    Follow Up / Future Follow Up. Eligibility is `CRM Lead Status.type` not in
    Lost/Won (plus converted/parked still excluded); cadence still decides who is
    *due*. Cards mark **Cadence** vs **Task** (`in_cadence` / `has_task`); a due
    task on a cadence-due lead keeps the cadence phase so both badges can show.
    Leftover-task-only stays `phase=task`. Filters: in cadence / task / task-only
    / cadence-no-task.
  - **Bulk hand-over from the board** (`assign_today_leads`) — **Reassign** mode
    turns every card into a checkbox and puts an "Assign to…" bar above the
    columns. On the team (or anyone-else) board the owner name on the card is
    itself a picker, so a daily review can move one lead without entering select
    mode. The board is where a rep already has the day's work in front of
    them, so it is the cheapest place to say "these five are Dennis's now"; the
    alternative is opening five leads and editing a side-panel field on each.
    - **Nothing about the cards is migrated, and that is the design paying off.**
      Ownership is read off the lead at request time and never stamped onto the
      card, so a reassigned lead's cards simply appear on the new owner's board
      on the next load — state, order and outcome intact. There is deliberately
      nothing to move.
    - **The destination list is every user who works leads** (`_assignable_users`,
      Sales User / Sales Manager, enabled, System User), **NOT `_owner_options`**,
      which is built from the day's cards. That distinction is the whole point:
      the person you most need to hand a deal to is precisely the one with an
      empty board. A cards-derived list would have made Dennis unreachable as a
      destination on exactly the day he needed the deal.
    - **Counts are stated in LEADS, not cards.** A lead can hold two call cards,
      so "4 selected" followed by a toast saying 2 leads moved reads as a bug;
      ownership moves per lead, so that is the unit to lead with (the card count
      rides alongside only when the two differ).
    - **Dragging is disabled while selecting.** A drag and a pick are the same
      gesture on a card, and a mis-drag in selection mode would RESOLVE a card
      and open the outcome modal when the rep meant to tick it.
    - Uses `doc.save()`, unlike `lead_owner_backfill`'s `db.set_value` — this is a
      handful of leads moved by a person on purpose, and "changed Lead Owner from
      German to Dennis" on the timeline is exactly the audit trail anyone will
      later want. It is also what fires `lead_owner_change`, so the open tasks and
      the stale assignment follow. Per-lead failures are collected, never thrown:
      a lead sitting in a Lost status with no lost reason cannot be saved at all
      (`validate_lost_reason`), and one such card must not abort the other four.
    - `frontend/src/pages/Today.vue` + `crm/api/today_board.py`. No ops piece, no
      schema change.
  - `get_today_report(owner=...)` scopes **today's** figures + `completed_by` the
    same way (a bar reading 12/87 over a 30-card board is worse than no bar). The
    **streak and recent-day history stay team-wide**. Response `scope` is
    `{today: owner|team, streak: team, recent: team}`.
  - **Cards are rows, not a live recomputation** (ops doctype `CRM Today Item`).
    "Done"/"Skipped" are judgements a person made; recomputing would lose them,
    or resurrect a dismissed card, as soon as a call got logged — and the board
    has to hold still while people work it. Division of responsibility: **the
    cadence decides what LANDS on the board; humans own the card after that.**
    Generation only ever ADDS and never retracts a card that stopped being due.
  - **The list stays current after the 5am snapshot.** Header **Sync list**
    manually re-runs the shared cadence and reports how many cards it added.
    New leads and every lead-task mutation enqueue the same add-only sync after
    commit; a five-minute scheduler is the race/failure safety net — note it is
    on `*/5 * * * *`, i.e. **round the clock**, not business hours as this file
    long claimed. Jobs dedupe during import bursts, and structural card autonames
    make concurrent runs safe. New scheduler method
    `crm.api.today_board.run_today_sync` requires `sync_jobs` after deploy.
    Existing Done/Skipped state and manual ordering are never touched.
  - **The board CLOSES to new cards at 4pm CT** (`BOARD_CLOSE_HOUR`, gw316;
    17→16 in gw433 at Lance's ask — note 9/1's actual streak-breakers arrived
    3:52pm, so the earlier close alone would not have caught them; Dennis had
    worked both leads and simply never ticked the cards).
    `cleanup_late_cards(day, dry_run=1, close_hour=None)` (gw433) is the
    bench/whitelisted repair for late-added orphan cards: deletes only cards
    created at/after the cutoff, never touched, whose lead is on a LATER board
    day (lossless); `close_hour` overrides the cutoff for one-off repairs.
    Working a card after 5pm is fine and always was — what is not fine is the
    board growing after everyone has gone home, because a card added at 11pm is
    unresolvable and silently reads as a rep who did not finish. Measured: German
    and Exe resolved **every** card on both 2026-08-10 and 08-11, and both days
    still scored 71%/68% and 94%/93%, because 20 inbound leads landed at
    23:20–23:57 on the Monday (40 cards) and 4 more at 23:33 on the Tuesday (8).
    Those late cards were the entire unresolved remainder of both days.
    - **Nothing is lost by holding them back**: all 20 and all 4 of those leads
      appeared on the NEXT morning's board anyway (20/20, 4/4), so the late add
      was pure duplication that only cost the score. The nightly generation is
      what picks them up, and a never-called lead is still due the next day.
    - `_board_is_closed(day)` gates `_generate_today`, so all three callers obey
      it: the manual button, the new-lead/task hook (which also stops before
      enqueuing, so a late-evening import doesn't queue a job per commit for a
      board that will refuse every one) and the round-the-clock scheduler. A day
      already past is closed too — materialising fresh work onto a board nobody
      will look at again is the same mistake.
    - **Sync list says so out loud** ("The list is closed for today — new leads go
      on tomorrow's list", amber). "List is up to date" would read as a lie to
      anyone who knows a lead just came in.
    - The 48 orphaned cards from Aug 10/11 were deleted on prod under exactly
      those conditions (after-5pm creation + never touched + lead present on a
      later board), restoring both days to 100% and both reps to a 5-day streak.
  - **The streak is TEAM-WIDE** (2026-08-28, reversing gw303's personal streak).
    `get_today_report` always computes the streak (and recent-day history) from
    every card; `owner` only scopes **today's** bar + `completed_by` so they
    still match the board on screen. The flame button reads `🔥 Team · N days`.
    A perfect day still means every card on the **team** board is Done or Skipped.
  - **Resolving a card writes the outcome onto the lead's timeline** (gw303) —
    `_log_outcome_comment()` posts a Comment on the CRM Lead ("Today board —
    marked Skipped (call 2)" + the reason). The board is where the judgement is
    made, but the lead page is where anyone later asks what happened, so a skip
    reason now survives past 5pm. Both resolve paths log: `set_today_state`
    (with the outcome, and again as "outcome corrected to …" if the rep
    re-answers) and `reorder_today`'s fallback branch for a drag that skipped the
    modal — the normal drag already went through `set_today_state`, so
    `cur != target` is what stops it double-logging. **Put-backs are never
    logged**: undoing a mis-click is not a judgement, and logging it would bury
    the entries that are. Best-effort — a timeline write can never fail a rep's
    click.
  - **Today report + team streak** — the flame button opens current Total/Done/
    Skipped/Remaining progress, who completed today's cards, and recent business-
    day resolved rates (Done + Skipped). A perfect streak day means every card has been resolved
    as **Done or Skipped**. An unfinished current day does not erase the streak
    earned through the previous business day.
    `frontend/src/components/TodayReportModal.vue` +
    `TodayReportMetric.vue`; `crm.api.today_board.get_today_report`.
  - **autoname `format:{for_date}-{lead}`** makes (date, lead) structurally
    unique, so generation at 5am + manual Refresh + first-page-view auto-generate
    can all race without duplicating a card (verified under a real
    clear-then-regenerate race: 0 duplicates).
  - Each card is now **one call**, not one lead: first-week leads that owe two
    calls get independently actionable **Call 1 of 2 / Call 2 of 2** cards.
    `CRM Today Item.call_number` extends the structural key to
    `(date, lead, call_number)`; the schema upgrade preserves old card state/order
    and expands an old `calls_needed=2` row on first read. If the first call was
    logged before generation, only Call 2 is materialized.
  - Cards show **address and phone on separate lines**, the soonest open task
    (click → the real Task modal), and a green **incoming-text flag** with the
    last received time. The lead status is a color-dot dropdown directly on the
    card; changing it saves the full CRM Lead (status history + hooks preserved),
    updates both call cards for that lead optimistically, and asks for the normal
    Lost Reason before a Lost-type status can be applied. Open tasks use a neutral
    empty circle. When the card is
    Done and a task was completed that board day, it shows the latest completed
    task with a green check instead of the next future task. Clicking the address
    on either the card or lead modal
    asks **Google Maps or Zillow** and opens the chosen destination; both use the
    shared `utils/propertyLinks.js` builders also used by the full Lead page.
    The message icon opens the standard Send Text modal with
    the cursor already in the composer; Today adds **Skip / Send / Send & finish**
    so the card can be judged without returning to the board.
  - **Zillow address miss is said out loud** — if `/property` cannot resolve the
    lead, Today shows an amber “ask the seller” banner (and a card flag once we
    have asked). Saving a new address reveals **Rerun comps**, which clears the
    geocode + BatchData caches and force-refetches. Auto-refetching a miss would
    re-bill it. `ZillowAddressMatch.vue` + `zillow.refresh_lead_facts` +
    `comps.zillow_match` + `today_board.zillow_unresolved`.
    - **GOTCHA (2026-09-16/17) — a Zillow OUTAGE used to read as an address
      miss.** The RapidAPI subscription lapsed (429 from 10:11 on 9/16, then
      403 "You are not subscribed to this API"); `facts_for_lead` cached every
      failed lookup as `{}` = "Zillow doesn't recognize this address" for 30
      days, so 28 leads told reps to ask the seller to confirm a fine address
      (Dennis, #bugs). A genuine miss is an HTTP 200 with no zpid (16 historic
      negatives vs 7 lookup failures ever), so a `None` body is NEVER a miss:
      it is no longer stored, `_zillow_match` / `refresh_lead_facts` carry
      `unavailable` (`not_subscribed` / `throttled` / `quota` / `error` /
      `not_configured`, per-request via `frappe.flags`), and the banner says
      "Zillow isn't answering" instead. A 403 also opens a **10-minute hold**
      (`zillow_outage`, same no-TTL storage rule as the quota key) so the
      prewarm stops logging ~275 failures an hour. Repair after such an
      outage: `bench execute crm.api.zillow.clear_failed_negatives --kwargs
      '{"since": "2026-09-16 10:00:00", "dry_run": 0}'`. Tests:
      `unit_test_zillow_unavailable.py`.
  - **The Today lead modal is now a qualify + comp + work surface**, without
    replacing the real Activity feed it already mounted. The left rail reuses
    `FirstCallReadCard` (Motivation × Price 2×2, saved onto the CRM Lead), and the
    right column is **two peer tabs, `Activity` and `Comps`** — the second being
    the REAL `CompsView` map, not a summary of it. Details are deliberately LAZY
    (nothing is billed just for opening a lead): `crm.api.comps.get_comp_details`
    makes the Zillow property + `/photos` calls only after a click, normalizes one
    ~1152px URL per image, and caches the result 30 days in Redis (partial
    failures retry after 1h). No schema/ops piece. `TodayLeadModal.vue` +
    `CompDetailModal.vue` + `utils/comps.js` + `crm/api/comps.py` / `zillow.py`.
    - **`TodayCompsPanel.vue` is GONE** (gw330). It was a horizontal strip of up
      to 8 comp cards — price, facts, `N/5 fit`, distance — and Lance's verdict
      was that the list is useless. It is: **a card can say "0.8 mi" but not
      WHERE**, and which side of the highway a sale is on is most of whether it
      comps at all. It also re-implemented in miniature what the map already did
      properly and could not do the things that make the map worth having — no
      filters, no recency fade, no hide/use, no radius, no ladder notice.
    - **Tabs, not a taller strip.** The modal's content area is ~655px at 88vh and
      the map alone needs ~550px with its filter bar, so stacking them would have
      left the Activity feed a hundred pixels. Both panes stay MOUNTED (`v-show`),
      so switching costs neither a refetch nor the Activity scroll position, and
      the pane choice is **not reset per lead** (unlike the Activity sub-tab): a
      rep comping a run of cards is in comping mode, and dropping them back on the
      timeline at every card would make them re-click it every time.
    - Comps mount **lazily on first use of the tab** (`compsOpened`), because a
      lead we have not looked up in 30 days costs a billed Zillow lookup and
      nobody should pay it for a lead they opened to read the timeline. The map is
      keyed on the lead, since `CompsView` loads `onMounted` and has no watcher on
      its `lead` prop.
    - Underwriting stays page-only (it needs the room), so the tab bar carries an
      **Open comps page** button rather than relying on the rep remembering.
    - **The left rail collapses on the Comps tab** (gw331) and the header is one
      dense line. The rail is 288px of First-Call Read beside a map that wants
      every pixel it can get — measured live, collapsing it takes the map from
      **360px to 648px wide**. The header spent ~90px on a name and two badges
      while the **phone number and address** — the two things a rep acts on — sat
      inside that rail, so those move UP into the header line and nothing
      actionable goes away with the collapse. The rail follows the PANE rather
      than being remembered (comps wants the room, activity does not); the
      chevron in the tab bar overrides it until the pane changes again.
      `CompsView`'s ResizeObserver re-measures the map on collapse, so no host
      has to tell it.
  - **The map absorbed the one thing the strip did better: photos.** The pin popup
    and every property-list row now open `CompDetailModal` (`data-comp-details`
    through the same delegated popup-click listener as use/hide). That is a
    straight win on the full page too, where photos previously did not exist at
    all — and photos are the fastest way to know a comp is not comparable, since
    square footage says nothing about a gutted shell beside a renovated flip.
    - **GOTCHA — `useKeyboardShortcuts` is opted out of `skipWhenDialogOpen` here**
      (it had to be, back when comps was itself a Dialog), so the gallery had to
      be excluded from `active` BY HAND. Without it `H` hides the very comp whose
      photos are on screen.
    - **Gallery provider links** — Zillow stays unchanged; Redfin/Realtor open
      observed listing URLs when available, otherwise **Google Redfin** / **Google
      Realtor** explicitly name the site-restricted Google search. Realtor reuses
      the subject's matched estimate `href`; Redfin uses redfin-scraper-api
      `GET /url` (AVM match, store then one ~110m live box — not website
      autocomplete) and falls back to `/photos` if that route is missing. No
      invented listing IDs. Thin galleries still reuse `/photos` for CDN frames.
      Both subject and comp galleries use `CompDetailModal` + `utils/propertyLinks.js`.
    - **The SUBJECT opens the same gallery** (gw352) — from its pin popup and from
      its tray card. It was the one house on the board you could not look at,
      which is backwards since it is the house being priced.
      `comps.get_subject_details(lead)` reuses `_shape_detail`, so the panel that
      renders a comp renders this too and the two cannot drift; `CompDetailModal`
      takes a `subjectMode` flag rather than existing twice. Same lazy contract as
      a comp — two billed calls, on an explicit click, cached 30 days — keyed on
      the zpid where we have one (from the lead's cached facts) so two leads on
      one house share it. No add-as-comp button, no fit badge, no distance: a
      house is not a comp for itself.
    - **GOTCHA — `loading="lazy"` DOES NOT FIRE inside the comps tray** (gw352).
      Not subtly wrong — it never runs. Measured: ten cards, ten `<img>` with
      valid srcs, **ZERO loaded** (`complete:false`, `naturalWidth:0`), including
      the first card fully on screen at y=572 of an 863px viewport; nudging the
      tray's scroll changed nothing; flipping one to `eager` loaded it instantly.
      The tray is a nested scroll container the document itself never scrolls, and
      Chrome's heuristic does not re-evaluate for it. **That was the whole "photos
      only load when I hover" bug** — hovering calls `prefetchPhotos`, which swaps
      the `src`, and a src change is what finally makes Chrome look. Replaced with
      an **IntersectionObserver rooted on the tray** (`[data-comp-tray]`): rooted
      on the viewport it cannot see past the tray's own clip, so the 400px of lead
      time is silently lost and cards fetch exactly as they appear. Still lazy on
      purpose — a 200-comp board must not pull 200 thumbnails on open. The
      gallery's own horizontal thumbnail strip is fine on `lazy` (verified 20/20),
      so this is specific to that scroller.
    - **GOTCHA — an occluded Chrome WINDOW reports `visibilityState: "hidden"`,
      and IntersectionObserver then never fires at all** — including its initial
      callback. A hand-attached probe logged **zero** entries and
      `chrome_screenshot` failed with "image readback failed", which together
      make a perfectly good observer look broken. `chrome_tab activate` is NOT
      enough (it only raises the tab within its window). `window.open(...,
      'popup=yes,width=...')` produces a genuinely visible window — the same trick
      the mobile-layout note uses. Check `document.visibilityState` before
      concluding anything about IO/RO/rAF.
    - **GOTCHA — Leaflet measures its container ONCE, at init, and a hidden one
      measures 0×0.** The existing one-shot `invalidateSize` 120ms after render
      only covers a container revealed within that window, which a tab is not, so
      the map arrived crammed in a corner. `CompsView` now owns a **ResizeObserver**
      on the map element instead — every show/hide/resize re-measures itself and no
      host has to remember to tell it. The callback defers a frame, or it trips
      "ResizeObserver loop completed with undelivered notifications".
    - Verified live at 1200px: map 696×384 with 12/12 OSM tiles painted, 12 comp
      pills + the Subject pill, identical size after switching to Activity and
      back (twice), popup → gallery → close leaving the Today modal intact, 0
      console errors. **Escape does not close the stacked gallery** (✕ does) —
      pre-existing, the strip nested it the same way.
  - **Filters** cover lead status, priority, incoming texts, and open tasks. A
    draggable **Priority order** modal saves each user's order cross-device via a
    standard Frappe user default (no custom field). Default = Never called → Task
    due → Week 1 morning → Week 1 afternoon → Weekly → Monthly. Week-one call 1/2
    are separate morning/afternoon passes; the persisted cadence phase stays
    `week1`, and `priority_key` derives the pass from `call_number` at read time.
  - A task after TODAY suppresses cadence generation (a later-today task still
    belongs today); if both an overdue task and a future appointment exist, the
    future appointment wins. Due tasks rank immediately after never-called.
  - Clicking a card now mounts the **actual Lead `Activities.vue` surface**, not
    a lookalike: the same timeline, quick comments, To-do quick-add, task edit and
    task completion behavior. `scrollOnMount=false` keeps the pinned To-do visible
    in this modal instead of the Lead page's normal mount scroll hiding it.
  - Interactions: hover **✓ Done / ⊘ Skip / ↩ put back** (fixed-size absolute
    buttons so they cannot overflow a narrow desktop card), drag across columns,
    and drag to reorder within one. Realtime `crm_today` keeps boards in step.
  - **Resolving a card asks what happened** (`Modals/TodayOutcomeModal.vue`).
    Ticking ✓ or dragging into **Done** opens a picker — Connected / No Answer /
    Left a Voicemail / **Sent a Text** / Booked an Appointment / **Other** — where
    Other requires a sentence. **Skipped** is the same shape (Lance, 2026-08-31):
    Dead lead / Lost / Already scheduled / Already contacted / Check with Dennis /
    Follow up later / Not selling / **Other**, taken from 60 days of free-text
    skip notes (462 skips; 124 had no reason). Other still requires a sentence.
    Stored on `CRM Today Item.outcome` /
    `outcome_note` (ops `setup_today_board.py`, both `has_field`-guarded via
    `_supports_outcome()` so the app is safe to deploy first) and rendered back
    onto the card, so a wrong answer is visible and fixable rather than
    write-only. Done and Skip actions remain available on their own columns as
    **Edit outcome / Edit skip reason**, so correction uses the same-state API
    instead of put-back/re-resolve. Re-submitting the same state only rewrites the outcome and
    deliberately does **not** restamp `resolved_at` — the intraday pulse reads
    that column as "resolved in this half hour", so correcting a mis-click must
    not move a card into a later window. **Put back is never interrogated**:
    undoing a mis-click isn't a judgement, and a prompt there would make the
    mistake cost more than the action.
    - **The drag path had to move from `@end` to `@change`.** `end` fires on the
      list the drag STARTED in, which is the wrong column to ask about; `change`
      names the destination and hands back the moved card, which is what decides
      whether to open the modal before anything is written. Cancelling a dragged
      drop must `board.reload()` — vuedraggable has already moved the card, so
      abandoning the answer otherwise leaves the board lying about where it is.
    - **GOTCHA — `reorder_today` never stamped `resolved_*`.** Only
      `set_today_state` did, so a card *dragged* into Skipped was invisible to the
      intraday pulse and a rep who works by dragging read as idle. Both paths now
      share `_state_stamps(state)`, and `reorder_today` also clears the outcome
      when a card is dragged back to To Call.
  - **Follow-up strip on every To Call card without an open task** (gw450,
    Lance picked mockup B of three): a dashed row `⏰ Follow up · 2h · 3d ·
    1wk · 1mo · 📅 · + other`. One tap on a chip creates a **"Follow up"**
    `CRM Task` at that time (same 9am-CT snap as the lead-page composer);
    📅 or `+ other` swaps the strip for an inline title box + the same chips
    + a DateTimePicker (blank title still means "Follow up"). Chips are the
    rep's own `crm_task_due_presets`, now read through
    `composables/taskDuePresets.js` so the to-do composer and the board
    cannot drift. Insert is the plain `frappe.client.insert` the to-do block
    uses (assigned to the CREATOR, like the composer), so `crm_task_update`
    fires for every other surface; both call cards of the lead are patched
    optimistically and the board reloads for the canonical row. Hidden on
    Done/Skipped, in Reassign mode, and once a task exists (the task row takes
    its place). `data-no-drag` + Sortable `filter` so typing in the box
    cannot start a drag. Words hide under a **container query** at ≤270px so
    the strip stays one line in a three-column board at 1200px — the
    control is the chips, the captions are titles.
  - **The card's task row is two hit targets.** The circle ticks the task
    complete **and back** without leaving the board (`frappe.client.set_value`,
    optimistic, both directions); the rest of the row opens the lead's **to-do
    list** (`TodayLeadModal` → `Activities` → `TaskTodoList`), where this task can
    be edited *and more can be added* — Lance's explicit call over a single-task
    `TaskModal`, which can only ever edit the one you clicked. Today.vue no
    longer mounts `TaskModal` at all; the lead panel's own `AllModals` provides
    it when a to-do row is clicked. The title uses a native `title` attribute
    rather than `<Tooltip>`: Tooltip renders a wrapper element, which would
    become the flex child and break the truncating `min-w-0 flex-1` title on a
    narrow card. Reopening matters as much as completing — the circle is a pixel
    from the row that opens the task. This is also why `get_today_board` now
    falls back to **a task completed today when there is no open one**
    (`open_task or completed_task`): otherwise ticking the box made the row
    vanish and left no way to undo. A Done card still prefers the completed task,
    exactly as before.
  - **`reorder_today` renumbers the WHOLE destination column**, not just the
    dragged names — cards are seeded at wide priority offsets, so writing
    10/20/30 onto three dragged cards once dropped
    them *behind* untouched neighbours still sitting at 3, 4, 5. Any name not
    passed keeps its relative position and is renumbered after the ones that
    were, so even a partial list can't corrupt the order.
  - **GOTCHA**: `columns` is synced from the resource with a **watcher**, not
    `board.onSuccess = ...`. The resource is `auto: true` and can resolve BEFORE
    a post-hoc onSuccess assignment lands — which rendered "All clear" over 66
    real cards. Caught only in live verification; the API was fine the whole time.
  - Ops: `scripts/setup_today_board.py` (idempotent, `--dry-run`).
  - **Not yet checked on a phone** — fixed-width columns + horizontal scroll;
    desktop is the primary surface unless Ger confirms otherwise.
- **Intraday Today pulse (every 30 min, Acq channel)** — the half-hourly
  heartbeat between the 5am standup and end of day, so pace is visible while the
  day can still be changed. Posts to the **Acq channel** (not a DM) so the whole
  acquisitions team works off the same number, via the same `pi` bot and token as
  the standup. `crm/api/today_pulse.py` (**new**).
  - **No "N cards behind pace" verdict** — an elapsed-vs-resolved over/under
    line was built and then removed the same day. The board routinely carries
    more cards than a day can hold (81-111 generated against ~87 resolved on a
    good day), so it read "behind" almost every day. That is a statement about
    board size, not about the person working it, and a warning that fires daily
    stops being read. Board overload belongs in the standup's intake-capacity
    number, not in a half-hourly nudge.
  - Carries: cards resolved **since the last pulse**, the day's rolling total as a
    Done/Skipped/left progress bar, pace vs. the hours left, and **Quo talk time**.
  - **Team-wide by default** (2026-09-08): removed the Ger-only default roster
    that silently excluded Exe/Dennis's completions and calls. Empty/unset
    `today_pulse_users` now means everyone; an explicit site-config roster still
    restricts the delta/calls (board totals remain team-wide). Check that override
    on deployment. Regression tests: `unit_test_today_pulse.py`, including Ger off.
  - **Talk time is a first-class metric, not decoration.** Cards-per-half-hour
    alone punishes the behaviour we want: a setter in a 20-minute conversation
    with a motivated seller resolves fewer cards than one dialing voicemails, and
    a bare "+0" reads as a rebuke for doing the job right. A window with no cards
    but real talk time is rendered as *"No cards closed — but 19m on the phone,
    longest 15m. Deep in a conversation."*
  - **Skips are timestamped now** (`resolved_at`/`resolved_by`, stamped for Done
    AND Skipped; `done_at`/`done_by` stay Done-only so the Today report, activity
    pulse and card UI keep their exact meaning). ~30% of a day's cards are
    resolved by skipping, so a Done-only delta reported a working setter as idle.
    Falls back to `done_at` and says so in the message if the ops script hasn't run.
  - **The delta window is a watermark, not a fixed 30 minutes** — it runs from the
    last *successfully posted* pulse to now, so a failed or skipped slot folds its
    cards into the next message instead of dropping them. Verified by replaying a
    real day: the deltas sum to exactly the day's resolved total (87 = 87).
  - **The observed rate is measured from the first resolved card, not from 9:30.**
    The setters routinely start an hour or more after the window opens; charging
    them for that time made the pulse open every day with a false "behind" warning
    built out of hours nobody was working. A rate is withheld entirely until
    `MIN_RATE_HOURS` of actual work has elapsed.
  - **Late in the day the required rate is replaced by a projection.** "need
    ~62/hr" with 30 minutes left is arithmetically true and useless; the message
    instead says what the current pace actually lands ("at ~12/hr that's about 6
    more, ~25 carrying over"). On the 8/04 replay that projection was accurate to
    within one card.
  - **The bar is two-tone (`█` resolved / `░` still to call), not three.** It first
    shaded Done and Skipped separately; Lance read the middle shade as "in progress"
    on the very first preview. A legend on the counts line fixed the ambiguity but
    not the cost — a nudge that has to be decoded every thirty minutes is not
    glanceable. The Done/Skipped texture lives in the counts line instead.
  - **`_progress_bar` never lies in either direction** — a non-zero segment never
    rounds away to nothing, and a board with work left always keeps at least one
    empty cell (naive rounding filled every cell with 1 card left of 21+, so the
    bar read "finished" while the board was not). Invariants checked exhaustively
    over every Done/Skipped/left split for totals 1–200.
  - Call attribution reuses `activity_progress`'s exact chain (`caller` →
    `receiver` → `User.custom_quo_number`), so the pulse and the Team Activity
    report cannot disagree about whose call it was. Verified 0 unattributed on prod.
  - No self-reply loop: `agent-listener` drops posts whose `user_id` is its own,
    and the pulse posts as that same `pi` user.
  - `preview_pulse(send=0, now=..., since=...)` is the dry run and never moves the
    watermark.
  - **Ops**: needs `scripts/setup_today_board.py` (adds `resolved_*`, idempotent)
    and `bench sync_jobs` on prod — a new scheduler hook does nothing until its
    Scheduled Job Type row exists. Cron `*/30 9-17 * * 1-5` is read in the SITE
    timezone (America/Chicago); the job itself enforces the real 9:30am–5:00pm
    window so the working hours live in one readable place.
- **Daily standup (5am CT Mattermost DM)** — the calling list is the Today board,
  not a DM. The 5am job still **generates** that board, then DMs Lance a
  **four-line recap of yesterday**: streak hit/missed, cards per person (done/skip),
  leftover owners, Done outcomes, every skip reason — plus a spend footer
  (`crm/api/spend_report.py`): BatchData wallet (the line that used to ride the
  calling-list DM) and ISTL money-balance vs yesterday's snapshot, read through
  LeadMarket `GET /api/istl-balance` (LeadMarket already stays logged into ISTL;
  CRM never holds that password). RealEstateAPI has no usage endpoint, so it is
  not in the footer. Cadence still lives in `crm/api/daily_standup.py` so the
  board and `get_standup_lead_names(bucket)` cannot drift. `preview_standup(send=0)`
  previews the recap (`today` = the morning the job runs) and does **not** move
  the ISTL snapshot. ISTL line needs `leadmarket_token` in site_config (Infisical
  `LEADMARKET_GMAIL_WEBHOOK_TOKEN` or `LEADMARKET_WEB_SECRET`). The old list
  renderer (`render_markdown`) is kept but not sent.
  - **Business days exclude US federal holidays** (2026-09-08, after Labor
    Day generated a 90-card board nobody was asked to work and broke the
    streak). `is_business_day` in `daily_standup.py` is the single gate —
    computed observed federal holidays (`us_federal_holidays`, no package)
    plus site_config `crm_holidays` (ISO date list) for company days off.
    `previous_business_day` / `business_days_between` route through it, so
    the standup recap, pulse, board generation, streak and cadence agree.
    Unit-tested in `crm/tests/unit_test_business_days.py`. The Labor Day
    cards were deleted (all 90 were untouched and every lead was on the
    Sep 8 board).
  - **Cadence = Dennis's**, posted in the Acq channel 2026-07-31: 2x/day for a
    week → weekly for 3 weeks → monthly. Two clarifications from Lance:
    "Call/Text" means call AND text but **only calls are metered** (texts are fast
    and don't compete for the same capacity), and **"1 week" = 5 BUSINESS days**
    — the call log is flat every weekend, so calendar-day counting burned ~2 days
    of a lead's best week and overstated cost (13 calls/lead in month 1, not 17).
  - **Suppression**: an open task with a FUTURE due date means the lead is booked
    → off today's list. Due-today/overdue puts it ON. Stops the report telling a
    rep to cold-dial a seller Dennis already scheduled.
  - **Roles, not owners** — `lead_owner`/`_assign` say Dennis owns ~99% of leads,
    but German + Exe do the calling while Dennis closes. Splitting by owner would
    hand the setters an empty list, so it emits one shared **calling queue** plus
    a **closer list** (Contract Sent → Make Offer → Underwriting, closest-to-
    closing first, flagged by DD date / task due / days silent).
  - **Ordering was the hard part; two cuts were wrong.** Ranking "has a due task"
    as its own top phase made the queue ~50 identical "needs 1 — Follow up" rows
    (~45 leads carry an auto-created task literally titled "Follow up") and buried
    all 17 never-called leads — the exact failure the report exists to fix. Phase
    now comes from the **cadence alone**; a task is a *reason*, not a rank. Second
    cut: a lead called yesterday still appeared under "Monthly sweep" because a
    generic task was overdue — leads due ONLY via a leftover task now sit in their
    own group at the bottom. Never-called sorts first (it's the actual leak).
  - Groups are individually capped so the list stays finishable.
  - **Excludes** parked import leads (`import_hidden`), converted, and
    `EXCLUDE_LEAD_NAMES` (the "Lance Test" record).
  - `preview_standup(send=0, note=...)` is the dry run (whitelisted/bench);
    `send_daily_standup` is the scheduler entry, wrapped so a delivery failure
    can't take down the cron slot, and it re-checks `is_business_day()` itself.
  - **Ops**: `bench set-config mattermost_token <pi bot token>` +
    `standup_dm_user lancejohnson` (token also at
    `~/.config/mattermost/pi-agent.env`; see `Projects/Groundwork/mattermost`).
    Absent a token `send_dm` no-ops rather than erroring.
  - **GOTCHA**: cron `0 5 * * 1-5` is read in the **SITE timezone**
    (America/Chicago), NOT UTC — writing it in UTC once turned an "8am" digest
    into 1pm. And a new scheduler hook does **nothing** until `sync_jobs` creates
    its Scheduled Job Type row on prod (gw127/128) — run it via `bench execute`,
    not `bench console`.
- **Dead leads stop generating work** — a lead moved to a dead status kept its
  open follow-up tasks forever, so they sat in the Activity to-do block and in
  every "due today" list. Measured on prod 2026-08-03: **25 open tasks on Dead
  Leads** (oldest due 2026-06-22) — a quarter of the whole due-or-overdue
  backlog was chasing leads nobody should call. Same disease that made the old
  ISTL digest useless ("the due list had 33 leads, but most were Dead Lead").
  `crm/api/task_hygiene.py` (**new**) + a `CRM Lead` `on_update` hook
  (`on_lead_update`) cancels open tasks (`Backlog`/`Todo`/`In Progress`) when a
  lead moves INTO a dead status.
  - **Keyed on `CRM Lead Status.type == "Lost"`, NOT on status names** — both
    "Dead Lead" and "Lost" are type Lost, so a rename or a new dead status
    ("Not Interested") keeps working. Hardcoding a guessed status list is
    exactly what broke the previous report. `Won` is deliberately excluded (a
    won deal can still carry real closing tasks) — add to `TERMINAL_TYPES` if
    that changes.
  - Gated on `has_value_changed("status")`, so re-saving an already-dead lead
    does NOT re-cancel a task a human deliberately reopened (verified).
  - **Canceled, never deleted** (reversible; timeline keeps the struck-through
    history), via `doc.save()` rather than `db.set_value` so `CRM Task.on_update`
    fires `crm_task_update` and the kanban badge / to-do block refresh live.
  - Hook body is wrapped in try/except — hygiene can never block a status change.
  - `backfill_terminal_tasks(dry_run=1)` is the bench-executable sweep; applied
    on prod (25 tasks / 25 leads, 0 errors, due backlog **91 → 66**, and every
    remaining due task now sits on a live workable status). No ops piece.
- **Call classification badge (editable)** — each call card in the Lead/Deal
  Activity timeline (and Buyer Conversation tab, same `CallArea.vue`) shows a
  color-themed badge with the call's classification (Connected / Voicemail
  Left / Greeting Hangup / Screener - No Contact / IVR-Robot / No Answer /
  Phantom / No Transcript), written by the `classify-crm-calls` skill
  (`~/.claude/skills/classify-crm-calls/` — deterministic pull → agent reads
  transcripts → guarded write-back) onto CRM Call Log custom fields
  (`custom_call_class` / `custom_call_class_source` rule|ai|human /
  `custom_call_note` / `custom_call_side`; created idempotently by the skill's
  pull script, NOT an ops setup script). The badge is a dropdown: picking a
  class calls `crm.api.call_class.set_call_class`, which stamps
  `source=human` — the classifier's write-back never overwrites human
  verdicts. Tooltip shows the classifier's evidence note. No backend read
  changes needed: `get_call_log` returns the full doc (`as_dict`), so the
  fields ride along like `custom_ai_summary`. GOTCHA: frappe-ui `Dropdown`
  uses reka-ui `DropdownMenuTrigger as-child` — the slot ROOT must be a plain
  element (a `<button>`); wrapping the slot in `<Tooltip>` swallows the
  trigger handlers, and `@click.stop` must live on a wrapper `<div>` (Dropdown
  doesn't inherit attrs, so the card's open-modal click fires otherwise).
  Mobile (gw253): wrapped badge rows use a tighter vertical gap, and transcript
  lines switch below `sm` from the cramped 48px speaker-name column to a
  two-column grid (time | speaker-above-content); long names no longer collide
  with dialogue, and hover-only line actions hide on touch widths. Verified in
  a genuine 390px Chrome popup: 9 transcript rows, zero bounding-box overlap.
  `crm/api/call_class.py` (**new**) + `frontend/src/components/Activities/CallArea.vue` +
  `CallTranscript.vue`.
- **Sequences run in named statuses, and a response PAUSES rather than stops**
  (2026-09-09, Lance's ask). `CRM Sequence.lead_statuses` (Small Text, one per
  line like `auto_enroll_sources`; ops `setup_sequence_statuses.py`, live on
  prod) — blank = any status, i.e. the old behaviour. The editor renders it as
  status chips ("Runs while the lead is in …", read via `createListResource`
  on `CRM Lead Status`, no endpoint). Enforcement is app code,
  `crm/api/sequence_status.py`: a `CRM Lead on_update` hook pauses every
  Active enrollment of a gated sequence the moment the lead moves out of the
  set, and `drain()` re-checks before each step (the net for `db.set_value`
  status writes and hand-enrolled leads already outside it). The ops
  `sequence_auto_enroll.py` skips a lead already outside the set. **Paused,
  not Stopped**, everywhere the lead itself acts: leaving the statuses, a
  text reply / answered inbound call (`sequence_events_webhook.py`), and a
  Connected call outcome (`call_outcome_sequence_exit.py`) all set Paused with
  an `auto-paused: <reason>` `last_log`, which the enrollment table shows
  under the badge; Do Not Call still Stops. Nothing auto-resumes on
  re-entering a status — Resume is a human click. Unit tests:
  `unit_test_sequence_status.py`. `Seller Outreach (Default)` was set to
  New + Called No Answer during verification (0 enrollments, no auto-enroll).
- **Contact exits the sequence; no next step gets poked daily** (2026-09-09,
  Lance). (a) A Today card resolved Done with outcome **Connected**
  (`SEQUENCE_EXIT_OUTCOMES` in `today_board.py`) pauses every Active
  enrollment of the lead (`sequence_status.pause_all`) — alongside the
  existing exits (text reply / answered inbound / Connected call outcome).
  (b) Phase **`nudge`**: a live lead that NO Active sequence is driving and
  that has no dated open task gets a card every day — reason "no next step ·
  N days since last contact" — until someone books a task after today.
  `_fetch_chase_rows` now carries `last_contact` (max of CRM Call Log and
  Quo Message either direction, scheduled/canceled placeholders excluded)
  and `in_sequence`; `last_contact_label` is the pure formatter, also on the
  closer reason. Order: task → closer → nudge. Measured at the switch: 55
  nudges (41 Follow Up; German 29 / Exe 19 / Dennis 7), oldest 29 days
  silent. Test lead "Test Lead For Agreement" added to `EXCLUDE_LEAD_NAMES`.
  **"Sent a Text" deliberately does NOT exit** (Lance, same day: "if we
  receive a text it should exit, but not if we send a text") — a day-1
  sequence text would otherwise end the sequence for everyone; a RECEIVED
  text already exits via the webhook.
- **The Today board's own call ladder is GONE** (2026-09-09, Lance: "the
  only cadence we want now are in the sequences"). `daily_standup._classify`
  now returns due only for (a) a task due today — `task`, which is how the
  New Lead 10-Day sequence hands work to a rep — and (b) `closer` (deal in
  flight, nothing booked). never-called / 2-a-day week 1 / weekly / monthly
  make NO cards; those phases survive only as labels on pre-existing cards
  (`PRIORITY_ORDER` is `("task", "closer")`, legacy keys sort after). The
  Priority modal lists just the two. Measured on prod at the switch: due
  **60 → 24** (18 task + 6 closer); **115 live leads with no task fell off
  the board** — leads that predate the sequence and are enrolled in nothing.
  Non-iSTL sources (PropertyLeads, Leadzolo) get no call tasks from any
  sequence and so never surface either. `PHASE1_*`/`CALLS_PER_LEAD_MONTH1`
  remain for the standup's intake-capacity arithmetic only.
- **Scheduled texts** (2026-09-09, Lance: weekend text tasks → "let the user
  schedule a text"). A clock button beside Send on `SendTextModal` and the
  Text-tab `SMSBox` opens `ScheduleTextPicker` (Tomorrow / Sat / Mon 9am
  chips + DateTimePicker, 9am Chicago snap shared with task due chips).
  `crm/api/scheduled_text.py`: `schedule_text` stores a **`Quo Message`**
  placeholder (`id` `sched-<hex>`, `status=scheduled`, `message_date` = send
  time, `sent_by` = rep) so it sits IN the thread at its future slot —
  dashed blue bubble, "Scheduled · Sat, Sep 12 9:00 am", Cancel (owner or
  manager; `cancel_scheduled_text` deletes + publishes `quo_message`). The
  Activity timeline labels it "Scheduled". `send_due` on the existing
  `* * * * *` cron (**new scheduler method → `sync_jobs` on prod**) POSTs
  OpenPhone, inserts the REAL row under the Quo id (same dedupe as
  `send-text` / the webhook) and deletes the placeholder; failure marks it
  `failed`. A lead that went Lost/Won first is NOT texted: `canceled`, shown
  as "Not sent — lead closed". Scheduling from the Today card does not
  finish the card. No new doctype, no ops script. Verified on prod via
  bench (schedule → thread shape → `send_due` on the dead Lance Test lead →
  canceled) and the UI on all three surfaces.
- **`New Lead 10-Day` sequence + drainer quiet hours** (2026-09-09, Lance's
  spec: one triple dial every other day for ten days, one text a day for
  ten days). Built DISABLED with no auto-enroll, gated New / Called No
  Answer. **Enabled 2026-09-09, auto-enrolls `iSpeedToLead`** (the only
  source actually being bought — 378 of 396 leads in the last 30 days); `New
  Lead Ring Alert` stays as it was for Red Panda / PropertyLeads. **Nothing
  in it sends automatically**
  (Lance: "get rid of the texts and just set them as tasks"). 15 steps, no
  Pushover, no intro burst: day 1 = a **Task** "Text {{ first_name }} — day
  1 of 10" at wait 0 + triple dial 1 (Call step); days 2–10 one Text-task
  each (1 Days waits) with the drafted message in the body for copy/paste;
  triple dials 2–5 at wait 0 after the day 3/5/7/9 tasks. **`Task` is a new
  step type** (ops `setup_sequence_task_step.py` adds the Select option;
  `crm_sequence_runner_core.py` branch: subject = title (Jinja, blank →
  "Follow up"), message = description, assigned to the lead owner, due now)
  — a manual step, unlike Text which sends. Verified live on the Lance Test
  lead and cleaned up. A Call step's body tells the rep to log the outcome
  (Connected pauses, Do Not Call stops). Cutover =
  enable it, move `auto_enroll_sources` off `New Lead Ring Alert`, disable
  that one (both enabled = two intro bursts). **Quiet hours + business days**
  live in `sequence_drain.py` (`quiet_hold_until` / `business_hold_until` /
  `hold_until`, all pure and unit-tested, applied both at fire time and to the
  stored `next_run` via `_align_next_run`): a Text/Call/Task step whose own
  wait is ≥ 1 hour waits for the next WORKING morning — pushed to 8am if it
  comes due outside 8am–8pm site time, and rolled forward off a weekend or a
  US federal holiday. The wait-0 / seconds intro burst is deliberately exempt:
  a lead that arrives Saturday night is still answered Saturday night.
  - **The business-day roll reuses `daily_standup.is_business_day`** — the same
    gate as the standup, board generation and the streak, `crm_holidays`
    included — so nothing in the CRM can disagree about what a working day is.
  - **It is computed from the step's own due date, never from `now`.** That is
    what makes it idempotent: `drain_due` re-aligns every Active enrollment
    once a minute, and a rule keyed on `now` would walk a held step one more
    day into the future on every pass. `business_hold_until` returns None when
    the date already lands on a business day, so re-applying it is a no-op.
  - **Nothing is skipped and no day is dropped** — the cadence stretches across
    the weekend, which is what "1 week = 5 business days" has meant here since
    the standup was written. A wait-0 Call step riding behind a rolled Task
    needs no rule of its own; it follows whatever the Task did.
  - **Why (gw…, Exe 2026-09-14, #bugs):** waits were CALENDAR days, so a lead
    enrolled Wednesday had "day 4 of 10" land on Saturday and "day 5" on
    Sunday, and the rep opened Monday holding three overdue to-dos on one
    lead. Measured on prod that morning: **133 of the 224 open cadence tasks
    were due on a weekend**, 65 of 74 leads carried three or more, and
    Saturday+Sunday held 175 of the 29% of all cadence tasks that ever landed
    on a day nobody works. It was not reps ignoring the list — 326 of the 564
    cadence tasks were Done, and the lead in the screenshot had ticked day 4
    ON the Saturday.
  - **The backlog it left is a SEPARATE, opt-in repair**:
    `task_hygiene.collapse_sequence_pileup(dry_run=1)` cancels superseded
    `— day N of 10` tasks, keeping each lead's latest day (dry run on prod:
    150 tasks across 67 leads, open tasks 444 → 294, every lead down to at
    most one). Deliberately **not** a scheduled job: with the drainer fixed
    the pile-up stops, and a rep who is genuinely one day behind should still
    see yesterday's task. `superseded_tasks` / `cadence_day` are pure and
    unit-tested; the title regex is the only marker a sequence task carries,
    so a human-typed "Follow up" can never be selected.
- **Deals in flight surface on the Today board DAILY** (same day): a lead in
  `CLOSER_STATUSES` (Underwriting / Make Offer / Contract Sent) with no task
  due after today is phase **`closer`** in `daily_standup._classify` — due
  every day, one card, reason "Make Offer · no follow-up scheduled" (or the
  due task's title). Booking any future task clears it. **Signed Contract and
  later (`POST_CONTRACT_STATUSES`, same set as dispo) mint no Today cards** —
  not closer, not task, not nudge. Checked BEFORE the
  call ladder: by Underwriting the lead has been reached, so "never called"
  says nothing. `PRIORITY_ORDER` gained `closer` after `task`; a user's saved
  order gets it appended last (drag it up once). Today.vue badge "Deal in
  flight" (purple via class override — frappe-ui Badge has no purple theme).
  `CRM Today Item.phase` is a Data column, so no ops piece. Probed on prod
  before deploy: exactly the 6 in-flight deals (5 with an overdue "Follow
  up" task, 1 with none).
- `frontend/src/pages/Sequences.vue`, `Sequence.vue` — native sequences list +
  step editor + enrollments management
- `frontend/src/router.js` — `/sequences`, `/sequences/:sequenceId` routes
- `frontend/src/components/Layouts/AppSidebar.vue` — Sequences nav item
- `frontend/src/components/SidebarLink.vue` — absolute-URL support
- `frontend/src/components/Modals/TaskModal.vue` — "Call Outcome" disposition
  dropdown (Connected / Left Voicemail / No Answer / Wrong Number / Do Not Call)
- `crm/api/activities.py` — `call_outcome` in task field lists
- `frontend/src/components/Activities/CommentArea.vue` + `Activities.vue` +
  `crm/api/comment.py` — edit your own comments inline on the Lead/Deal activity
  feed: a hover-only pencil (Comments tab + the unified Activity timeline) opens an
  inline rich-text editor with Save/Cancel. Owner-only `edit_comment` API
  (PermissionError guard; suppresses mention re-notify on edit); `Activities.vue`
  passes the activities resource via `v-model` so the feed reloads after a save.
- **@-mention emails** — `@`-mentioning a teammate in a Lead/Deal comment already
  created an in-app `CRM Notification`; now it ALSO emails them. `crm/api/comment.py`
  `notify_mentions()` calls a new `email_mention()` per mention: `frappe.sendmail`
  with the `crm_mention` template (comment HTML + a "View comment" button) linking
  to `/crm/{leads|deals}/<reference_name>#<comment_name>` — the same route+hash the
  bell notification uses, so it lands on the record and scrolls to the comment.
  Self-mentions are skipped (mirrors `notify_user`); a send failure is caught/logged
  so it can never roll back the comment; owner full_name + record label are computed
  once before the mention loop. Pure app code, no ops piece (reuses existing SMTP).
  `crm/api/comment.py` + `crm/templates/emails/crm_mention.html` (**new**, mirrors
  the `crm_invitation` template).
- **Quick comments (customizable canned comments)** — a "Quick comment" block in
  the Lead/Deal Activity feed (directly below the To-do block, same card style):
  a row of one-tap chips that each post a canned comment to the timeline. Each
  user customizes their own list via an inline pencil editor (add/edit/remove
  rows + Save). Seeded defaults for any user who hasn't customized: "Call 3x's,
  voicemail, sent text" / "Called" / "Voicemail" / "Sent text". Persisted
  cross-device on a `User.custom_quick_comments` JSON field (mirrors the
  `custom_quo_number` per-user-setting pattern). Requested by Dennis.
  - `frontend/src/components/Activities/QuickComments.vue` — **new** (chips +
    inline editor; reads the session user's list from the users store, falls back
    to defaults when unset)
  - `frontend/src/components/Activities/AllModals.vue` — `addComment(content)`
    helper (posts via `crm.api.comment.add_comment`, wraps plain chip text in a
    `<div>`, reloads the feed) + exposed
  - `frontend/src/components/Activities/Activities.vue` — renders `<QuickComments>`
    after `<TaskTodoList>`; widened the Activity-tab `v-else-if` to `title ==
    'Activity'` so the To-do + Quick-comment blocks always show (even on an empty
    lead) instead of the EmptyState
  - `crm/api/session.py` — `custom_quick_comments` added to `get_users` fields
  - `crm/api/comment.py` — `set_user_quick_comments(comments)` (session user's
    own list only; stores a cleaned JSON array, capped at 30)
  - Ops (`../frappe-crm-deploy`): `scripts/setup_quick_comments.py` adds the
    `User.custom_quick_comments` Long Text custom field (no seeding — defaults
    live in the frontend)
- **Inline lead-name editing (sidebar header)** — hovering the lead's name in
  the Lead page sidebar shows a pencil; clicking swaps it for separate
  First/Last name inputs (Tab moves first → last, Enter or clicking away
  saves, Esc cancels). Saves `first_name`/`last_name`; the server's
  `validate()` rebuilds `lead_name`, mirrored optimistically so the header
  never flashes stale. First name required (matches the doctype). Pure
  frontend. `frontend/src/pages/Lead.vue`.
- `frontend/src/components/Kanban/KanbanCardField.vue` + `pages/Leads.vue`
  `#fields` slot — hover-only card affordances on the Leads Kanban: copy icon
  for phone/address fields, pencil-to-edit (inline popover, `frappe.client.set_value`
  + board reload) for any non-read-only field. Nothing shows until row hover.
- **Global Kanban card settings (one editor = default for everyone)** — when
  `lance.johnson@groundworkpro.com` (the `GLOBAL_KANBAN_EDITOR` constant) edits
  the default Kanban card layout, the standard Kanban view is saved globally
  (`user=""`, which `get_views` serves to every user) and overrides each user's
  personal Kanban view. (Originally keyed off `Administrator`, but Lance runs
  the CRM as his own user, so his edits never went global — switched to his
  email.) Everyone else / other view types are unchanged (still per-user).
  Applies to any Kanban (Leads + Deals).
  `crm/fcrm/doctype/crm_view_settings/crm_view_settings.py`
  (`create_or_update_standard_view`: editor+kanban → `user=""`, promotes any
  pre-existing personal row instead of duplicating) +
  `frontend/src/stores/views.js` (a global/user-less standard view always wins
  over a personal one when resolving `standardViews`).
- **SMS / texting** (Quo/OpenPhone): native two-way texts on the `Quo Message`
  doctype.
  - `frontend/src/pages/TextMessages.vue` + `/texts` route + sidebar item — a
    compose-first inbox (conversations · thread · compose)
  - `frontend/src/components/Activities/SMSArea.vue`, `SMSBox.vue` — per-lead
    Text Messages tab (thread + compose); also folded into the unified Activity
    timeline (`Activities.vue`)
  - `SendTextModal.vue` + `AllModals.vue` + `ActivityHeader.vue` — "Send Text"
    quick action next to "Log a Call"
  - per-user sending number on `User.custom_quo_number`: when a user with no
    linked number opens a texting surface (SMS tab / `SendTextModal` / the
    `/texts` inbox thread), `Modals/SelectQuoNumberModal.vue` auto-pops with
    the Quo workspace lines (number + line name) — picking one saves to their
    profile via `set_user_quo_number` and unblocks Send (a "Select number"
    banner reopens it if dismissed). Replaced the old inline `QuoFromSelect`
    "Send from" row in the compose surfaces (gw171; the component file remains,
    now unused). Admin assignment still in `Settings/Users.vue`;
    `composables/quoSender.js` has `quoNumbers`/`myQuoNumber`/`formatPhone`
  - `crm/api/sms.py` — read API (`get_sms_messages`, `get_sms_conversations`,
    `is_sms_enabled`) + `set_user_quo_number`; `crm/api/session.py` exposes
    `custom_quo_number`; `crm/hooks.py` — `Quo Message` after_insert publishes
    the `quo_message` realtime event (the server scripts can't, sandbox)
  - **MMS media (photos/videos)** — inbound texts can carry image/video
    attachments. They arrive ONLY in the Quo `message.received` webhook payload
    as `media:[{url,type}]` (the REST API omits media → already-received MMS are
    unrecoverable; capture is going-forward only). Stored as a JSON array on the
    new `Quo Message.media` Long Text field (added by ops
    `setup_quo_message_doctype.py`; populated by the `sequence-events` webhook).
    `sms.py` parses it (`_media`) and returns `media:[{url,type}]` per message;
    the inbox preview shows "📷 Attachment" for an image-only (blank-text) text.
    Rendered by `components/Activities/SMSMedia.vue` (images inline, videos with
    a player, other types as download links) in both `SMSArea.vue` and the
    unified timeline block in `Activities.vue`. Clicking an image opens
    `components/Activities/ImageLightbox.vue` — a teleported full-screen viewer
    that pages through that message's images (←/→ buttons + arrow keys, wraps,
    counter; Esc / ✕ / backdrop close). The OpenPhone API CANNOT send MMS (send
    is text-only) — test inbound media by POSTing a synthetic `message.received`
    to `/api/method/sequence-events` (see ops repo).
- **Call transcript + waveform sync** — a "conversation score" view for recorded
  calls: a dual-lane diarized waveform (rep above the axis in blue, lead below in
  amber, shared playhead), a click-anywhere-to-seek scrubber, a synced transcript
  that auto-scrolls and highlights the active line (click a line → seek), a
  talk-time balance bar (rep% vs lead%), and optional Gemini chapter ticks. No new
  npm dependency: native `<audio>` for playback + Web Audio API `decodeAudioData`
  for peaks + `<canvas>` for the waveform (single fetch reused for both decode and
  a blob-URL `<audio>` source). Speaker colors live in JS (canvas can't read
  Tailwind tokens) and are blue/amber for dichromat safety.
  - `frontend/src/components/Activities/CallTranscript.vue` — the component
  - `crm/api/call_transcript.py` — `get_call_transcript(call_log)`: normalizes
    OpenPhone's diarized segments to clean `rep`/`lead` speakers (trusts a
    pre-normalized `speaker`, else OpenPhone's `userId`, else a last-10-digit
    identifier match), computes the talk-time ratio, and returns stored chapters
  - hosts: `components/Activities/CallArea.vue` (a "Transcript" toggle badge in
    the Lead/Deal activity timeline, next to "Listen") and `pages/CallReview.vue`
    (a per-call "Transcript" expander)
  - **Transcript source = OpenPhone native only.** The diarized, timestamped
    transcript and Gemini chapters are captured server-side into two custom Long
    Text fields the OPS REPO must add to CRM Call Log:
    `custom_transcript` = `{"dialogue":[{speaker|userId|identifier,start,end,content}],"duration":n}`
    and `custom_transcript_chapters` = `{"chapters":[{title,start,end}],"highlights":[{quote,t}]}`.
    Neither field needs to exist for the app code to run — the endpoint degrades to
    "transcript still processing". Ops work (in `../frappe-crm-deploy`): add the two
    custom fields; subscribe the `call.transcript.completed` webhook
    (`/v1/webhooks/call-transcripts`, Business/Scale plan) in `setup_quo_webhooks.py`;
    capture it in `sequence_events_webhook.py` (fetch `/v1/call-transcripts/:callId`,
    store `dialogue`); generate chapters via Gemini Flash over the transcript text
    (key in Infisical); backfill historical calls.
- **Call transcript deep-link to a timestamp** — share a link to a specific
  moment in a recorded call's transcript + audio, landing in the lead's Activity
  timeline. Link shape `/crm/leads/<leadId>?call=<callLogName>&t=<seconds>#activity`
  (Deals supported too via `linkTarget`); opening it switches to the Activity tab,
  auto-expands that call's `<CallTranscript>`, seeks the audio to `t`, and scrolls
  the card into view. Grab a link two ways: hover any transcript line → a 🔗 at the
  line's end copies a link to THAT line's start; or the 🔗 in the player bar copies
  a link to the current playhead (both via `copyToClipboard`). Pure frontend, no
  schema/server piece.
  - `components/Activities/CallTranscript.vue` — new `seekTo` + `linkTarget` props;
    `applyPendingSeek()` defers the seek until the audio has buffered far enough to
    land there (`preload='auto'` + retries on `progress`/`canplay`/`canplaythrough`)
    — seeking at `loadedmetadata` clamps to the buffered edge (apiDuration is often
    null for these); the two 🔗 copy buttons.
  - `components/Activities/CallArea.vue` — self-activates when
    `route.query.call === call.name` (expand + a scroll-retry loop that re-runs
    `scrollIntoView` until the card parks near the top, since the feed re-lays-out
    after mount); passes `:seek-to` down.
  - `pages/CallReview.vue` — passes `:link-target="{type:'leads',id:reference_name}"`
    so its copy-link points at the lead timeline.
- **Timestamped call comments + "Playback" consolidation** — reviewers annotate a
  specific moment in a recorded call ("ask a better question here") and the comment
  is pinned (a violet author-initial dot) on a time-aligned ruler under the
  waveform; a Comments list under the transcript shows each with a clickable
  time-badge (→ seek), and owners get hover edit/delete. Collaborative: every sales
  user sees all comments on a call; edit/delete is owner-only (managers may delete).
  Also **decluttered the call UI**: removed the separate **Listen** inline player
  (activity timeline) and the standalone `<audio>` player (Call Review) — hitting
  one control opens audio+waveform+transcript+comments — and **renamed that control
  "Transcript" → "Playback"** (the "Recording" raw-file link stays).
  - **Storage = new ops doctype `CRM Call Comment`** (mirrors `CRM Call Review`):
    `call_log` / `at_time` (Float secs) / `author` / `content`, autoname hash,
    Sales-roles perms. Created by `../frappe-crm-deploy/scripts/setup_crm_call_comment.py`
    (idempotent REST, `--dry-run`). No CRM Call Log custom field.
  - `crm/api/call_comments.py` — `get_call_comments` / `add_call_comment` (author =
    session user) / `edit_call_comment` (owner-only) / `delete_call_comment`
    (owner or manager); each mutation publishes `crm_call_comment` realtime
    (site-wide, `after_commit`); all guarded by `db.exists` so a pre-provision site
    returns `[]`. Reuses the `reports.py` sales-role gate.
  - `components/Activities/CallTranscript.vue` — comments resource + 💬 transport
    button → inline composer at the current playhead, plus a per-transcript-line
    hover 💬 next to the 🔗 (`startCommentAt(line.start)` → composer at that line's
    moment); waveform pins (`posOf(at_time)`, `selectComment` → seek + highlight),
    comments list with inline edit, `crm_call_comment` `$socket` listener (reload
    when `call_log` matches). Add/edit/delete via frappe-ui `call()`.
  - `components/Activities/CallArea.vue` — dropped the Listen badge + `AudioPlayer`;
    Transcript badge → **Playback** (PlayIcon). `pages/CallReview.vue` — dropped the
    `<audio>` element; toggle label → **Playback** (keeps the "No recording" span).
- **AI "Integrity Report" call-review bot** — a daily scheduler job that reviews
  every recorded call from the prior day with Gemini (2.5 Flash) and emails Lance
  one digest. Scores how well the rep got to the seller's **motivation** (0-5, or
  null on follow-ups/voicemail) and flags **integrity** issues — anything not fully
  honest or "salesy" instead of plainly clear (good: *"we're going to buy it and
  resell it to a builder or homeowner as quickly as we can — we actually work on
  getting it pre-sold right away"*; bad: *"we have multiple exit strategies like buy
  and hold, fix and flip, or wholesale"*). Each issue = verbatim quote + why +
  better phrasing. Also notes where the lead is at + what to do differently. First
  LLM call in the app code (raw `requests` to the Gemini API, key in site_config
  `gemini_api_key` — mirrored from the existing Infisical `GEMINI_API_KEY`, the same
  key the ops chapter-gen uses; JSON response schema; model config-swappable via
  `call_review_model`, e.g. `gemini-2.5-pro`). Per-call result stored in a new ops
  doctype `CRM Call AI Review`, surfaced in the Lead/Deal-adjacent **Call Review**
  tab — which is **open to the whole sales team** (gw303; it was Lance-only until
  then, which wasted it — the reps learn most from hearing their own calls back).
  - `crm/api/call_review_ai.py` — **new**: `run_daily_integrity_report` (daily_long),
    `review_call_now` (whitelisted, Lance/System-Manager, on-demand + testing),
    `_review_one`/`_build_llm_input`/`_lead_context`/`_call_claude`/`_persist_review`/
    `_send_digest`. Scope = transcript + duration ≥ 60s; idempotent (one
    `CRM Call AI Review` per call); loads lead context (status/tasks/comments/prior
    AI summaries) into the prompt.
  - `crm/api/call_transcript.py` — extracted pure `_build_transcript(doc)` helper
    (endpoint is now a thin wrapper) so the bot reuses clean rep/lead dialogue.
  - `crm/templates/emails/crm_call_review_report.html` — **new** digest template.
  - `crm/hooks.py` — `run_daily_integrity_report` added to `daily_long`.
  - `crm/api/reports.py` — `validate_access` is the **sales-role gate**
    (`ALLOWED_REPORT_ROLES`) again as of gw303; `get_call_review` attaches
    `ai_review` per call from `CRM Call AI Review` (guarded by `db.exists`).
    **Writing back to the AI is still reviewer-only**: `review_call_now` and
    `reply_to_review` (`crm/api/call_review_ai.py`, which keeps its own
    `CALL_REVIEW_USER`) re-run Gemini and teach GLOBAL house rules that reshape
    every future review — a different power from reading one.
  - `frontend/src/utils/sidebarLinks.js` (the Call Review `condition` and the
    `CALL_REVIEW_USER` export are gone), `router.js` (route guard removed),
    `pages/CallReview.vue` (per-call AI panel: flag badge → expand → motivation,
    integrity issues, coaching).
  - Ops (`../frappe-crm-deploy`): `scripts/setup_call_ai_review.py` creates the
    `CRM Call AI Review` doctype (autoname by `call_log`; fields motivation_score/
    motivation_reason/integrity_issues JSON/overall_flag/lead_status/
    what_could_be_better/model/reviewed_at/reference_*); `bench set-config
    gemini_api_key <key>` on prod, value pulled from the existing Infisical
    `GEMINI_API_KEY` (same pattern as `documenso_api_token`).
  - **2026-07-10 (gw127/gw128)**: (a) the daily job had NEVER fired — the
    `daily_long` hook needs a **Scheduled Job Type row**, and `sync_jobs` never
    ran after the gw118 deploy (the seqdrain gotcha again). Fixed on prod via
    `bench execute frappe.core.…scheduled_job_type.sync_jobs` (running it from
    `bench console` did NOT persist the row; use bench execute). Any future
    scheduler-hook addition needs this. (b) Digest is now **exhaustive** — clean
    calls get full detail rows (green border) after the flagged ones, not just a
    count. (c) System prompt now enforces Lance's transparency talking points
    (from the Jun 27 email to Dennis): approved exit-strategy script, banned
    phrases ("OUR builders/partners", the three-exit-strategies menu,
    "contractors/partners" for buyers), profit + multiple-visits disclosures
    (missing-disclosure flags only on discovery/offer calls), and **novation
    guardrails** (seller must understand: we just list it with an agent — they
    could too; access for showings; they'll net LESS than listing themselves
    even after agent fees; our value = simplicity/negotiations).
  - **gw129/gw130: unknown-lead calls in the digest** — a call with no linked
    lead now shows the other party's phone number ("Unknown (+1555…)") and
    deep-links to the **Call Review page** (`/crm/reports/calls?date=&call=&t=`)
    instead of nothing. `pages/CallReview.vue` honors those query params: sets
    the date, auto-opens that call's Playback + AI panel, seeks to `t`, scrolls
    the card into view (scroll-retry loop). NOTE the route path is
    `/reports/calls` (route name "Call Review") — there is no `/call-review`
    path; gw129 shipped the wrong URL and gw130 fixed it. Verified live.
- **iSpeedToLead refund-eligibility watch — REMOVED 2026-07-28** (gw226, at
  Lance's request). Was a twice-daily digest (8am/3pm Mon-Fri) of ISTL leads at
  risk of losing the 5-double-dials-in-10-days refund, plus a matching amber/
  red/green kanban card tint. Deleted wholesale: `crm/api/istl_refund_report.py`,
  `crm/templates/emails/crm_istl_refund_report.html`, the two `cron` hooks, and
  the `_istl_card_colors` branch in `crm/api/doc.py` (`_new_lead_color` is back
  to the plain untouched-new-lead age tint from gw104, and `dueTint`/
  `TINT_URGENCY` lost their now-unreachable `green`). The two Scheduled Job Type
  rows were deleted on prod. Recoverable from git history if it's ever wanted
  back.
- `frontend/vite.config.js` — PWA service worker set `selfDestroying` (the
  precache served stale app bundles after deploys)
- **Sequence real-time drainer** — `crm/api/sequence_drain.py` + `crm/hooks.py`
  (CRM Lead doc-event `enqueue_for_lead` + 1-min `drain_due` scheduler). Makes
  sub-minute sequence-step waits actually honor seconds (the sandboxed engine
  can't `time.sleep`/delay-enqueue, so the old cron rounded every wait up to
  ~60s). Single-driver model on a dedicated `seqdrain` worker; the old
  "CRM Sequence Runner" cron is disabled. Full design + the two deploy gotchas
  (register the `seqdrain` queue in common_site_config; `migrate`/`sync_jobs` to
  register `drain_due`) live in `../frappe-crm-deploy/CLAUDE.md` → Sequences.
  - **GOTCHA (gw493) — the drainer inherited the WEBHOOK's Guest session.**
    `frappe.enqueue` captures `session.user`; from an inbound lead that is
    Guest, and `CRM Task.after_insert → assign_to → check_permission` rejects
    Guest AFTER the task row is inserted, so every failed attempt left an
    orphan task (Richard Vega, 2026-09-09: three "Text — day 1" to-dos in one
    minute). `drain()` now `set_user("Administrator")` first, and the core
    script defers Call/Task steps when it is run in-request as Guest (the
    auto-enroll wait-0 path) — the drainer fires them seconds later. `drain()`
    also takes a MySQL `GET_LOCK('seqdrain:<enr>')`: the `job_id` dedupe is
    check-then-enqueue and two ticks slipped through it, both ran step 1, and
    the loser's task insert had committed before its enrollment save lost on
    TimestampMismatch (one duplicate in the 80-lead bulk enroll).
  - **Fail-safe (gw173)**: the engine catches step exceptions internally and
    leaves the enrollment Active-and-due, so a persistently failing send used to
    retry forever — when Quo ran out of prepaid credits (Jul 8–15 2026) that
    meant 275k Error Log rows and the stale backlog blasting out the moment
    credits were topped up. `drain()` now snapshots
    (current_step, next_run, modified) around `_run_core`; a due run that
    advances nothing counts as a failure: the job returns (retries once per
    drain_due tick, not 50×/job) and `fail_count` (Int on the enrollment, added
    by ops `setup_sequence_failsafe.py`) increments; at
    `MAX_CONSECUTIVE_FAILURES` (10 ≈ 10 min) the enrollment is set **Paused** +
    `FAILSAFE_NOTIFY` (Lance) is emailed (resume = set Active on the sequence's
    Enrollments list). Progress resets the counter; disabled sequences and a
    missing `fail_count` column no-op (pre-provision safe). Quo billing itself:
    auto-recharge is now ON (2026-07-15).
- **Lead/Deal tasks as a Trello-style to-do list** — the existing `CRM Task`
  feature (its own Tasks tab + heavyweight `TaskModal`) is now surfaced in the
  **unified Activity timeline**: a pinned **"To-do"** block at the top of the
  Activity feed lists every open task with an explicit **What + When + Add**
  flow: title first, then No date / 2h / 3d / 1wk / 1mo / calendar, then Add in
  both visual AND keyboard order; nothing is created until Add/Enter. A due chip
  SELECTS the date (and exposes `aria-pressed`) when a title is typed; with a
  BLANK title a chip creates a “Follow up” task at that time in one tap
  (Lance, 2026-09-03 — reversed the earlier select-only rule, since "bump this
  lead" is the common case). The calendar button still only selects. Inserts default to the current
  user + `Todo`. The full Task modal opens as a compact **Schedule a Task** form
  (title + when, advanced fields behind More options on create AND edit), a
  touch-sized hover circle → click-to-complete checkbox, and a trash action that
  is always visible on touch but quiet-until-hover on desktop; tasks sort by due
  date (overdue first), the relative due date is **red**
  once overdue / **amber** when due today. Completed/canceled tasks drop into the
  chronological history anchored at their completion date (`modified`),
  struck-through. Open tasks live only in the To-do block, completed only in
  history — no duplication. Creating/saving a task **stays on the Activity tab**
  (removed `TaskModal`'s `@after="redirect('tasks')"`).
  - `frontend/src/components/Activities/TaskTodoList.vue` — checklist (no nested
    gray card): click a title to edit the task + schedule in `TaskModal`; hover-
    check completes it. This replaces the hidden split where title-click renamed
    but a hover-only panel icon edited the schedule (unreachable on touch). Due
    chips are per-user (`crm.api.task_presets`, Frappe user
    default `crm_task_due_presets`, no ops field) with a pencil editor like
    quick comments; unset falls back to 2h / 3d / 1wk / 1mo. **Day/week/month
    chips land at 9:00am America/Chicago**, not midnight and not now+N×24h;
    hour chips stay relative to now. A DateTimePicker midnight snap does the
    same 9am. Due labels read `Thu 9am` / `today 9am`, not `timeAgo`.
  - `TaskModal.vue` sends ONLY mutable task fields. Passing the whole fetched row
    included a stale `modified` timestamp after realtime reloads and caused
    `TimestampMismatchError` on ordinary edits. The awaited successful submit is
    also the authoritative close point (with an explicit model update), so modal
    closure does not depend on frappe-ui resource callback timing.
  - `frontend/src/components/Activities/Activities.vue` — `openTasks` computed +
    `get_task_activities()` (completed tasks → timeline entries keyed on
    `modified`), merged into the Activity feed; `task` branch in the timeline +
    `timelineIcon`; renders `<TaskTodoList>` and widened the feed's `v-if` so the
    quick-add shows on a lead with no other history
  - `frontend/src/components/Activities/AllModals.vue` — `addTask(title, due_date)`
    helper (centralized with the existing `updateTaskStatus`/`deleteTask`); no
    longer redirects to the Tasks tab after a task save
  - **Kanban next-task-due badge** (Leads **and** Deals) — each card shows the
    soonest open task's due date as **colored text** (red overdue / amber today /
    muted future). Server computes it as a pseudo-field `_next_task_due` in
    `crm/api/doc.py` `getCounts` (earliest `due_date` among non-Done/Canceled
    tasks; filtered out of the DB `rows` like `_last_comm`); added to the default
    `kanban_fields` in `crm_lead.py` + `crm_deal.py`; selectable via
    `KanbanSettings.vue`; rendered in the `#fields` slot of `pages/Leads.vue` +
    `pages/Deals.vue` (`parseRows` → `{label, value, color}`); shared `dueColor()`
    helper in `frontend/src/utils/index.js`. No schema change, no new npm dep.
  - **Kanban "Tasks due" filter** (Leads) — a dropdown (Due today / Overdue
    / Due today + overdue / Clear) filters the board to leads with a matching open
    task. `crm/api/doc.py` `get_docs_with_due_tasks(doctype, scope)` resolves the
    lead names server-side (since `_next_task_due` is computed, not a column);
    `pages/Leads.vue` injects them as the same never-persisted `name in [...]`
    default filter the dashboard drill uses. (Deals not wired yet — same backend
    would extend it.) **Lives in the view-controls row** next to the Filter
    button (not the page header): `ViewControls.vue` exposes a `#actions` slot
    just left of `<Filter>`, and `pages/Leads.vue` fills it with the Tasks-due
    `Dropdown`. To make room, `get_quick_filters` now also drops the `email` and
    `organization` quick filters for CRM Lead (alongside the existing `converted`
    strip), so the Leads search row is Full Name / Status / Source only.
- **Status Change Report** (on the `/dashboard` landing page) — a drill-downable
  table of how leads move between statuses, replacing the old status-changes bar
  chart. Two lenses via a Cohort/Flow toggle: **Cohort** (default) = the leads
  *created* in the range and where each went; **Flow** = every transition that
  *happened* in the range (Started/Ended are population snapshots at the window
  edges). Per-status columns Entered/Left/Started→Ended/Net; an unfold arrow
  reveals each status's inflow/outflow flow (a synthetic "Created" node feeds
  newly-created leads). Clicking any flow drills into the CRM Leads list filtered
  to exactly those leads. All derived from the `CRM Status Change Log` child
  table + each lead's `status`/`creation`; both lenses satisfy
  `Ended = Started + Entered − Left`.
  - `crm/api/leads_dashboard.py` — `get_status_change_report` (table + per-stage
    inflow/outflow) and `get_status_transition_leads` (drill-down name resolver)
  - `frontend/src/components/Dashboard/StatusChangeReport.vue` + `FlowEdge.vue`;
    integrated in `pages/LeadsDashboard.vue`
  - **Acquisition scope toggle** (gw192): a second segmented control next to
    Cohort/Flow — **Acquisition | All stages**, default Acquisition (persisted in
    `localStorage['statusReportScope']`). Acquisition shows only the 7
    acquisition-phase statuses (New → Signed Contract; `ACQ_STAGES` constant in
    `StatusChangeReport.vue`); dispo + parking/terminal stages drop out as rows
    but still appear inside a row's unfolded Came-from/Went-to flows. Pure
    client-side filter over the already-returned stages — toggling is instant,
    backend and drill-downs untouched. If a status is renamed, update
    `ACQ_STAGES` (unmatched rows just fall out of Acquisition scope; All stages
    always shows everything).
  - drill-down: `frontend/src/stores/leadDrilldown.js` holds an ad-hoc lead-name
    set; `pages/Leads.vue` injects it as a never-persisted `name in […]`
    default-filter (+ a dismissible banner); `components/ViewControls.vue` now
    exposes `reload()` so clearing the drill refreshes the list
- **Activity Report** (on the `/dashboard` landing page) — an unfoldable table of
  outreach for the selected range: **Leads called**, **Leads texted**, and
  **Agreements sent**, each shown as **unique leads** *and* **total actions**,
  split **Outbound vs Inbound** (inbound = the lead's replies, so you can see
  who's responding — inbound counts are tinted green; agreements have no inbound).
  Clicking an Outbound/Inbound number drills that set into the Leads list (same
  `leadDrilldown` mechanism as the source/status drills); unfolding a row
  (chevron) lists every lead inline with per-lead out (↗) / inbound (↙, green)
  counts, each clickable to open the lead, plus "Open all in Leads". All three
  dated by `creation`, scoped to `CRM Lead`-referenced records and (for sales
  users) the current user. Also shows **Quo talk time** (total CRM Call Log
  `duration` for the range, outbound/inbound in the tooltip) as a stat in the card
  header, with per-lead talk time in the Leads-called unfold. (Note: inbound calls
  only count if the telephony webhook logged them as `type=Incoming` and linked
  them to a lead — historically all logged calls have been Outgoing, so call
  inbound reads 0; inbound texts work via Quo `direction`.)
  - `crm/api/leads_dashboard.py` — `_activity_summary` (added to
    `get_leads_dashboard` as `activity`) + per-source row fetchers
    (`_call_rows` = CRM Call Log `type`, `_text_rows` = Quo Message `direction`,
    `_agreement_rows` = CRM Esign Agreement) + `_tally` + the `get_activity_leads`
    drill/unfold endpoint (per-lead counts + display names, `DRILL_CAP`). Quo
    Message / Esign Agreement are guarded by `frappe.db.exists` so an unprovisioned
    site just drops that row.
  - **REDESIGNED as a people-first contact ledger** (gw194+gw195, replacing both the
    original type-rows table and the short-lived gw193 "N acq" sub-numbers,
    which Lance rejected): `ActivityReport.vue` is now ONE ROW PER CONTACTED
    LEAD (name · current-status dot · calls ↗out/↙in · talk time · texts
    ↗out/↙in · Agr column only when the scoped set has agreements), scoped by
    an **Acq | Dispo | All** segmented toggle (default Acq, persisted in
    `localStorage['activityScope']`); a totals strip (Contacted / Calls /
    Talk time / Texts) sits above the table and reflects the active scope.
    Inbound is green, outbound gray (the app's direction language). Sticky
    table header in a max-h scroll; row click opens the lead; footer "Open all
    in Leads" feeds the scoped names straight into `leadDrilldown` (no server
    resolver). Backend: **`get_contacted_leads`** merges the three row fetchers
    (which select `Lead.status` through their existing joins) into per-lead
    rows with a `bucket` field — `acq` (`ACQ_STATUSES`, = the 7 acquisition
    statuses; keep in sync with `ACQ_STAGES` in `StatusChangeReport.vue`),
    `dispo` (`DISPO_STATUSES`), or `other` (dead/parked/won) — so ONE fetch
    powers all three scopes client-side (toggle = instant). Bucketing is by
    CURRENT status, not status at activity time. `get_leads_dashboard` no
    longer returns `activity`; `_activity_summary`/`get_activity_leads` remain
    in `leads_dashboard.py` but have no frontend consumer. Mobile (gw195):
    below `sm` the Status + Talk-time columns hide, the name column caps at
    7rem (≈376px total on a 390px phone), and the table wrapper is
    `overflow-auto` so narrower screens scroll inside the card. NOTE: the
    Chrome-MCP extension pins the page viewport (innerWidth stays desktop-size
    regardless of OS window size), so phone layouts can't be visually verified
    through it — verify responsive classes via DOM inspection or a real phone
    (a freshly-created MCP window sometimes DOES render narrow — worth a try).
    gw196: the Status Change Report table is wrapped in `overflow-x-auto` +
    `min-w-[520px]` — unwrapped, its width forced the WHOLE PAGE to scroll
    horizontally on phones, clipping the status names off-screen (Lance hit
    this live). Wide dashboard tables must always scroll in-card.
- **Collapse fleeting (<60s) status changes** — a status changed by mistake and
  quickly corrected no longer leaves a fleeting intermediate behind. A run of
  consecutive status changes where each intermediate was held <60s collapses to
  the net transition (A→B→C in under a minute reads as A→C); a bounce back
  (A→B→A) disappears entirely. Applied on **both** surfaces that show status
  changes, which read different sources, so they agree:
  - **Per-lead/Deal Activity timeline** (Frappe Version history) —
    `crm/api/activities.py` `collapse_rapid_status_changes()`, a display-only
    pass wired into `get_lead_activities` + `get_deal_activities` before the sort
    (never mutates the Version audit trail).
  - **Dashboard Status Change Report** (`CRM Status Change Log` child table) —
    `crm/fcrm/doctype/crm_status_change_log/crm_status_change_log.py`
    `add_status_change_log()` rewrites the prior transition to the corrected
    status and drops the fleeting row at write time (bounce reopens the prior
    row). Threshold constant `MIN_STATUS_HELD_SECONDS` / `STATUS_COLLAPSE_SECONDS`
    = 60 in each file. The creation/initial status is never collapsed.
- **BatchData "Fetch tax / liens"** (Leads + comps page) — a **$0.22**/pull
  button that hits `/property/lookup/all-attributes` with the **deed-history** token
  (`__INFISICAL:BATCHDATA_DEEDS_API_KEY__`). Confirm dialog names the charge.
  Each pull is a **CRM Property Tax Pull** row; headline fields (apn,
  property_owner, tax_status, annual_tax, assessed_value, last_tax_pull_at/by)
  write back onto the lead (Property Details sidebar). The **Tax / liens** card
  (lead sidebar + comps page under the offer calc) shows owner/APN/status plus a
  collapsible **Records** table (foreclosure, deeds, mortgages, tax history).
  Live refresh via `crm_tax_pull`. Comps page also has a **Tax / liens** toolbar
  button (`CompsView.vue`).
  - **Architecture mirrors SMS**: ops server script `pull-tax-info`; app hook
    `on_tax_pull_insert` parses + writeback + realtime. Deed/mortgage/foreclosure
    tables are parsed on **read** from `raw_response` (`_dd_from_raw`) so no extra
    doctype columns. Annual tax falls back to `listing.taxes` when the assessor
    `tax` block is empty.
  - `crm/api/tax_info.py` — `on_tax_pull_insert` hook (parse + writeback +
    publish `crm_tax_pull`) + `get_tax_pulls(lead)` read API
  - `crm/hooks.py` — `CRM Property Tax Pull` `after_insert`
  - `frontend/src/components/Modals/FetchTaxInfoModal.vue` (charge confirm),
    `components/TaxInfoCard.vue` (sidebar object), `pages/Lead.vue` (header
    button + card + realtime doc/sidebar reload), `components/Activities/
    Activities.vue` (`tax_pull` timeline type + `taxPulls` resource + listener),
    `AllModals.vue` (`fetchTaxInfo`), `utils/index.js` (`formatNumber`)
  - **Tax-assessor caveat**: `tax.taxAmount`/`assessment.totalAssessedValue`
    only populate once the "Core Property Data (Tax Assessor)" product is enabled
    on the BatchData account; until then owner/APN/value/tax-status-flags come
    through and the assessor columns stay empty (parser already wired for them).
    BatchData has NO cumulative back-taxes-owed $ field — "taxes owed" = annual
    tax + delinquency status (`tax.taxDelinquentYear` / `quickLists.taxDefault`).
  - Ops (`../frappe-crm-deploy`): `scripts/setup_tax_info.py` (doctype + lead
    custom fields + Property side-panel layout), `site/server_scripts/
    pull_tax_info.py` + manifest entry, synced via `sync_server_scripts.py`.

- **Acq Price (header) + Dispo fields (lead sidebar)** — deal-lifecycle fields
  on CRM Lead (this CRM runs the whole lifecycle on leads; there are zero CRM
  Deal records). **Acq Price** = `acq_price` (Currency) lives in the sidebar
  HEADER: a `$`-icon row (MoneyIcon, no label — `title` tooltip "Acq Price")
  directly under the lead name/address in `pages/Lead.vue`, per Lance ("under
  the lead name and just have the $ icon"). Custom inline editor: digits only
  (non-digits stripped on input), live thousand separators (`toLocaleString`),
  Enter/blur saves via `updateField('acq_price', n)`, Esc reverts; the
  `doc.acq_price` watcher skips overwriting the draft while focused. **Dispo**
  side-panel section = `dispo_price` (Currency, leads the section),
  `inspection_end_date`/`closing_date` (Date), buyer assigned as at-a-glance
  fields (`buyer_name`/`buyer_phone` (Phone)/`buyer_email` (Email)/
  `buyer_entity`/`buyer_em_amount` (Currency)/`buyer_inspection_end_date`
  (Date)), and `list_price` (Currency), rendered generically with inline
  editing. Custom Fields + `CRM Lead-Side Panel` layout rows created by ops
  `../frappe-crm-deploy/scripts/setup_dispo_fields.py` (idempotent, `--dry-run`;
  enforces the canonical Dispo field order, keeps UI-added extras after ours,
  and deletes retired sections via `REMOVED_SECTIONS` — the old `deal_section`
  is gone). Also **removed upstream's 300px max-height + internal scroll on
  side-panel section columns** (`SidePanelLayout.vue` CSS) — it hid fields past
  the fold (Lance couldn't see Buyer EM); the sidebar body is one scroll
  region, so sections now grow naturally.
- **InvestorLift dispo integration** — marketing dashboard on the lead
  (`InvestorLiftCard.vue`) + address auto-matcher, top-level **Dispo** nav →
  per-property buyer Kanban (`pages/Dispo.vue`) + buyer page w/ conversation
  timeline (`pages/Buyer.vue`), automated buyer scraper with SMS-webhook 2FA
  capture, auto-pull of new buyers from address-request notifications.
  Backend `crm/api/investorlift*.py`; settings page
  `Settings/InvestorLiftSettings.vue`. Full design doc:
  `docs/investorlift-integration.md` (merged from
  `feature/investorlift-dispo`, built in a parallel agent session).
- **Buyer directory + manual buyer creation + metro areas** (gw158-gw165) — a
  top-level **Buyers** nav (`/buyers`, `pages/Buyers.vue`): searchable directory
  of every CRM Buyer (search + metro filter + "Active in" = metro or engaged
  property cities + deal counts), a **New buyer** modal
  (`Modals/BuyerModal.vue`, create/edit, dedupes by email→phone→name and offers
  "Open" on a duplicate), and metro linkage: new ops doctype **CRM Metro Area**
  seeded with the **393 Census MSAs** (July 2023 OMB delineation, vendored at
  `../frappe-crm-deploy/scripts/data/us_metro_areas.txt`), plus
  `CRM Buyer.metro_area` (Link) + `buybox` (Small Text, free-form until buybox
  search is structured). Backend `crm/api/buyers.py` (get_buyers/create_buyer/
  update_buyer/get_metro_areas/create_metro_area/get_buyer_calls). Buyer page
  gained Edit (same modal), metro + buybox display. Ops:
  `scripts/setup_buyer_directory.py` (idempotent; doctype + fields + metro seed).
  - **Buyer activity parity with leads**: the buyer page's Conversation now
    renders calls from **CRM Call Log** (matched by last-10 phone against
    from/to) with the lead timeline's own `CallArea` card — recording, Playback
    (waveform + transcript + comments), AI summary — merged time-sorted with the
    live-fetched Quo texts. `get_buyer_calls` reuses `parse_call_log` and
    substitutes the buyer's name for the "Unknown" contact side;
    `get_buyer_conversation` (ingest) is texts-only now.
  - **IL buyer contact enrichment (Marcel Cohen fix)**: the address-request
    webhook now falls back to the IL admin API (property inquiries →
    `/customers/{id}`) when the paired "New buyer signed up" text isn't found,
    so buyers arrive with email/phone/verified/il_buyer_id instead of name-only;
    `_find_buyer` gained a last-resort name match against email-less+phone-less
    rows so enrichment merges instead of duplicating (`investorlift_ingest.py`).
    Cleaned the two prod duplicates (Marcel Cohen, Illinois Land Investment).
  - **Whole-dollar currency display**: `stores/meta.js` getFormattedCurrency/
    getCurrencyWithPrecision default precision 0 (docfield precision still
    wins), and `crm/api/activities.py` `strip_currency_cents()` drops the ".00"
    Version docs bake into timeline "added Acq Price as $ 2,500.00" entries.
  - **Signed agreement PDFs open inline**: `download_signed_agreement` sets
    `display_content_as="inline"` + `content_type="application/pdf"`; the
    AgreementsCard + timeline links are now "Open signed PDF" with
    `target=_blank` (opens as a browser tab; right-click still saves).
  - **Phone display formatting**: buyer surfaces (Buyer/Buyers/DispoBoard/
    CallReview) render numbers via the existing `formatPhone` → (###) ###-####.
  - **Metros are MULTI-select** (gw166): `CRM Buyer.metro_areas` (Small Text,
    JSON array — metro names contain commas) supersedes the single `metro_area`
    Link; BuyerModal uses a chips + Autocomplete picker (client-side over
    `get_metro_areas`, single-line options — the stock Link control showed
    name+title duplicated for metros — with a Create-"query" footer);
    `get_buyers(metro=)` filters via a quoted-JSON LIKE; `active_in` = metros
    joined " · " else engaged-property cities.
  - **Manually add buyers to deals** (deals = leads): `add_buyer_to_lead(lead,
    buyer, stage)` (idempotent, publishes `crm_il_buyers`) +
    `Modals/AddBuyerToDealModal.vue` — mounted on the **Dispo page** header
    ("Add buyer": pick/search a buyer or create one inline via
    `BuyerModal :redirect="false"`, pick a stage) and on the **Buyer page**
    ("Add to deal": pick a dispo property).
- **Buyer activity parity (comments / to-dos / agreements on buyers)** (gw175) —
  the Buyer page (`pages/Buyer.vue`) is now Lead-shaped: tabs on the left
  (**Activity** with the To-do quick-add block + Quick-comment chips, mirroring
  leads · **Conversation** (the bespoke live-Quo texts + phone-matched CRM Call
  Log calls panel, moved into a tab — those aren't reference-linked, so they
  stay outside Activities) · Comments · Tasks · Notes · Attachments), and a
  right Resizer sidebar (profile + Engaged properties + a new **Agreements**
  card). It mounts the SAME `<Activities doctype="CRM Buyer">` component leads
  use — comments (`Comment`), tasks (`CRM Task`), notes (`FCRM Note`) and files
  were already generically keyed by `reference_doctype`/`reference_name`, so
  the whole modal/quick-add/realtime stack (incl. `crm_task_update`) just works.
  - `crm/api/activities.py` — `get_activities` gained a CRM Buyer branch →
    **`get_buyer_activities`** (mirrors the lead version; `avoid_fields` hides
    the machine-churned `last_active`/`deal_history`/`il_buyer_id` versions).
  - `crm/api/comment.py` — @-mention notifications/emails handle CRM Buyer
    (name = `buyer_name`, deep-link route `/crm/buyers/...`).
  - **Agreements: buyer-linked only** (gw176 — Lance: a property's seller-side
    agreements must NOT appear on an engaged buyer). `CRM Esign Agreement`
    gained an optional **`buyer` Link → CRM Buyer** (ops `setup_agreement.py`
    `ensure_field(..., after="lead")`); the buyer card lists ONLY rows with
    `buyer == this buyer` (`get_buyer_agreements`, has_column-guarded), each
    still linking to its lead + `property_label` (rows shaped by the extracted
    `_shape_agreement`). `components/BuyerAgreementsCard.vue` (**new**, modeled
    on AgreementsCard: status badge, buyer link, signed-PDF link, `crm_esign`
    listener). The **＋** is a dropdown of engaged properties → fetches that
    lead's doc → the existing `CreateAgreementModal` (same prefill flow), which
    passes its new optional `buyer` prop → `create_docuseal_agreement(buyer=)`
    stamps the link, so the agreement lands on BOTH the lead's card/timeline
    and this buyer's card. Lead-page creations have no buyer (unchanged).
  - Tab state persists per-user as `lastBuyerTab` (`useActiveTabManager`).
- **Buyer ↔ Quo two-way contact sync + buyer texts/calls sync** (gw177) — every
  CRM Buyer is the peer of a Quo (OpenPhone) contact so buyer numbers show names
  on calls/texts in the Quo apps (the team had been hand-typing "Manny - Chicago
  Buyer" contacts), and buyer conversations are stored/live in the CRM.
  - **`crm/api/quo_contacts.py`** (new) — the sync engine. Push: CRM Buyer
    after_insert/on_update (or explicit `enqueue_push` from the `db.set_value`
    paths in `buyers.py` / `investorlift_ingest.py`, which fire no doc events)
    creates/updates the linked contact; on_trash tombstones `externalId`
    (`deleted-<name>`, mirrors the lead-side unlink script). Pull: `sync_all`
    (cron `*/10`, hooks.py) pages all contacts once and reconciles two-way by
    comparing `contact.updatedAt` / `buyer.modified` against the
    `quo_synced_at` watermark — adopts existing manual Quo contacts by last-10
    phone (never duplicating them), renames junk "*- *-" import rows, pulls
    team edits (name/tags/email-fill) back, creates contacts for unlinked
    buyers, and IMPORTS a Quo-only contact as a new CRM Buyer when its name or
    tag says "buyer" (word match) and its phone matches no lead. Conflict
    policy: on first link a non-junk human-typed Quo name wins (pulled in);
    both-sides-changed → Quo name wins, tags union. Link state on CRM Buyer:
    `quo_contact_id` / `quo_synced_at` / `quo_tags` (ops
    `setup_buyer_quo_sync.py`); key from site_config `quo_api_key`.
  - **Quo tags**: the workspace's "Contact" multi-select custom field (created
    by the team, renamed from "Tags" — resolved by key via
    /v1/contact-custom-fields, matched case-insensitively against
    Contact/Tags) mirrors two-way with `CRM Buyer.quo_tags` (comma-separated;
    editable in BuyerModal as "Quo tags", shown merged with the IL
    `buyer_type` chips on the buyer page). Engaged property addresses are
    pushed one-way into the Quo "Property" multi-select. API values are
    free-form (no predefined options needed).
  - **OpenPhone contact-PATCH landmine** (cost us a test contact): a
    `customFields`-only PATCH returns 200 but wipes `defaultFields` and the
    phone-less contact is then garbage-collected (all later GETs 404). Every
    PATCH must send the FULL merged `defaultFields` with item `id`s stripped.
    Recorded in the Quo Bruno QUIRKS.md.
  - **Buyer texts are stored now**: the ops `sequence-events` webhook mirrors
    texts matching a CRM Buyer phone (leads keep priority) into `Quo Message`
    with `reference_doctype: CRM Buyer`; history backfilled by ops
    `backfill_buyer_texts.py` (fetches workspace lines live).
    `get_buyer_conversation` reads those stored rows (fast, full history, MMS
    media via `SMSMedia`, sender attribution) instead of live-fetching 50/line
    from the API; `Buyer.vue` listens for `quo_message` (re-attached on every
    switch into the Conversation tab, because Activities' unmount does a
    blanket `$socket.off('quo_message')`) and `crm_buyer_update` (emitted by
    the pull sync after changing a buyer).
  - **Buyer calls**: the webhook's `call.completed` mirror stamps
    `reference_doctype: CRM Buyer` when the external number matches a buyer
    and no lead; history stamped by
    `crm.api.quo_contacts.stamp_buyer_call_references` (bench execute,
    dry_run default). The buyer page call list itself still matches by phone
    (`get_buyer_calls`).
  - **Property tags on IL-link + engagement** (gw178/gw179): when a lead gets
    `il_property_id` (manual save → `on_lead_update` hook; the auto-matcher
    links via `db.set_value`, so `investorlift._run_match` calls
    `enqueue_tag_lead_property` explicitly), the SELLER's Quo contact gets the
    property address added to the Quo "Property" multi-select — that also
    makes the tag value exist in Quo (no API to define options; a value exists
    once a contact carries it). A buyer engaging/leaving a property
    (`CRM Lead Buyer` after_insert/on_trash → `on_lead_buyer_change`)
    re-pushes their contact so its Property tags reflect current engagements
    (recomputed from live rows; wholesale replace when non-empty — a
    disengage-to-ZERO leaves the last tag, since pushing [] could wipe
    hand-set values). Backfill: `tag_all_linked_leads` (bench execute).
    gw179: `push_buyer` skips no-op PATCHes (blind PATCHes bump `updatedAt`
    and churn the reconcile) and `_upsert_buyer` only enqueues a push when an
    identity field actually changed — the IL scraper re-upserts every buyer
    each run and once flooded the short queue with 189 no-op pushes.
- **Bulk-text buyers (per-message confirm)** (gw186/gw187) — text a picked group
  of CRM Buyers, but with a **manual confirm on every message** so the rep
  eyeballs each `{{first_name}}` substitution before it sends (Lance's explicit
  ask). A `BulkTextModal.vue` stepper: **Compose** (pick recipients — a checklist,
  all pre-checked = "text all", buyers with no phone auto-excluded + counted;
  write a template with a `{{first_name}}` / `{{name}}` token; confirm the sending
  Quo number) → **Review** (walks recipients ONE at a time: buyer + phone + the
  fully-rendered message in an editable box; Send & next / Skip / Back; running
  "N of M · X sent") → **Done** (sent/skipped/failed summary). Nothing sends
  until the rep clicks — deliberately **one synchronous send per click**, no
  background blast.
  - Two entry points: the **Dispo board** header **"Text buyers"** button (seeded
    with that deal's board buyers via `get_deal_buyers`, deduped) and a
    **select-mode** on the **/buyers directory** ("Select to text" → a checkbox
    column + "Text (N)"; in select mode a row is a `<div>`, not a `router-link`,
    so a click toggles selection instead of navigating — `@click.prevent` on a
    router-link loses the race with RouterLink's own nav handler).
  - **Backend = app code** `crm/api/bulk_text.py` `send_buyer_text(buyer, content,
    from_number)` — sends ONE text to one buyer, content verbatim (the rep already
    confirmed it), buyer phone resolved server-side. Like `agreement_notify.py` it
    POSTs OpenPhone directly with site_config `quo_api_key`, and stores the text as
    a `Quo Message` referenced to **CRM Buyer** (so it threads on the buyer's
    Conversation tab + fires the `quo_message` realtime via the existing
    after_insert hook). No ops piece — reuses the `quo_api_key` already in
    site_config and the existing Quo Message doctype. Sales-roles gated; a
    first-time from-number pick is saved to the user (mirrors `send-text`).
  - `frontend`: `components/Modals/BulkTextModal.vue` (**new**), mounted in
    `pages/Dispo.vue` (button + `get_deal_buyers` resource) and `pages/Buyers.vue`
    (select-mode). `{{first_name}}` renders client-side (falls back to the first
    word of the buyer name, then "there") — and since every message is confirmed,
    a bad substitution is caught before it sends.
  - **Dispo board list view + Buyers "by property" filter** (gw188): (a) the Dispo
    board (`components/Activities/DispoBoard.vue`) gained a **Board/List** toggle
    (segmented control in the `pages/Dispo.vue` header, persisted per-user in
    `localStorage['dispoView']`, passed as a `view` prop). List = a flat,
    stage-ordered table (Stage dot + label / Name / Type / Phone / Direction /
    Last active / Msgs), same `get_deal_buyers` data + realtime as the Kanban.
    (b) The **/buyers directory** gained an **"All properties"** filter (an
    Autocomplete next to the metro filter, options from
    `investorlift_ingest.get_dispo_properties`) that narrows the list to the
    buyers engaged on that dispo property. `crm/api/buyers.py` `get_buyers` gained
    a `property` param (a CRM Lead name) → resolves the `CRM Lead Buyer` rel rows
    to buyer names and adds a `["name","in",[...]]` filter; composes with
    search + metro + select-to-text.
  - **"Text these (N)" on the filtered list** (gw189): when any filter is active
    (search / metro / property) the /buyers toolbar shows a solid **"Text these
    (N)"** button that opens `BulkTextModal` pre-loaded with the WHOLE current
    filtered list (all pre-checked) — a one-click path vs "Select to text"'s
    manual pick. Both now set a shared `bulkRecipients` ref at open time
    (`textThese` = all `rows`, `textSelected` = the checked ones); the per-message
    confirm step still applies. Gated on `hasFilter` so it never blasts all
    ~354 buyers unfiltered.
  - **Status column + status failsafe** (gw190): when filtered by property each
    buyer carries its per-property **interest_stage** (`get_buyers` returns it from
    the `CRM Lead Buyer` row when `property` is set). The property-filtered /buyers
    list swaps its "Active in" column for a **Status** column (colored dot + stage).
    `BulkTextModal` gained a **"Statuses to text"** chip row (shown whenever any
    recipient carries a `stage` — i.e. the Dispo "Text buyers" and the
    property-filtered "Text these"): one toggle chip per present stage (with a
    count), and the recipient checklist + selection are scoped to the active
    stages. **"Not Interested" is OFF by default** (`EXCLUDE_BY_DEFAULT`) so a
    blast can't accidentally hit uninterested buyers; toggling a chip re-syncs the
    selection to the now-visible set. Each checklist/review row also shows the
    buyer's stage. Recipients get `stage` from `get_deal_buyers.interest_stage`
    (Dispo) / `get_buyers.interest_stage` (property-filtered Buyers). Stage colors
    (blue/orange/red/green/purple) live in JS in both `BulkTextModal.vue` and
    `pages/Buyers.vue`. gw191: the **"Text these (N)"** button label now reflects
    the **post-failsafe** count (`textTheseCount` — phone-present and not in
    `EXCLUDE_BY_DEFAULT`), previewing what will actually be pre-selected (e.g.
    "Text these (53)" for a 58-buyer property with 5 Not Interested); the header
    "N buyers" still shows the true filtered total.
- **Kanban render: the N² is gone** (gw325) — the board was "frequently slow and
  laggy" and the query was never the reason. Measured on prod: `get_data` returns
  354 cards in **195ms in-process** and ~400ms over HTTP, but the board then
  **blocked the main thread for 4,303ms** rendering 268 cards. Cloning and
  re-laying-out that same 21,000-node DOM by hand takes **110ms**, so ~97% of the
  cost was JavaScript above the DOM — and it grew as **N²** (108 cards 1,050ms;
  268 cards 4,303ms; fit T ≈ 5.4ms·N + 0.040ms·N²). Now **linear**: 108 → 275ms,
  **275 → 519ms (8.3x)**.
  - **Everything expensive was per-FIELD-per-CARD.** `KanbanCardField` is mounted
    once per field per card — 7 × 287 = ~2,000 instances — and each one:
    (a) called `getMeta()`, which built a **fresh `createResource` every call**;
    (b) ran `getFields()` inside a computed, filtering+mapping all **138** CRM
    Lead fields through a deep `reactive()` proxy (~276,000 proxied reads, each
    also **registering a dependency link**); and (c) mounted a `Tooltip` **and** a
    `Popover` that are invisible until you hover.
  - **The N² term was a write, not a scan.** `getFields()` did
    `f.fieldtype = 'User'` on **every call** — a mutation of shared reactive state
    that all ~2,000 computeds had just subscribed to, so each call invalidated all
    the others. `stores/meta.js` now memoizes the API object and the derived field
    list per doctype, reads the **raw** meta (`toRaw`) with a single `metaVersion`
    ref as the only dependency, and **shallow-copies** the fields that need
    reshaping instead of mutating the store.
  - **Don't build what nobody is looking at.** The hover affordances moved into
    `KanbanCardFieldAction.vue`, mounted on `pointerenter`; the per-card actions
    `Dropdown` mounts the same way via `HoverMount.vue` (which **replays the click
    that woke it**, so it still opens on the first click, including on touch). The
    three counter `Tooltip`s only ever showed a fixed string — a native `title`
    does that for free, the same trade the Today card documents. Buttons per board
    **635 → 193**, DOM nodes per card **~84 → ~60**.
  - `getRow()`/`getRawValue()` scanned the whole row array on **every** call,
    ~25× per card; both are now Map lookups, and `getRow` memoizes its result
    (it allocated a fresh `{label}` wrapper each time). Same fix in `Deals.vue`
    and `Tasks.vue`, which had copies of the pattern.
  - **GOTCHA — `KanbanView`'s `columns` computed wrote to its own dependency**
    (`column.column.color = …` inside the getter). Harmless only because statuses
    always have colours, so the branch never ran; it is now a watcher.
  - **Realtime no longer refetches the board.** `crm_task_update` and
    `crm_first_call` are broadcast **site-wide**, so any task anyone completed —
    **35-116 a day** — made every open board refetch ~300KB and re-render, i.e.
    the lag was usually caused by somebody ELSE's click. They now refresh the one
    affected card through the new whitelisted **`crm.api.doc.get_kanban_card`**
    (verified on prod: one 162ms call, no `get_data`), coalescing bursts over
    250ms, and fall back to a full reload only when the card changed column,
    vanished, or the request failed. `PSEUDO_FIELDS` is now a shared constant so
    the endpoint and `get_data` cannot disagree about what is computed vs stored.
  - **GOTCHA — the site was on HTTP/1.1 and that, not the server, was most of the
    cold-load wait.** The SPA is **106 asset requests**; a browser allows ~6
    connections per origin, so the first API call (`get_users`) sat **3,442ms
    STALLED** — it is 65ms when a connection is free — and since the bootstrap
    chains off it, `get_data` wasn't requested until **t=7.1s**. With h2 enabled:
    stall **6ms**, `get_data` starts at **t=3.1s**. NOTE nginx applies `listen`
    protocol options **per socket, not per server block**, so this turned on h2
    for every site on `:443`.
  - Ops (`../frappe-crm-deploy`, gw325): h2 in `nginx/crm.groundworkpro.com.conf`;
    gunicorn **2 → 4 workers** via a `command:` override in `docker-compose.yml`
    (the image hardcodes `--workers=2`; there is no env var) — `gthread` advertises
    2×4 but Frappe is CPU-bound Python, so the **GIL** means only WORKERS run at
    once, which is why `get_views` took 719ms during a page load and 62ms alone;
    and `scripts/setup_kanban_indexes.py`, which adds the missing
    `(reference_doctype, reference_docname)` index to **`tabCRM Call Log`** and
    **`tabQuo Message`** — `tabComment` had it, those two didn't, and they were the
    two slowest queries in `apply_counts` despite holding a third as many rows.
  - **The card's "+" menu left the flow** (gw327). The three counters are
    229-258px inside a 238px footer, so `justify-between` had nowhere to put the
    button and pushed it **27px past the card's right edge**, where the next
    column covers it — `elementFromPoint` returned the neighbouring column for
    all but ~4px of it. Clipping the counters to make room was tried and
    **reverted**: it kept the button inside (0 of 108 cards overflowing) but hid
    the email counter on **every** card, and trading a fiddly button for
    permanently missing information is the worse deal. The button is now absolute
    and hover-revealed at the card's bottom-right, exactly like the copy/pencil
    affordances on the field rows — nothing is hidden at rest, and it hit-tests
    as itself with 11px of clearance. `group/card relative` on the card in
    `KanbanView.vue` is what the reveal hangs off.
  - **GOTCHA — a hover-only trigger for a MODAL menu positions itself at (0,0).**
    reka-ui's dropdown sets `pointer-events: none` on `<body>` while open, so the
    card instantly loses `:hover`, a `group-hover`-only trigger collapses to
    `display:none`, and Popper then anchors the open menu to a 0x0 box at the
    origin — the menu lands in the **top-left corner of the window**, ~750px from
    the card. reka sets `data-state="open"` on the trigger, so
    `has-[[data-state=open]]:flex` pins the container open for exactly as long as
    its menu is. This is the second time this bug has appeared here:
    `KanbanCardFieldAction` already carries an `editorOpen` ref and a comment
    saying the same thing about its Popover. Any hover-revealed control that
    OPENS something needs one of the two. **Third time (2026-09-04): the
    column header's `⋯` → Delete menu** in `KanbanView.vue` had the identical
    `hidden group-hover:flex` trigger and landed at (0,0); fixed with
    `data-[state=open]:flex` on the button itself.
  - The counters were also made to fit rather than be clipped: gaps went
    `gap-1.5 → gap-1` between groups and `gap-1 → gap-0.5` inside them, which
    recovers ~20px.
  - **The email counter is gone, and that is what actually fixed the crowding**
    (gw328). `tabCommunication` on this site is **EMPTY — 0 rows, ever**, because
    no email integration is in use, so `@ 0↑ 0↓` rendered on all 108 cards
    forever: ~70px of the widest row on a 268px card, showing nothing, and the
    reason everything else ran out of room. It now renders only when that lead
    has email activity, so it comes back by itself if email is ever adopted.
    **Calls and texts stay unconditional on purpose** — a zero there is a real
    signal a setter acts on ("never called"), not an absence. Measured after:
    **0** cards showing it, **0** with footer overflow, **0** where the hover
    chip can occlude anything; the tightest card went from **1px** free to
    **75px** for a 32px chip.
  - **GOTCHA — `emptyOutDir: false` means `assets/` holds OLD `index-*.css`.**
    Grepping the wrong one made a Tailwind rule that had emitted correctly look
    missing, and nearly sent a working fix back for a redesign. Resolve the
    newest (`ls -t`) before concluding a class didn't compile.
  - **A drag no longer refetches the board** (gw329). A CROSS-column drag was
    always cheap (`applyKanbanDrag` just `set_value`s the status and lets
    vuedraggable keep the card where it was dropped), but every other drag fell
    through to `applyKanbanViewUpdate`, which ended in an unconditional
    `list.reload()`. Reordering two cards inside one column — or dragging a
    column sideways, or recolouring one — therefore cost a full board round trip
    plus a full re-render, **~0.5s of frozen board**, to be handed back the
    arrangement already on screen. The client already knows the answer; the order
    it is about to persist IS the answer. It now reloads only when the shape of
    the DATA changes: different `kanban_fields`, a different `column_field` /
    `title_field`, or a column brought back from deleted (whose cards were never
    fetched). Measured before → after on a real drag: refetch **yes → no**,
    blocked main thread **~490ms → 124ms**. The view is still saved either way —
    verified the reordered order survives a full page reload, which is the check
    that matters, since skipping the refetch must not skip the save.
  - **Virtualizing the board is NOT the next win — measured, don't re-litigate.**
    90% of rendered cards are off-screen (11 of 108 visible at 1200px; 4 of 15
    columns), which sounds damning, but:
    - **scrolling is already 60fps** — avg frame **16.6ms**, p95 18.6ms, max
      18.7ms, horizontally AND vertically inside a column. There is nothing to
      win. The browser does not care about 6,500 nodes.
    - **back-navigation already paints from cache.** The list resource carries
      `cache: [doctype, view, viewType]`, and frappe-ui's `createResource`
      returns the *same resource with its data intact* on a cache hit. Measured
      returning from a lead: first card in the DOM at **236ms** while the
      revalidating `get_data` didn't resolve until 4,058ms — i.e. the network is
      already off the perceived path (stale-while-revalidate, for free).
    - and virtualization actively **fights vuedraggable**, which needs the
      destination column mounted to drop into.
    The only thing left is ~200ms of off-screen render on a cold board, and the
    cure is worse than the disease at this size. Revisit only if a column
    routinely holds many hundreds of cards.
  - **GOTCHA — don't count network calls with
    `performance.getEntriesByType('resource')`.** Its buffer caps at 250 entries
    and silently drops the oldest, so `entries.slice(baselineIndex)` quietly
    returns the wrong window. It reported "0 API calls" for a drag that had
    demonstrably saved the view to the database. Wrap `window.fetch` instead.
  - **GOTCHA — measure render in a FOREGROUND tab.** Chrome throttles rAF and
    defers paint in a background tab, so a headless/background harness reports a
    first-contentful-paint of 7.8s for a page that paints in 384ms. Block-time
    measurements happened to survive it (before/after were measured identically),
    but any absolute timing taken in a background tab is not real.
  - **`get_data` is not fired "late" in normal use** — measured warm, foreground:
    FCP **384ms**, the nine bootstrap calls fire in PARALLEL at ~310ms and finish
    by ~425ms, `get_data` runs **724ms → 1508ms**, everything done by 1.76s. The
    t=2.8s/7.1s figures quoted during diagnosis were a cold asset cache right
    after a deploy (every chunk re-hashes) plus background-tab throttling. In-app
    navigation doesn't re-run the bootstrap at all: lead → board is ~220ms of
    render. So the cold-load waterfall was never what "laggy" meant.

- **Stale quick-filter searches on personal views** (gw327) — typing in the
  "Full Name" box writes `lead_name LIKE %…%` into your saved standard view and
  leaves it there. Intended (views remember filters) and visible (`quickFilterList`
  seeds the box back), but in practice people search one lead, navigate away, and
  never clear it. Found on prod 2026-08-14: **German's kanban was showing 1 of 353
  leads** (`%simmons%`, set the previous morning), **Exe's 1 of 353** (`%shel%`),
  and German's list 5 of 353 (`%patrick%`). Both setters had effectively lost
  their board. Cleared with ops `scripts/clear_stale_view_searches.py` (dry-run by
  default, `--apply` to write); both boards are back to 108 cards / 15 columns.
  Deliberately narrow: only `lead_name` LIKE, only PERSONAL views (never a public
  one like the ISTL LeadPack boards, which are filtered on purpose), and only that
  one key is dropped so a deliberate status filter alongside it survives. **Worth
  re-running if someone says the board "is empty" or "lost my leads".**
  - **And the trap itself is closed** (gw328): the Leads board now carries an
    amber banner naming every active filter **and what it leaves you with** —
    *"This board is filtered — showing 1 lead · Full Name contains simmons"* —
    with one-click Clear. Two signals already existed (the text sitting in the
    quick-filter box; the count badge on the Filter button) and **both were
    missed for a day**, because both describe the STATE and neither describes the
    COST. Persistence itself is unchanged — views are supposed to remember
    filters; what was missing was consequence.
  - Scoped so it can't become wallpaper: **personal standard views only**
    (`!route.query.view`, the same condition `createOrUpdateStandardView` uses),
    and it ignores the injected `default_filters` behind the dashboard drill-down
    and the tasks-due scope, which already have their own UI — the same
    distinction `Filter.vue` draws. `ViewControls` gained `clearFilters()`, which
    funnels through the existing `updateFilter` so persistence and reload behave
    exactly like clearing from the Filter popover. Applies to the list view too.
    **Deals was deliberately not given the banner** — there are zero CRM Deal
    records, so it would be untested surface for no benefit.

- **Filters: user pickers, no phantom queries, and a 10x faster kanban**
  (gw222/gw223) — Lance: "filters aren't really working… assigned to isn't
  working really… adding filters in the gui is pretty laggy." Three distinct
  causes, all in the list/kanban filter path.
  - **`getValueControl` checked the operator before the fieldtype.** The
    `like`/`not like`/`in`/`not in` branch sat above every fieldtype branch and
    returned a bare `FormControl type=text`, so the perfectly good `Link`
    dropdown one branch below was unreachable. Worse, `getDefaultOperator`
    returned `like` for Link too — so **every** Link field (Lead Owner, Created
    By…) and **Assigned To** opened as an empty text box. `_assign` stores
    `["dennis.szafran@groundworkpro.com"]`, so the only thing that ever matched
    was a substring of the login *email*: typing "Dennis Szafran" returned
    nothing, which is exactly what "isn't working" meant.
    Now `_assign` and any **Link → User** field render
    `Controls/UserFilterSelect.vue` — an Autocomplete over the already-loaded
    users store (`full_name` label, email as the sub-line, **zero network
    calls**). It owns the `%email%` wrap/strip, so it emits a ready-to-store
    value and Filter.vue's blanket `@change` handles it unchanged
    (`inheritAttrs: false` keeps the template's `v-model`/`placeholder` off the
    inner Autocomplete). `getDefaultOperator` now returns `equals` for
    Link/Dynamic Link so the dropdown is the default; `like` is still selectable.
  - **Picking a field immediately ran a query — with no value.** `setfilter`
    called `apply()` straight away, so an empty `like` became `%%`, and since
    `NULL LIKE '%%'` is NULL, merely *naming* a field silently dropped every
    record with nothing in it (adding "Assigned To" hid all unassigned leads —
    prod's saved kanban view was sitting in exactly that state). `filters` was
    also a **computed whose Set got mutated**, so a row could only persist if the
    server echoed it back. It's now a `ref` synced from a separate
    `appliedFilters` computed, which keeps valueless rows as client-only
    **drafts**: they show in the popover, never reach the server, and cost no
    round-trip. `hasValue()` gates `apply()`/`setfilter`/`removeFilter`/
    `updateOperator`. Also guarded `updateValue` against the `null` a cleared
    DatePicker emits (`value.target` threw) and `transformIn` against non-strings.
  - **Every filter change fetched the board twice.** `updateFilter` →
    `list.reload()` **and** `createOrUpdateStandardView()` → `reloadView()` →
    the `getView` deep watcher → `reload()` again, identical result. The view
    write is now debounced 600ms (`persistStandardView`) and the watcher skips
    the echo of our own write via `skipNextViewReload`, released on a
    `setTimeout(…, 0)` after `reloadView()` — a macrotask, so it lands after the
    microtask watcher flush. (Comparing `getParams()` to `list.params` does NOT
    work: the response's resolved `columns`/`rows` differ from the view's.)
  - **The real lag was `getCounts`: ~18 queries PER CARD.** A 104-card leads
    kanban = ~2,600 round-trips and `get_data` took **2.6s** (the list view,
    which skips it, takes 83ms). Replaced with `apply_counts(rows, doctype)` —
    one grouped query per source (`_count_by_doc`/`_max_by_doc` via `frappe.qb`,
    with a `split_field` for the direction splits), plus one `CRM Lead` fetch for
    `_first_call`/`_new_lead_color` and `istl_refund_report.prefetch_outbound_calls`
    for the ISTL tint (`refund_card_color` gained an optional `calls=` param).
    Called once over every card on the board, after the column loop. `getCounts`
    remains as a single-record wrapper. **Verified on prod against the old
    per-card code: 0 mismatches on 150 and 400 leads, 51x and 114x faster;**
    kanban `get_data` 2624ms → 253ms live.
  - Net: one filter interaction went from 2 kanban fetches (~5s) to one ~250ms
    fetch, and adding a filter field costs nothing until you fill it in.
  - `frontend/src/components/Controls/UserFilterSelect.vue` (**new**),
    `components/Filter.vue`, `components/ViewControls.vue`, `crm/api/doc.py`,
    `crm/api/istl_refund_report.py`. No ops piece, no schema change.
  - **gw224/gw225 — an unmapped operator wedged the whole popover.** On the ISTL
    LeadPack view, setting any filter did nothing: the value changed, the badge
    counted it, the list never reloaded. `oppositeOperatorMap` is keyed on the
    UPPERCASE `LIKE` that the popover itself writes, but the per-import-list
    views are generated in `lead_import.py`, which wrote lowercase `like` — so
    `convertFilters` produced `operator: undefined`, and the next `apply()` threw
    on `f.operator.includes(...)`, killing the emit before it reached
    `updateFilter`. **This was the original "filters aren't really working — I
    noticed it on the ISTL import" report**; it survived gw222/gw223 because the
    standard views all store `LIKE`. Fixed three ways: `normalizeOperator()`
    resolves any case and never returns undefined; `transformIn`/`parseFilters`
    tolerate a missing operator; and `lead_import.py` now writes `LIKE`. A prod
    sweep normalized the 2 stored views and dropped a stale
    `_assign: ["LIKE","%%"]` sitting on a personal kanban (it had been hiding
    every unassigned lead on that board).
  - **gw229 — "Save as new" + Source quick filter dropped.** The only way to
    keep an ad-hoc filtered set was "Save Changes", which writes back over the
    view you opened (destructive on a shared/public one like the LeadPack), or
    the buried views-dropdown → Create View. A **Save as new** button now sits
    in the view-controls row: it snapshots the live params into a fresh
    `CRM View Settings` via the existing `ViewModal` in `create` mode, then
    navigates to it. Shown whenever `viewUpdated` OR any filter is active —
    the second half matters because on a *standard* view
    `createOrUpdateStandardView` flips `viewUpdated` back to false within a
    second, so a `viewUpdated`-only gate would flicker. Cancel/Save Changes keep
    their original stricter gate (saved view + not public-unless-manager).
    `saveAsNewView()` + `canSaveAsNew` in `ViewControls.vue`, both toolbars.
    Also dropped **`source`** from the Leads quick-filter row (`_hidden` in
    `get_quick_filters`) — the Filter popover covers it and the row was tight.
  - **Multi-select filters** (`Controls/MultiSelectFilter.vue`, **new**) — a
    compact summary button ("Dennis Szafran" / "2 selected" / "All (6)") opening
    a searchable checkbox list with Select all / Clear. Renders as a summary
    rather than chips because it has to fit the filter row beside two selects.
    Used whenever the operator is `in`/`not in` and the field can offer options:
    **users** (`_assign` + any Link→User, from the users store), **import lists**
    (`crm.api.lead_import.get_import_lists`, with per-list lead counts — nobody
    remembers a generated name like "ISTL LeadPack — Jun 2026"), and **Select**
    fields. `_assign`/`import_lists` now default to the `in` operator.
  - Backend `crm/api/doc.py` **`expand_json_list_filters`** — `_assign` and
    `import_lists` are JSON arrays in a Text column, so "any of these" is an OR
    of LIKEs, not a SQL IN. It resolves the OR to a concrete `name in [...]`
    once, up front, because the same dict filter is reused by the kanban's
    per-column queries, per-column counts and total count, none of which take
    `or_filters`. Per-field needle shapes (`%email%` vs `%"list name"%`) live in
    `JSON_LIST_FILTER_FIELDS`. `_constrain_names` **intersects** with an
    existing `name` filter rather than overwriting it, so it composes with the
    dashboard drill-down. **Must run AFTER `apply_import_visibility`**, which
    opts a query out of the parked-lead exclusion by looking for the very
    `import_lists` key this rewrites away. Verified on prod: single-value `in` ==
    the old LIKE (256), multi-value == the union of LIKEs (698), empty == 0,
    intersection with a 5-name drill == 5, `import_lists in [pack]` == 514.
- **Lead import hardening + address repair** (gw221) — the Jun 2026 LeadPack
  (514 leads) went in with the address block scrambled on part of the batch and
  the importer said nothing: **88 leads had the whole street address sitting in
  `property_zip`** with `property_address` empty, **31 had the seller's EMAIL in
  Property Address / Property City**, and **122 had 3-4 digit ZIPs** (MA/NJ/RI
  leading zero eaten by the spreadsheet). Pattern = the pasted sheet was a
  concatenation of vendor exports with different column layouts under one header
  row; the importer wrote every cell wherever the mapping pointed and reported
  "514 created, 0 errors".
  - **Repair** — `crm/api/lead_import.py` `repair_import_addresses(list_name,
    dry_run=1)` (bench-executable, whitelisted, idempotent). `_repair_one(doc)`
    is the pure per-lead rule: rescue an email out of an address field onto
    `email` (a second one goes to `lead_summary` as "Other contact:"), drop
    vendor placeholders ("Not Provided"), then treat a **complete address as
    authoritative wherever it turns up** — `FULL_ADDR_RE` (plus a `, USA` tail
    strip) re-seeds address/city/state/ZIP in one go — and finally zero-pad the
    ZIP. **Invariant: a value is only ever cleared if it has been written
    somewhere else, or is a placeholder.** `_looks_like_street` is deliberately
    strict (house-number-led or comma-bearing) because it gates lifting a value
    out of the City column, where a bare town name legitimately lives. Ran on
    prod 2026-07-28: 222 leads changed, 0 values dropped, re-run is a no-op.
  - **Same rule now runs at import time** — `import_leads` calls `_repair_one`
    on each built row, so the address lands right whichever column it arrived
    in. A correctly-shaped row passes through untouched.
  - **Pre-flight warnings in the map step** (`ImportLeadsModal.vue` `warnings`
    computed, amber block above the mapping table — warns, never blocks): rows
    whose cell count ≠ the header's (the classic whole-batch-one-column-over
    cause); two columns mapped to the same field (`buildRows` keeps only the
    last non-empty, silently); a column whose sampled values don't match the
    field it's mapped to (email/full-address/ZIP/phone shape vs `EXPECTED`); and
    columns that have data but no field selected. Verified live against a
    synthetic sheet — all four fire.
  - **Dead aliases fixed**: `sellermotivation`/`reasonforselling` and
    `howfasttheywanttosell` pointed at `property_reason_for_sell` /
    `property_duration_to_sell`, which are **not fields on CRM Lead** (the real
    ones have no `property_` prefix). `guessField` only accepts an alias the
    field list offers, so those columns were silently dropped on *every* import
    ever run — all 514 leads have `reason_for_sell`/`duration_to_sell` null.
  - Done-screen copy no longer claims leads auto-promote when status leaves
    "New" — promotion has been manual-only since gw215.
- **Parked import leads are hidden from the dashboard too** (gw221) — the Leads
  board hid a fresh batch (`import_hidden`) but `/dashboard` still counted it,
  so 2026-07-27 read **518 new leads instead of 4**, with the LeadPack owning
  the source donut and inventing a status cohort. `crm/api/leads_dashboard.py`
  gained `live(query, Lead)` (`import_hidden IS NULL OR != 1`, `has_column`
  guarded — NULL is visible, since a lead predating the field isn't parked),
  applied at all 15 CRM Lead query sites (summary, trend, status changes, cohort
  + flow tables, every drill-down resolver, and the call/text/agreement activity
  fetchers). `crm/api/dashboard.py` `get_leads_by_source` imports it **inside
  the function** — `leads_dashboard` already imports `dashboard` at module
  level, so a top-level import would cycle.
- **Buyer import (bulk + single)** — buyers only ever arrived on their own (the
  IL scraper + the address-request webhook); a bought cash-buyer list, a county
  LLC export or a REIA spreadsheet had no way in but the one-at-a-time modal.
  Mirrors the bulk **lead** importer (`crm/api/lead_import.py` +
  `ImportLeadsModal.vue`, gw211-215) closely enough that the two read the same.
  - **Bulk**: `crm/api/buyer_import.py` + `Modals/ImportBuyersModal.vue`, in the
    "…" menu on **/buyers** and as **Import buyers** on the **Dispo** board
    header (pre-seeded with the open board). Paste rows / upload a CSV → confirm
    the auto-guessed column mapping → optionally pick a **property** and the
    **reps** to split the batch between. 200-row chunks; `assign_offset` carries
    the round-robin rotation across chunks.
  - **Property picker** = leads in **Signed Contract, Photos & Lockbox In
    Progress, Needs Listing, Marketing to Buyer, Buyer Assigned**
    (`PROPERTY_STATUSES`; confirmed with Lance — "Contract Sent" isn't ours yet,
    "Won" is done). Each buyer gets a `CRM Lead Buyer` row at the chosen stage;
    one `crm_il_buyers` emit for the whole batch, not one per buyer.
  - **Assignment = ownership** (Lance's call, over per-buyer call tasks): a
    Frappe `_assign` ToDo on the CRM Buyer. A buyer someone already owns is
    never re-assigned, and the rotation only advances on an assignment that
    actually happened, so the reps who do get buyers get an even split.
  - **Dedupe** = email → last-10 phone → name (the `_find_buyer` rule), but built
    as ONE index up front — `_find_buyer` re-queries per lookup, which would
    scan the whole buyer table once per row. A matched buyer is attached and
    assigned but **never overwritten**: blank fields get filled in, curated
    values stay. Re-import is therefore idempotent (verified on prod).
  - Metro columns are **matched** against the Census list, never created;
    unrecognised ones are reported back. Junk emails (alt numbers, "n/a") are
    dropped rather than failing an otherwise good row.
  - **Single**: `Modals/BuyerModal.vue` gained the same **Add to property +
    Board stage + Assign to** row on create (skipped via `:with-property="false"`
    from `AddBuyerToDealModal`, which attaches the buyer itself). If the buyer
    already exists, the duplicate banner now also offers **Add to property**.
  - **A dispo board no longer requires an `il_property_id`** (gw220). That was
    never the right gate — a deal needs a buyer board the moment it's ours, and
    most of ours are never posted to IL (three of the eight properties under
    contract had no board at all). `get_dispo_properties` now lists a lead if
    ANY of: its status is in **`DISPO_LEAD_STATUSES`** (the same five statuses
    the importer's picker uses) · it already has buyers on its board (so a board
    + its history survives the status moving on to Won/Dead) · it's IL-linked
    (the old rule, kept). The status tuple lives in
    `investorlift_ingest.DISPO_LEAD_STATUSES` and `buyer_import` imports it
    (the other direction would cycle), so picker and switcher can't drift.
  - **Buyer import lists** (gw247/gw248) — every bulk import tags its batch
    with a **list name** on `CRM Buyer.import_lists` (JSON array, same shape +
    `_dump`/ensure_ascii rules as the lead-side field — helpers imported from
    `lead_import`), so /buyers can filter to "the REIA list from July" and feed
    exactly that set into "Text these (N)". The import modal's **List name**
    input is prefilled ("Buyer list — Jul 30, 2026"; a CSV upload swaps in the
    filename unless the user typed their own — note the ref must be initialized
    at setup, not just in the `watch(show)` reset, because /buyers mounts the
    modal with `v-if` so it mounts with `show` already true and the watcher
    never fires); clearing it skips tagging. Matched existing buyers get the
    tag too (membership, not provenance) via side-effect-free `db.set_value`.
    /buyers gains an **"All lists"** Autocomplete (hidden until a list exists;
    options `name (count)` from `get_buyer_import_lists`), seedable via
    `/buyers?list=…` — which the done-screen's "Open this list" button uses —
    and counted into `hasFilter` so the bulk-text button lights up.
    `crm/api/buyer_import.py` (`list_name` param + `_add_to_list` +
    `get_buyer_import_lists`), `crm/api/buyers.py` (`get_buyers(import_list=)`,
    quoted-JSON LIKE like metros), `ImportBuyersModal.vue`, `pages/Buyers.vue`.
    Ops: `scripts/setup_buyer_import_lists.py` (adds the Long Text field; all
    app code has_field/has_column-guarded).
  - **Gotcha (bit the lead importer too, silently)**: `usersStore()` is a Pinia
    *setup* store, so `store.allUsers` is the UNWRAPPED array and
    `const { allUsers } = usersStore()` hands back a stale snapshot; `users` is
    the resource whose `.data` is `{allUsers, crmUsers}`, not an array. Both
    spellings render an EMPTY rep-chip row — read `usersStoreRef.allUsers`
    inside the computed. Fixed in `ImportLeadsModal.vue` as well.
  - No ops script: every field written already exists.
- **CRM-wide delete access + movable Dispo buyers + structured Buyer Buybox**
  (gw256) — every CRM sales role can delete primary records. Lead/Deal/
  Organization/Buyer permissions already allowed it; stock Contact did not, so
  ops `scripts/setup_crm_delete_permissions.py` explicitly enables delete for
  Sales User + Sales Manager (and repairs drift across Contact/CRM Lead/CRM Deal/
  CRM Organization/CRM Buyer/CRM Lead Buyer). Buyer detail now has its missing
  trash action and uses the standard linked-document delete/unlink modal.
  - Dispo cards are real cross-column `vuedraggable` items; dropping calls
    `crm.api.buyers.move_buyer_stage`, persists `CRM Lead Buyer.interest_stage`,
    and publishes the existing `crm_il_buyers` realtime event after commit.
    Every card also has an accessible **Move buyer** menu (same endpoint) for
    touch/keyboard use and as a precise alternative to drag.
  - CRM Buyer gained JSON-list `buybox_cities` (**Buying In**; legacy field
    name, now stores city/state markets, whole states, and ZIP codes) and
    `buybox_property_types` fields. Both render as searchable, create-any-value
    chip pickers in the Buyer edit modal and inline sidebar; location
    suggestions come from distinct CRM Lead cities, states, and ZIPs. Existing
    `buybox` is the free-form notes layer (price/condition/deal-size/etc.). The
    shared Autocomplete footer now reads its reactive query and clears it on
    close; the prior private-DOM `_value` lag could save `554012`, and reopening
    after adding `MN` concatenated the next entry into `MN55401`.
  - Buyer detail now mounts the standard globally-configurable
    `SidePanelLayout`: default sections are **Buybox** and **Buyer Details**;
    managers use the section pencil → Edit Field Layout to add/reorder/remove
    any CRM Buyer field. Ops `scripts/setup_buyer_directory.py` adds the two
    fields and seeds `CRM Buyer-Side Panel` once without overwriting later
    manager customization.
  - App: `crm/api/buyers.py`, `crm/api/investorlift_ingest.py`,
    `frontend/src/components/Activities/DispoBoard.vue`, `BuyerDetailPanel.vue`,
    `Controls/JsonListControl.vue`, `Modals/BuyerModal.vue`,
    `SidePanelLayout.vue`, `pages/Buyer.vue`. Ops: the two setup scripts above.
  - **IL webhook duplicate-buyer race (gw257)** — "a new Abdul appeared when I
    dragged him" was NOT the drag: OpenPhone delivers the same InvestorLift
    notification text once per Quo line it reaches, so `on_sequence_event` →
    `_handle_address_request` ran twice ~0.2s apart, both passed `_find_buyer`
    pre-commit, and both inserted (Abdul BUY-00425/426 and Shelton
    BUY-00409/410 were each created 0.2s/0.016s apart, owner=Guest). The two
    stacked duplicates only became visible when one was dragged into another
    column. Fixed with a MySQL `GET_LOCK` named lock keyed on the buyer name
    around the find+insert, plus a `frappe.db.commit()` right after acquiring
    it — REPEATABLE READ otherwise keeps the waiter's `_find_buyer` blind to
    the row the parallel handler committed. Shell duplicates were raw-deleted
    (`frappe.db.delete`, deliberately skipping `on_buyer_trash`: each shell
    SHARED the keeper's `quo_contact_id`, and the trash hook tombstones the
    contact — even with the id cleared it falls back to an externalId lookup),
    then the keepers re-pushed so the Quo contact's externalId points at the
    survivor.
- **Dispo Not Interested reasons** — moving a buyer card into **Not Interested**
  (drag or the accessible Move buyer menu) pauses the move and opens an IL-style
  multi-select: Pricing / Not buying in this location / Not currently in the
  market / Daisy chainer / Does not buy deal type / Property condition / No
  longer buying / Other, plus an optional note. Cancel reloads the board so a
  dragged card snaps back; Submit saves the stage + reasons atomically through
  `move_buyer_stage`. Reasons live on the per-property `CRM Lead Buyer`
  relationship (never on global `CRM Buyer`), are editable from the card/menu,
  and clear when the buyer leaves Not Interested so a later IL-origin stage move
  cannot surface stale CRM reasons. Orange reason symbols render on board/list
  cards; the Not Interested column header aggregates labeled counts per reason
  for that property (legacy rows with no selection count as Unspecified). The
  modal renders all eight choices without an internal scroller — the app's
  hidden-scrollbar styling made the bottom choices look absent in the first live
  verification. Reuses `crm_il_buyers` realtime. App:
  `crm/api/buyers.py`, `crm/api/investorlift_ingest.py`,
  `frontend/src/components/Activities/DispoBoard.vue`,
  `BuyerRejectionReasonBadge.vue`, `Modals/BuyerRejectionReasonModal.vue`, and
  `utils/buyerRejectionReasons.js`. Ops:
  `scripts/setup_buyer_rejection_reasons.py` adds JSON reasons / note / by / at
  fields; `setup_investorlift.py` includes them for fresh sites.
- **Do-not-contact + InvestorLift stage integrity** (gw296) — a buyer replied
  "remove", Exe moved his card to **Not Interested**, and it was back in
  **Attempted to Contact** on the next refresh; a later bulk text reached him and
  he complained. Three separate defects, all now closed. **The rule that came out
  of it: `il_stage` may ONLY ever be written from an observed scrape of the
  board.** Nothing else may claim to know what InvestorLift thinks.
  - **The sync daemon was confirming pushes InvestorLift never accepted.**
    `_push_stages` clicked the card over, then "verified" by re-reading
    `lead.lead_status` **out of the board's React store** 3.5s later — client-side
    optimistic state that `onTaskMove` updates whether or not IL persists
    anything — and called `confirm_stage_push`, which stamped
    `il_stage = "Not Interested"`. IL had silently dropped the move (its handler
    no-ops on a rejected transition), so the next scrape read the real column,
    saw it differ from the poisoned `il_stage`, concluded "IL moved the card" and
    overwrote the human's decision — **silently, via `db.set_value`, so it left no
    version-history row**, which is why it looked like nothing had touched it.
    Proof it never landed: push.log claims success at 11:28, and the *next* push
    at 11:39 still reports `before: attempted_to_contact`.
    `confirm_stage_push` is **replaced by `record_stage_push_attempt`**, which
    records only that we tried. A push that really landed needs no confirmation —
    the next ingest sees the new column and reconciles, the same self-healing
    path the daemon already relied on whenever a confirm call failed.
    `MAX_STAGE_PUSH_ATTEMPTS = 5` then reports the row as unpushable instead of
    retrying a transition IL refuses forever (`il_push_attempts` resets whenever
    the board actually changes).
  - **The address-request webhook and the inquiry pull both hardcode
    `"column": "NEW LEADS"`** — a fine default for a card that doesn't exist yet
    and a lie about one that does. Fed to the comparison it read as "IL moved
    this card to New", re-snapshotting `il_stage` and handing the next real
    scrape a false signal. Both now pass `stage_is_authoritative=False`, which
    applies the stage on CREATE only.
  - **A human's explicit Not Interested is never undone by an IL-origin move.**
    `not_interested_by` is stamped only by `move_buyer_stage`, i.e. only by a
    person, so it can't mistake IL's own bookkeeping for one of our decisions.
    `il_stage` still records what IL says (so the push queue keeps trying); IL
    just doesn't get to win in the meantime.
  - **`CRM Buyer.do_not_contact` is the durable opt-out** — the board column was
    the wrong place to store a removal request, because it is shared state with a
    third party that has no idea anyone asked us to stop. No IL code path writes
    the new field. `crm/api/do_not_contact.py` (**new**): `is_opt_out()` matches
    carrier keywords **only as the whole message** ("cancel"/"end" occur
    naturally — "I want to cancel the contract on 123 Main" must not flag) plus
    unambiguous phrases anywhere ("remove me", "stop texting", "lose my number");
    `check_inbound_opt_out` is a `Quo Message` after_insert hook that flags the
    buyer automatically; `backfill_opt_outs(dry_run=1)` sweeps stored history.
    **"not interested" is deliberately NOT an opt-out** — conflating a board
    stage with a removal request is the original mistake. Ambiguous compound
    cases ("take me off this property but keep sending others") flag, because
    over-applying costs one click to undo and `do_not_contact_reason` stores the
    message verbatim so the reviewer sees exactly what tripped it.
  - **`bulk_text.send_buyer_text` refuses a flagged buyer server-side**, last,
    after the UI has already dropped them — the text went out precisely because
    the signal the UI filtered on could be changed by another system. The modal
    **removes** them rather than unchecking (so "Select all" can't reach them) and
    **names them** in a red banner; `textTheseCount` reflects the post-failsafe
    number. Badges on the Dispo card + list, the /buyers row, and a banner with
    an Allow button on the buyer panel (turning it ON is unconfirmed, turning it
    OFF asks).
  - `_upsert_buyer` also **stops rewriting `buyer_name`/first/last for a flagged
    buyer**: the team's habit is to append "(REMOVE)" to the name, and IL rewrote
    it from its own scrape every cycle with `update_modified=False`, so the
    marker vanished within minutes leaving `modified` still showing the human's
    edit. (Observed live: the Aug-3 "(REMOVE)" was gone by Aug-5 with no human
    touch.)
  - Ops: `scripts/setup_do_not_contact.py` (CRM Buyer `do_not_contact` /
    `_reason` / `_by` / `_at`; CRM Lead Buyer `il_push_attempts` /
    `il_push_last_at`). Daemon lives in `~/Projects/Groundwork/investorlift-sync`
    (launchd `com.groundwork.investorlift-sync`) — **it is a separate repo from
    the ops repo and must be restarted for daemon.py changes to take effect.**
  - GOTCHA: the sync writes with `db.set_value(..., update_modified=False)`
    throughout, so **a machine overwrite leaves no Version row and does not move
    `modified`**. A field that "changed by itself" with an old `modified` stamp is
    the signature — look for a `set_value` writer, not a user.
- **Lead property photos → shared Google Drive** — a **Photos** sidebar card +
  **Photos** item in the Lead header More ▾ menu open a gallery modal: drag-drop
  or pick multiple files, scroll a thumbnail grid, click through them in the
  existing `ImageLightbox`, **Download all** as one zip, or **Copy folder link**
  to hand to a listing agent. Photos live in Drive ONLY (nothing mirrored into
  Frappe's File table).
  - **Folder** = one per lead, named after the full property address (composed by
    `agreement._full_property_address`, so a street-only manual lead still gets a
    fully-qualified name), created inside **`Wholesaling > Info & Photos`**
    (`INFO_PHOTOS_FOLDER_ID`, config-overridable via `lead_photos_folder_id`) —
    directly, NOT in that folder's legacy `Photos` subfolder (Lance's call). On
    creation it gets `type=anyone, role=reader` so the link works with no Google
    login; files inherit it, so sharing is set exactly once.
  - **Adoption matches folders only.** `Info & Photos` also holds loose
    "… Property Info" Google Docs whose names contain the same addresses, so
    `_find_existing_folder` filters `mimeType=folder` and compares a normalized
    name (case/punctuation-insensitive, trailing "Photos" dropped — the folders
    already there are inconsistent about that suffix).
  - **No new Google grant was needed** — the existing `crm-underwriting@` SA is
    already a full member of the Wholesaling drive (see the Underwriting entry).
    `photos.py` reuses `underwriting._google_access_token` rather than repeating
    the JWT dance.
  - **Uploads are one HTTP request per file** (resumable Drive upload: metadata
    POST → session URI → PUT bytes). A 40-photo phone batch is hundreds of MB and
    a single giant POST is exactly what trips nginx's body limit and loses the
    whole batch instead of 39-of-40.
  - **Download-all zips server-side** because Drive has no zip-a-folder API and
    its web-UI download needs a Google session — the thing link-sharing exists to
    avoid. Capped at `MAX_ZIP_BYTES` (400 MB), over which the user is pointed at
    the Drive link. Duplicate filenames (legal in Drive, not in a zip) are
    de-duped.
  - Thumbnails render via `drive.google.com/thumbnail?id=…&sz=w400` (works
    unauthenticated precisely because of the link share); Drive's own
    `thumbnailLink` is short-lived and referrer-blocked. Videos show a play tile
    that opens Drive rather than trying to stream inline.
  - `crm/api/photos.py` (**new**: `get_lead_photos` / `ensure_photo_folder` /
    `upload_lead_photo` / `delete_lead_photo` / `download_all_photos`; realtime
    `crm_photos`, site-wide + `after_commit`), `frontend/src/components/PhotosCard.vue`
    (**new**), `frontend/src/components/Modals/PhotoGalleryModal.vue` (**new**),
    `frontend/src/pages/Lead.vue` (card + More-menu item + modal mount — mounted
    directly rather than threaded through the AllModals→Activities expose chain,
    since nothing else opens it). Deletes are `trashed=true` (recoverable), and
    only for files actually parented by that lead's folder.
  - **GOTCHA — `pages/MobileLead.vue` is a SEPARATE page**, not a responsive
    variant of `Lead.vue`: below 768px the router renders it instead, and it
    re-declares its own copy of the sidebar cards (First-Call Read / Tax Info /
    Agreements / Underwriting / InvestorLift) plus its own `AllModals` host
    (`detailModals`, since mobile tabs are mutually exclusive so `Activities`
    isn't always mounted). Adding a card to `Lead.vue` alone makes it **invisible
    on every phone** — which is exactly what shipped in gw245 and Lance caught.
    Any new sidebar card must be added in BOTH files. Note also that the header
    action row on mobile renders only Call + Text — there is no `More ▾` menu, so
    a More-menu entry is not a mobile-reachable entry point on its own.
    Mobile reaches the sidebar through a **Details** tab (first tab).
  - **Verifying phone layouts through the pi-chrome extension**: the extension
    pins the automation tab's viewport at desktop width, but
    `window.open(url, 'name', 'popup=yes,width=390,height=844')` gives a genuine
    390px viewport that `chrome_screenshot({targetId})` can capture — how the
    mobile gap above was found and the fix confirmed.
  - Ops (`../frappe-crm-deploy`): `scripts/setup_lead_photos.py` adds the CRM Lead
    `photo_folder_id` / `photo_folder_url` cache fields. They are a CACHE, not the
    source of truth — everything is `has_field`-guarded and falls back to
    resolving by address, so the feature works before the script runs; what they
    buy is that a later address edit doesn't orphan the folder.

- **DD Expiration date** (Leads) — `dd_expiration_date` (Date custom field)
  shown as a calendar-icon row in the Lead sidebar HEADER directly under the
  Acq Price row (same minimal no-label formatting): displays
  "7/16/26 (2 days left)" — a day-granular countdown, red once past / amber on
  the day (shared `ddExpiration()` helper in `frontend/src/utils/index.js`,
  reuses `dueColor`). Click opens the native date picker (hidden
  `<input type="date">` + `showPicker()`), hover ✕ clears. Also renderable as
  a **Leads Kanban card field** (selectable in KanbanSettings automatically
  since it's a real column; `dd_expiration_date` branches in `pages/Leads.vue`
  template + `parseRows` render the same countdown + color). Deliberately NOT
  in the side-panel Dispo section. `pages/Lead.vue` + `pages/Leads.vue` +
  `utils/index.js`; ops: field added via `setup_dispo_fields.py`.
- **Showing Access line** (Leads) — `showing_access` (Small Text custom field)
  rendered as a key-icon row in the Lead sidebar header directly under the DD
  expiration row: free-text access instructions ("vacant", "lockbox 4127, dogs
  in yard", …) in a borderless auto-growing textarea that wraps; Enter/blur
  saves, Esc reverts. Deliberately NOT in the side-panel Dispo section.
  `pages/Lead.vue` + `pages/MobileLead.vue`; ops: field added via
  `setup_dispo_fields.py`. Requested by Lance (Mattermost 2026-07-31).
- **Documenso "Create Purchase Agreement"** (Leads) — a header action (in the
  decluttered "More" menu next to the name) that spins up a pre-filled, editable
  Documenso e-sign draft of the wholesale purchase agreement and hands back a
  self-serve buyer signing link. A modal picks the agreement type (Standard vs
  Novation/+AIF vs Amendment) and seller count; two sellers reveals a Seller 2
  name/email (one seller drops the Seller 2 fields). Mirrors the BatchData
  Fetch-Tax-Info wiring. (E-sign moved Documenso → **DocuSeal Cloud Pro** 2026-06:
  backend is now app code `crm/api/agreement.py` `create_docuseal_agreement`,
  templates resolved by NAME, newest id wins — see the `docuseal-migration-project`
  memory.) The resolver considers ONLY the DocuSeal **"Purchase Agreements"
  folder** (`TEMPLATE_FOLDER`) — the team builds one-off templates in the UI for
  specific deals (they land in Default, with default First Party/Second Party
  roles) and a one-off named "Amendment 17199 Hamburg Detroit" once won the
  newest-name match and 422'd every CRM amendment (gw152). Keep canonical
  templates in that folder and deal one-offs out of it. Prefill "Property
  Address" is composed by `_full_property_address()` — street + city + state +
  zip, each component appended only if not already inside `property_address`
  (webhook leads carry the full string; manually-entered leads are street-only
  with separate city/state/zip fields, which used to put just "123 Main St" on
  agreements).
  - **The Standard PSA body is rebuilt from a Google Doc, not edited in
    DocuSeal** (latest: 2026-09-01, doc `1dFP9Rmh…`, templates **5678345**
    one-seller / **5678348** two-sellers). `tmp/build_psa_templates_v2.py` is the
    whole pipeline — export the doc to PDF, locate the underscore blanks with
    `tmp/find_blanks.py`, upload untagged → set roles → PUT fields at those
    bboxes → clone (dodges the values-prefill 500) → values-test → archive the
    build original. `tmp/preview_psa_fields.py` draws the boxes onto the PDF so
    placement is checked BEFORE anything is created on DocuSeal; the locator
    reproduces the previous build's hardcoded coordinates exactly, so it is
    trustworthy.
    - **The old templates are deliberately left UNARCHIVED.** The resolver takes
      the highest non-archived id per name, so the new build wins for new
      agreements while every in-flight submission keeps rendering as signed.
      That also means the swap goes live **the moment the templates exist — no
      deploy** (`_resolve_template_ids` hits the DocuSeal API on every create).
    - **GOTCHA — a field area MUST carry `attachment_uuid`**, even on a
      one-document template. Without it the PUT returns 200, `GET` lists every
      field with correct coordinates, and a `values` prefill is accepted and
      stored — but the signing page renders **no fields at all**, i.e. an
      unfillable blank form, while every API response looks perfect. Take it
      from `documents[0].uuid`. The Aug-27 script omitted it and only got away
      with it because opening a template in the DocuSeal UI backfills the value.
    - **v2's wording changed the terms, so two NEW blanks exist**: `Closing Days`
      ("we close within ___ calendar days **after the inspection period ends**" —
      the old text hardcoded 30 days from execution, a different clock) and
      `Offer Expires Days`. Inspection is now **calendar** days, not business
      days. Both new fields are required and deliberately NOT prefilled — no safe
      default exists, and `required` means the rep must fill them before they can
      sign. `Sale Price` was never prefilled either.
  - **Amendment type (2026-07-14)** — "Amendment (price / closing date)" in the
    type dropdown creates an Amendment-to-PSA envelope from the DocuSeal
    templates `Amendment - One Seller` / `- Two Sellers` (ids 4996712/4996713,
    built from Lance's Desktop DOCX via `POST /templates/docx` UNTAGGED + field
    areas computed from the rendered PDF's underscore blanks and `PUT` back —
    text-tags reflowed the layout, don't use them; area `page` is 0-indexed on
    write despite the QUIRKS note). GOTCHA (bit us again): the freshly-built
    templates 500'd on ANY `values` prefill — the known DocuSeal glitch; the fix
    is CLONE the template (clone regenerates fields cleanly, accepts values) and
    archive the original, hence the final ids. Buyer role prefills only
    `Seller Name(s)` + `Property Address`; Binding Agreement Date, amended price
    and amended closing date are left for the buyer on the signing page. Seller
    name fields (`Seller Name` / `Seller 1 Name` / `Seller 2 Name`) match the
    existing `_seller_values` superset so seller prefill just works.
    `crm/api/agreement.py` (`want_amendment` + resolver branch) +
    `CreateAgreementModal.vue` (third type option).
    - **Rebuilt 2026-09-07 (ids 5758621 one / 5758624 two)**, Lance's asks off
      lead 01194: buyer reads **Groundwork Ventures Inc.** everywhere (the DOCX
      said "Inc" up top and ", LLC" at the signature block); each amendment is
      **optional behind a checkbox** — price, closing date, or a two-line
      **Other** (`Amend Purchase Price` / `Amend Closing Date` / `Amend Other`
      checkbox fields + `Other Amendments` text); and the Buyer block gained
      **Printed name / Title** with the PSA's field names (`Signer Name` req,
      `Title` opt) so the CRM prefills the rep's name. **Prefilled as a
      template off the lead** (Lance, same day): Binding Agreement Date =
      the date the lead's newest completed PSA went fully signed (DocuSeal
      `completed_at`, fallback `last_event_at`; blank when no signed PSA),
      Amended Purchase Price = `acq_price` as `250,000`, Amended Closing Date
      = `closing_date` — i.e. the CURRENT terms, so the rep edits one number;
      checkboxes stay unticked. `_binding_agreement_date` never raises. The PDF is now DRAWN by
      `tmp/build_amendment_templates_v2.py` (PyMuPDF, no DOCX) so the blank
      bboxes are known at draw time; `--pdf-only` writes the PDFs + a red-box
      field preview PNG. Old 4996712/4996713 left unarchived (in-flight
      submissions; resolver takes the newest id). Verified on the live
      signing page for both variants before the test submissions were archived.
  - **Cancellation type (2026-07-15)** — "Cancellation / release of earnest
    money" in the type dropdown creates a Cancellation-of-Contract + Release-of-EMD
    envelope from DocuSeal templates `Cancellation - One Seller` /
    `- Two Sellers` (ids 5014844/5014850, built via `POST /templates/pdf` from
    Lance's Desktop "Cancellation of Contract - Release of EMD.pdf" UNTAGGED +
    `PUT` fields at coordinates computed from the PDF's rule lines with PyMuPDF;
    cloned + originals archived per the values-prefill-500 gotcha). Buyer role
    prefills only `Seller Name(s)` + `Property Address`; contract date, buyer
    name(s), escrow agent, EM amount and the 4 disbursement rows are buyer-filled
    on the signing page. Each signer has ONE signature + date field with areas in
    BOTH sections (Cancellation block + Release block) — sign once, lands on
    both. PDF quirk: disbursement rows 2-4 exist in the form's text layer but
    render invisibly — filled values still print there, just without visible
    $/to/Address labels (row 1 is the normal case).
    `crm/api/agreement.py` (`want_cancellation` + resolver branch) +
    `CreateAgreementModal.vue` (fourth type option).
  - **Unilateral termination / no EMD (2026-08-18)** — the fifth agreement type
    creates a buyer-only notice from DocuSeal template
    `Unilateral Termination - No EMD` (id 5473548, canonical in Purchase
    Agreements). It uses the approved one-page Times New Roman 12pt PDF and has
    ONE `Buyer` submitter — the logged-in company representative — with no seller
    signer or seller link. Notice date, property owner (one field with areas on
    both the top line and salutation), phone, property address, and representative
    name are prefilled; contract date and representative title stay editable;
    signature is required. The API-built source was cloned before use and a
    values-prefill submission was verified, then archived. The modal hides all
    seller email/count controls for this type, and tracking surfaces label the
    internal URL as the company-representative link rather than a buyer link.
    `crm/api/agreement.py` (`want_termination` + buyer-only submitters) +
    `CreateAgreementModal.vue` + `AgreementsCard.vue` + `Activities.vue`.
  - `components/Modals/CreateAgreementModal.vue` (chooser + success view: buyer
    link with copy/open + seller links), mounted in `Activities/AllModals.vue`
    (`createAgreement()` + expose), forwarded by `Activities/Activities.vue`.
  - `pages/Lead.vue` — **button row decluttered** into Call · Text · **More ▾**
    (Dropdown via a `moreActions` computed: Make-a-Call / Email / Website /
    Attach / Fetch Tax Info / Create Agreement) · Delete. (Email +
    Website moved off the row into the menu per Lance — "that area is getting
    unruly".) `MoneyIcon`/`Email2Icon`/`LinkIcon`/`AttachmentIcon` imports now
    unused but harmless.
  - **Backend = ops server script** `create_agreement_draft.py` (api_method
    `create-agreement-draft`, token via `__INFISICAL:DOCUMENSO_API_TOKEN_ACQ__`):
    resolve template by title → `/template/use` w/ prefill → delete Seller 2 for
    one-seller → `/document/distribute` distributionMethod NONE (no emails). See
    `../frappe-crm-deploy` + the `documenso-deployment` memory.
  - Modal also: asks **Seller 1 email** when the lead has none (optional); creates
    a **Contact attached to the lead** when a Seller 2 is entered (email optional).
- **E-sign agreement tracking + live status** — each draft is recorded as a
  **CRM Esign Agreement** row (ops doctype) shown in a sidebar card +
  Activity-timeline entry, with status that updates live as recipients sign
  (Documenso webhook → ops `documenso-webhook` server script → CRM Esign Agreement
  on_update APP hook → `crm_esign` realtime). Mirrors the tax-info realtime/card/
  timeline pattern exactly.
  - `crm/api/agreement.py` — `on_agreement_insert`/`on_agreement_update`
    (stamp last_event_at + publish `crm_esign`, the sandbox can't) + `get_agreements`
  - `crm/hooks.py` — CRM Esign Agreement after_insert + on_update
  - `frontend/src/components/AgreementsCard.vue` (sidebar: status badge + signed
    count + buyer/seller links), mounted in `pages/Lead.vue` after `<TaxInfoCard>`
  - `frontend/src/components/Activities/Activities.vue` — `agreement` timeline
    type (`get_agreements` resource + `crm_esign` listener + DetailsIcon)
  - Documenso webhooks are DB-managed (Webhook table, not the public API); a row
    for teamId 4 points at `/api/method/documenso-webhook?secret=…`. Details in
    `../frappe-crm-deploy` + the `documenso-deployment` memory.
  - **"Download signed PDF" once completed** — when an agreement is fully signed
    (Documenso status `COMPLETED`, or `signed_count >= total_signers` — some rows
    never flip to COMPLETED in the webhook but reach 2/2), a green **Download
    signed PDF** link shows in the sidebar card AND the Activity-timeline entry,
    streaming the signed PDF. **Backend proxy** because Documenso's `download-beta`
    returns an internal, expiring MinIO presigned URL (`http://minio:9000/…`) a
    browser can't reach: `crm/api/agreement.py` `download_signed_agreement(agreement)`
    (whitelisted, GET) permission-checks via the lead, then `requests.get` the
    Documenso `/api/v2/document/{document_id}/download?version=signed` with the API
    token and sets `frappe.local.response` (`type="download"`, `filecontent`,
    `filename=<template>_signed.pdf`). `get_agreements` now returns an `is_signed`
    flag (`_is_completed`) the UI gates the link on. **Token is in site_config**
    `documenso_api_token` (set via `bench set-config`, mirrors the underwriting
    `google_sa_json` pattern — app code can't read the server-scripts'
    `__INFISICAL:…__` placeholder). **Open signed PDF is a public URL**
    (`crm.api.agreement.public_signed_pdf`, `allow_guest`): HMAC of the agreement
    name (secret = `agreement_pdf_secret` else site `encryption_key`), so the
    opened tab is a link anyone can open — title company, listing agent — with
    no CRM session. `get_agreements` / `get_buyer_agreements` return
    `signed_pdf_url`; the card has a copy button next to Open. Logged-in
    `download_signed_agreement` remains as fallback. Tests:
    `unit_test_agreement_pdf.py`.
  - **Adopting hand-built DocuSeal envelopes** — the team builds one-off templates
    directly in the DocuSeal UI (deal-specific novations / amendments / AIFs) and
    sends them by SMS. Those envelopes had no `CRM Esign Agreement` row, so
    `docuseal_webhook` looked up `document_id`, found nothing and returned early —
    a contract could be **fully signed with no trace on the lead** (that's how the
    6787 N 200 E / Zurek purchase agreement went missing). DocuSeal webhooks are
    **account-wide**, so the events already reached us; we were just dropping them.
    `crm/api/agreement_adopt.py` (**new**) adds the missing branch: on an unknown
    submission it matches back to a lead by **phone** (last-10, format-insensitive —
    the primary key, since texted links mean every party has `email: None`), then
    **email**, using **address** (street number + words, from the template name /
    "Property Address" field) ONLY to break a multi-lead tie, never to link on its
    own. Internal parties (our own users by login email, `custom_quo_number`, or
    the site-config `docuseal_internal_numbers` list) are excluded first, or the
    rep who signs every envelope would match whichever lead holds their number.
    Anything not resolving to exactly one lead is **never guessed**: `_alert_unmatched`
    emails/texts Lance once per submission with the `attach_submission` command to
    link it by hand (`CRM Esign Agreement.lead` is `reqd`, so a row can't exist
    without a confident match). Adopted rows are stamped `source=adopted` +
    `match_basis` and re-owned to the lead owner. `backfill_adoptions(dry_run=1)`
    sweeps all history (`docuseal_adopt_since`, default 2026-07-01, skips the
    account's throwaway test envelopes). 5 rows adopted on prod so far.
  - **Archiving is now soft** — `archive_agreement` sets `is_archived` instead of
    `frappe.delete_doc`: deleting lost the record entirely (one click could vaporize
    a signed contract) and, with adoption live, a later webhook event would just
    recreate the row it removed. The flagged row is both recoverable and a
    tombstone. Reads filter it out via a shared `_live_filter()`; `_agreement_fields()`
    centralizes the `has_column` guards (`provider`/`source`/`match_basis`) so an
    unmigrated site still works. `AgreementsCard.vue` shows an amber **"Auto-linked
    from DocuSeal"** badge (match evidence in the `title` tooltip) so an adopted
    envelope is never mistaken for one the CRM sent, and the archive confirm now
    says the record is kept.
  - Ops (`../frappe-crm-deploy`): `scripts/setup_agreement.py` adds `source`
    (Select `crm\nadopted`), `match_basis` (Small Text) and `is_archived` (Check)
    via `ensure_field` (idempotent). All three are live on prod.
- **Signed contracts parse themselves into the lead (Gemini, in-app)** — when
  a DocuSeal envelope goes fully signed, `docuseal_webhook` enqueues
  `contract_parse.parse_agreement` on the `long` queue; it fetches the signed
  PDF from DocuSeal, sends **the PDF itself** to Gemini (`gemini-pro-latest`,
  override `contract_parse_model`; same `gemini_api_key` as call review) with
  a JSON `responseSchema` derived from `PARSE_FIELDS`, and writes `acq_price` /
  `dd_expiration_date` / `closing_date` (+ the property address if the contract
  corrects it) onto the lead. **Hourly `catch_up_unparsed`** re-enqueues
  anything signed in the last 2 days with no `parsed_at` (needs `sync_jobs`).
  `parse_now(agreement, force=1, dry_run=1)` is the bench/manual entry —
  dry-run shows what WOULD be written. ~15s per contract.
  - **Moved in-app 2026-09-07 (gw458).** For a month this ran as a `pi`
    listener on the Mac mini behind Tailscale Funnel (`mini/contract-parser/`
    in the ops repo, launchd `com.groundwork.contract-parser`). It
    crash-looped for days after the ops checkout moved ("can't open file
    …listener.py") and a PSA signed 09:55 was still unparsed at 13:00. The CRM
    already owned the field map, validation and write path — only the model
    call lived on the mini. Verified before switching: Gemini matched pi to
    the dollar and the day on lead 01194's PSA. `notify_mini` survives as the
    fallback ONLY when `gemini_api_key` is unset; the launchd agent was
    unloaded. GOTCHA: `gemini-2.5-pro` now **404s** ("no longer available to
    new users") — hence the rolling `-latest` alias rather than a pinned name.
  - **PDF goes in as inline data**, not extracted text — no poppler in the
    container, and scanned/hand-built (adopted) envelopes still parse. The
    prompt says the document is untrusted data; the model has no tools, and
    `write_agreement_fields` still validates every value and rejects unknown
    field names. The job runs as Administrator (the webhook enqueues as Guest).
  - **Writes go through `doc.save()`, not `db.set_value`** — deliberately the
    opposite of the tax-pull/first-call pattern. The Version row is the point:
    it renders as "changed Acq Price from … to …" on the activity timeline,
    which is the entire audit trail here.
  - **GOTCHA — `property_address` is only written when the house number or
    street NAME differs** (`_address_really_differs`). The model returns
    street-only while the CRM holds the full string, and on the Aug-31
    backfill it overwrote "16 S 5th St W, Aurora, MN 55705" with "16 S 5th
    St" — a dry run minutes earlier had NOT returned the address (the model
    is not deterministic across runs, so a dry run is a preview, not a proof).
  - **Backfill 2026-09-07 of the 9 older unparsed envelopes**: 00070 closing
    08-15→09-30 (amendment), 00034 Aurora closing 08-21→08-31; three
    terminations + two hand-built amendments wrote nothing and were stamped.
    Two prices were HELD: the "8-31 Amendment" read **135,000** off the
    settlement statement — that is the novation SALE price (`dispo_price`),
    not acq — and the "Aurora Addendum and New Novation Agreement" read
    **101,000** (unstamped, awaiting Lance). The assignment/novation trap
    is alive and well; a settlement statement inside an envelope is the new
    shape of it.
  - **GOTCHA — the catch-up sweep is an outage bridge, NOT an importer.** It
    first shipped at 30 days keyed on `creation`; on install that swept every
    signed contract in history and began rewriting live leads (14 queued before
    it was stopped — it got through one cancellation, which correctly wrote
    nothing). Now 2 days, keyed on **`last_event_at`** (a contract sent last
    week and signed during an outage must still be caught, so `creation` was
    the wrong clock). Backfill is opt-in via a larger `days`.
  - **GOTCHA — business-day arithmetic.** DD periods are usually "N business
    days from the effective date", and holiday handling silently moves the
    deadline a day (Labor Day did exactly this in testing). The convention is
    now pinned in the prompt (weekends **and** US federal holidays excluded;
    effective date = last signature = day 0) and the model returns a `basis`
    explaining each date, stored in `parse_result`.
  - **Cancellations are deliberately not parsed** — the prompt returns nulls for
    them; assignments return `acq_price` null; amendments only what they amend. Clearing real fields on an LLM read of a cancellation is the one
    failure here that loses data instead of just being wrong. Verified live.
  - **GOTCHA — backfilling: preview first, and mind ASSIGNMENT documents.** An
    Assignment Agreement's price is what the BUYER pays US, not our acq price;
    on the Phoenix deal a backfill would have overwritten `acq_price`
    **180,000 → 190,000** (the assignment spread) on an active Buyer-Assigned
    lead. AIFs/POAs and novations correctly return `{}`. Also, a lead often has
    SEVERAL agreements (purchase → amendment → novation → AIF): submit oldest
    first so later terms win, or a superseded original clobbers the amended
    price. The 2026-08-01 backfill of dispo-active deals therefore ran as
    preview → human review → apply-exact-reviewed-values (no second inference),
    under a **fill-blanks-never-overwrite** rule; 7 fields across 5 leads, 4
    agreements held back because the document disagreed with the CRM (two were
    simply superseded originals the CRM already reflected).
  - `crm/api/contract_parse.py` (`PARSE_FIELDS` — the single source of truth:
    prompt, schema and validation derive from it — plus `trigger_parse` /
    `enqueue_parse` / `catch_up_unparsed` / `parse_agreement` / `parse_now` /
    `get_agreement_for_parse` / `list_unparsed_agreements` /
    `write_agreement_fields` / `mark_parse_failed`) + the trigger call in
    `agreement.py::docuseal_webhook` + the `hourly_long` hook.
  - Ops (`../frappe-crm-deploy`): `setup_agreement.py` owns `parsed_at` /
    `parse_status` / `parse_result` (`parsed_at` = the idempotency key, so a
    re-delivered webhook can't clobber a human's correction; an amendment is
    its own row and so correctly overwrites). A failed parse is stamped too,
    so a bad PDF is not re-billed hourly — `force=1` is the deliberate retry.
- **First-Call Read (2x2 lead qualification)** — after the first call a rep marks
  two yes/no reads that place the lead in a 2x2: **Motivated?** (is the seller
  motivated) x **On price?** (is their price realistic) → Motivated·On price /
  Motivated·Off price / Not motivated·On price / Not motivated·Off price; blank on
  either axis = "Not qualified yet". Drives the team to answer it on every initial
  call. Three-state (unset/Yes/No) so "not asked" is distinct from "No".
  - **Lead-page sidebar card** (top of the sidebar, above Tax Info): plain-English
    questions with big Yes/No buttons (green Yes / red No), a mini 2x2 grid that
    lights up the cell the lead lands in — columns are **Off price | On price**
    so the best read (motivated + on price) sits **top-right**, the corner the
    eye treats as "best" (gw303; it was top-left until then), and a color-coded guidance band (per-
    quadrant next-action sentence) + "Set by X · date" stamp. Tap the active
    choice again to clear it.
    `frontend/src/components/FirstCallReadCard.vue` (new), mounted in
    `pages/Lead.vue` before `<TaxInfoCard>` (`@saved="document.reload()"`).
  - **Kanban quadrant chip** (Leads) — a subtle themed Badge on each card (green/
    orange/blue/red) showing the quadrant label, only when BOTH axes are answered.
    Server pseudo-field `_first_call` ("motivated|on_price", e.g. "Yes|No") computed
    in `crm/api/doc.py` getCounts (guarded by `has_column`, CRM Lead only; excluded
    from the DB `rows` SELECT like `_next_task_due`); added to default
    `kanban_fields` in `crm_lead.py`; selectable in `Kanban/KanbanSettings.vue`;
    rendered in the `#fields` slot of `pages/Leads.vue` (`parseRows` → label/theme
    via the shared `firstCallRead()` helper in `utils/index.js`). Realtime
    `crm_first_call` (site-wide, after_commit) → `reloadKanban()` on the same
    on-board guard as `crm_task_update`. (Deals not wired — same backend extends.)
  - **Backend = app code** (not a server script): `crm/api/first_call.py`
    `set_first_call_read(lead, motivated, on_price)` — validates ''/Yes/No, writes
    all four fields via `db.set_value` (no full doc.save → no status/assign side-
    effects), stamps `first_call_by`/`first_call_at` server-side, publishes
    `crm_first_call`.
  - Ops (`../frappe-crm-deploy`): `scripts/setup_first_call_read.py` adds the four
    CRM Lead custom fields (`first_call_motivated`/`first_call_on_price` Select
    `\nYes\nNo`; `first_call_by` Link User read_only; `first_call_at` Datetime
    read_only) — kept OUT of the side-panel layout (the card renders them). The
    chip was also added to the saved Leads kanban views (CRM View Settings rows 3
    + 4, after `_next_task_due`, `label:""`) so it shows without each user re-
    adding it.
- **Underwriting workbook (Google Sheets) "Create Underwriting Sheet"** (Leads) —
  a header **More ▾** action + sidebar **Underwriting** card that copies the
  underwriting template Google Sheet into the shared "Underwriting" Shared Drive
  folder, renames it to the lead's `property_address`, pre-fills the **ARV** tab
  (B4=today's date, B5=the clicking user's full name, B9=`zillow.com/homes/<addr>_rb/`),
  records a **CRM Underwriting Workbook** row, and surfaces the link in the sidebar
  card + Activity timeline so the team can open underwriting later. **One workbook
  per lead** — re-clicking just re-opens the existing sheet. Live refresh via the
  `crm_underwriting` realtime event (site-wide, `after_commit`). Mirrors the
  tax-info/agreement card+timeline+realtime pattern.
  - **Google call lives in APP CODE, not a server script** — a Frappe server-script
    sandbox can't sign the OAuth2 JWT to mint a Google token. `crm/api/underwriting.py`
    `create_underwriting_workbook(lead)` mints a token with **PyJWT (RS256) + a
    `requests` token POST** (no Google client libs — already in the Frappe env),
    does the Drive `files.copy` (supportsAllDrives) + Sheets `values:batchUpdate`,
    inserts the doctype row, returns the URL. `get_underwriting_workbooks(lead)` is
    the read API; `on_workbook_insert` hook mirrors the URL onto the lead
    (`underwriting_url`, has_field-guarded — field not required) + publishes
    `crm_underwriting`. `crm/hooks.py` — `CRM Underwriting Workbook` `after_insert`.
  - **Credentials**: a dedicated Google service account
    `crm-underwriting@claude-code-486305.iam.gserviceaccount.com` (key in
    `~/.config/gcloud/crm-underwriting-key.json`). It is **NOT** domain-wide
    delegated. **Its actual reach is much broader than this doc long claimed**
    (measured 2026-07-29): it is a full member of **8 shared drives** — Wholesaling,
    Creative Finance, Entitlement, Hiring, Lux, RDF, Swipe, Training — with
    `canAddChildren`/`canEdit`/`canShare`/`canDeleteChildren` all true on
    Wholesaling, not "Content Manager of only the Underwriting folder". The
    Underwriting folder simply sits at the root of the Wholesaling drive. That
    breadth is what let the lead-photos feature ship with no new grant; it is also
    worth remembering that this key lives in prod's site config, so a prod
    compromise reaches all 8 drives. It **acts as itself** (no `sub` claim): `_google_access_token()` omits `sub` when `google_workspace_subject`
    is "" (present-but-empty). The key + empty subject are read from site config
    (`google_sa_json` / `google_workspace_subject`), the `frappe.conf.get` route
    `live_demo.py` uses for `demo_password`. (Legacy: an absent
    `google_workspace_subject` falls back to DWD impersonating
    `lance.johnson@groundworkpro.com` — the original broad `workspace-admin` SA.)
    Template id + folder id are module constants in `underwriting.py`
    (config-overridable: `underwriting_folder_id`). Files created in the Shared
    Drive are owned by the drive, not the SA, so the SA's zero storage quota is a
    non-issue.
  - `frontend/src/components/UnderwritingCard.vue` (sidebar) +
    `Modals/CreateUnderwritingModal.vue` (confirm → progress → success/open link;
    auto-detects an existing sheet on open) + `Activities/AllModals.vue`
    (`createUnderwriting()` + expose) + `Activities/Activities.vue` (`underwriting`
    timeline type + `underwritingWorkbooks` resource + `crm_underwriting` listener +
    icon + **forwards `createUnderwriting` through its own `defineExpose`** — easy to
    miss; the card→page→Activities→AllModals chain needs every hop) + `pages/Lead.vue`
    (card mount + More-menu item, both guarded by `property_address`).
  - Ops (`../frappe-crm-deploy`): `scripts/setup_underwriting_workbook.py` creates
    the `CRM Underwriting Workbook` doctype (fields `lead`/`address`/`sheet_id`/
    `sheet_url`/`created_by_user`/`workbook_created_at`; sales-role perms; autoname
    hash). The SA JSON was delivered into the backend `site_config` via `bench
    set-config` (not a script in the repo).

- **Frappe "assigned to you" emails are OFF by default** (2026-09-08, Lance —
  a 41-lead handover mailed each receiver 41 times). `Notification Settings`
  `before_insert` hook (`notification_prefs.default_assignment_emails_off`)
  zeroes `enable_email_assignment` for new users; existing rows were swept
  with `disable_assignment_emails(dry_run=0)`. Per-user re-enable lives at
  `/app/notification-settings/<user>`. In-app CRM notifications, @-mention
  emails and agreement alerts are untouched.
- **Agreement activity notifications (text + email to the lead owner)** — when a
  DocuSeal agreement is **viewed / started / signed**, the **lead owner** is
  notified by text (from the dedicated "Notifications" Quo line **(952) 395-3833**,
  to their own Quo number `User.custom_quo_number` by default) and by email. Fires
  on **every** DocuSeal event (no dedupe — Lance's call). Each user controls it on
  a new **Settings → User Configuration → Notifications** page: Text/Email channel
  toggles, a "Text me at" number override, and per-event checkboxes (Viewed /
  Started / Signed). Opt-out (everything on by default).
  - **Trigger = the existing DocuSeal webhook**, not a doc hook: `crm/api/agreement.py`
    `docuseal_webhook` calls `crm.api.agreement_notify.notify_event(agr, event, data)`
    after saving each row (it has the exact `event_type` + submitter `data` there).
    Wrapped in try/except so a notify failure never breaks the webhook's 200.
  - `crm/api/agreement_notify.py` — **new**: `notify_event` maps
    `form.viewed`/`form.started`/`form.completed`/`submission.completed` →
    viewed/started/signed, resolves recipient = `CRM Lead.lead_owner` (fallback the
    agreement creator), reads prefs, and sends. **Text goes straight to the
    OpenPhone API** (`requests` to `/v1/messages`, key from site_config `quo_api_key`,
    from `notifications_quo_number` default `+19523953833`) — it does NOT go through
    the ops `send-text` script (that runs as the session user / role-gated; the
    webhook is Guest) and does NOT create a `Quo Message` row (it's a rep alert, not
    a lead-thread text). Email via `frappe.sendmail` + `crm_agreement_notification`
    template.
  - `crm/api/notification_prefs.py` — **new**: `set_notification_prefs` (session
    user's own JSON, mirrors `set_user_quick_comments`) + `DEFAULT_PREFS` + `get_prefs`.
    Stored on `User.custom_notification_prefs`; surfaced via `crm/api/session.py`
    `get_users`.
  - `frontend/src/components/Settings/NotificationsSettings.vue` (**new**) +
    registered in `Settings/Settings.vue` under User Configuration.
  - Ops (`../frappe-crm-deploy`): `scripts/setup_notification_prefs.py` adds the
    `User.custom_notification_prefs` Long Text field; **`bench set-config quo_api_key
    <Infisical QUO_API_KEY>`** on prod so app code can send the texts (the DocuSeal
    webhook already subscribes to all the needed events). Verified live end-to-end
    (gw123): viewing a seller link texted Lance "Notify Test viewed the Purchase
    Agreement…" from 3833.

The companion server-side pieces (custom doctypes, scheduler engine, webhook
endpoints) are Server Scripts managed from the ops repo, NOT app code here. SMS
specifically: the `Quo Message` doctype, the `send-text` and `list-quo-numbers`
API server scripts, and inbound text mirroring in the `sequence-events` webhook
all live in `../frappe-crm-deploy`.

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
checkout, no image build. `push_deploy.sh` ships HEAD (clean tree required;
a push that would drop live commits is rejected by git), then rebuilds the
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
- **PUSH BEFORE YOU DEPLOY, PULL BEFORE YOU BUILD — this is the one that bites
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
