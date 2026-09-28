app_name = "crm"
app_title = "Frappe CRM"
app_publisher = "Frappe Technologies Pvt. Ltd."
app_description = "Kick-ass Open Source CRM"
app_email = "shariq@frappe.io"
app_license = "AGPLv3"
app_icon_url = "/assets/crm/images/logo.svg"
app_icon_title = "CRM"
app_icon_route = "/crm"

# Apps
# ------------------

# required_apps = []
add_to_apps_screen = [
	{
		"name": "crm",
		"logo": "/assets/crm/images/logo.svg",
		"title": "CRM",
		"route": "/crm",
		"has_permission": "crm.api.check_app_permission",
	}
]

get_site_info = "crm.activation.get_site_info"

# Cache
# ------------------

#: Cache prefixes that must SURVIVE `bench clear-cache`.
#:
#: `build_image.sh` ends every deploy in `clear-cache`, and Frappe's
#: `clear_cache()` deletes *every* key for the site except the prefixes listed
#: here. That is right for state derived from our code -- hooks, doctype meta,
#: server scripts and rendered website pages all go stale the moment a build
#: ships. It is wrong for these: they are PAID third-party responses, and
#: Zillow's answer about a house does not change because we deployed.
#:
#: Measured on prod: a deploy was taking **175 area circles + 22 pin lookups**
#: with it -- ~300 RapidAPI calls, on a key shared with istl-buyer's ZIP job --
#: which the Today-board prewarm then spent the next ~40 minutes buying back.
#: On a day with several deploys that was the single largest consumer of the
#: plan, and it bought nothing: the data thrown away and the data re-fetched
#: were identical.
#:
#: CONSEQUENCE, and it is why this is a deliberate trade rather than a free win:
#: the deploy wipe was invalidating these caches by ACCIDENT. With it gone, the
#: version numbers already baked into the keys -- `AREA_CACHE_VERSION`,
#: `PIN_CACHE_VERSION`, `DETAIL_CACHE_VERSION` -- are the ONLY invalidation
#: path. That is the mechanism they exist for, and it is surgical where the wipe
#: was blanket, but it means anyone changing the SHAPE of a cached row has to
#: bump the matching version constant. A deploy no longer papers over forgetting.
#:
#: TTLs are unaffected (area 7d, pin 30d, detail 30d), so nothing lives forever.
persistent_cache_keys = [
	"zillow_area",  # circle searches -- ~3.3 calls each, the expensive ones
	"zillow_pin",  # per-pin /property
	"crm:comp-detail",  # on-click property + photos
	"zillow_quota_remaining",  # else the quota guard is blind after every deploy
	"redfin_subject",  # Zillow-vs-Redfin subject cross-checks -- ~5s each to rebuild
	"crm:realtor-estimate",  # Realtor AVM per lead -- two BILLED Apivex calls each
	"crm:photo-exif",  # JPEG DateTimeOriginal per gallery URL -- download is the cost
]

export_python_type_annotations = True
require_type_annotated_api_methods = True

# Includes in <head>
# ------------------

# include js, css files in header of desk.html
# app_include_css = "/assets/crm/css/crm.css"
# app_include_js = "/assets/crm/js/crm.js"

# include js, css files in header of web template
# web_include_css = "/assets/crm/css/crm.css"
# web_include_js = "/assets/crm/js/crm.js"

# include custom scss in every website theme (without file extension ".scss")
# website_theme_scss = "crm/public/scss/website"

# include js, css files in header of web form
# webform_include_js = {"doctype": "public/js/doctype.js"}
# webform_include_css = {"doctype": "public/css/doctype.css"}

# include js in page
# page_js = {"page" : "public/js/file.js"}

# include js in doctype views
# doctype_js = {"doctype" : "public/js/doctype.js"}
# doctype_list_js = {"doctype" : "public/js/doctype_list.js"}
# doctype_tree_js = {"doctype" : "public/js/doctype_tree.js"}
# doctype_calendar_js = {"doctype" : "public/js/doctype_calendar.js"}

# Home Pages
# ----------

# application home page (will override Website Settings)
# home_page = "login"

# website user home page (by Role)
# role_home_page = {
# "Role": "home_page"
# }

website_route_rules = [
	{"from_route": "/crm/<path:app_path>", "to_route": "crm"},
]

# Generators
# ----------

# automatically create page for each record of this doctype
# website_generators = ["Web Page"]

# Jinja
# ----------

# add methods and filters to jinja environment
# jinja = {
# "methods": "crm.utils.jinja_methods",
# "filters": "crm.utils.jinja_filters"
# }

# Setup wizard
# setup_wizard_requires = "assets/crm/js/setup_wizard.js"
# setup_wizard_stages = "crm.setup.setup_wizard.setup_wizard.get_setup_stages"
setup_wizard_complete = "crm.demo.api.create_demo_data"
# setup_wizard_test = "crm.setup.setup_wizard.test_setup_wizard.run_setup_wizard_test"

# Installation
# ------------

before_install = "crm.install.before_install"
after_install = "crm.install.after_install"

# Uninstallation
# ------------

before_uninstall = "crm.uninstall.before_uninstall"
# after_uninstall = "crm.uninstall.after_uninstall"

# Integration Setup
# ------------------
# To set up dependencies/integrations with other apps
# Name of the app being installed is passed as an argument

# before_app_install = "crm.utils.before_app_install"
# after_app_install = "crm.utils.after_app_install"

# Integration Cleanup
# -------------------
# To clean up dependencies/integrations with other apps
# Name of the app being uninstalled is passed as an argument

# before_app_uninstall = "crm.utils.before_app_uninstall"
# after_app_uninstall = "crm.utils.after_app_uninstall"

# Desk Notifications
# ------------------
# See frappe.core.notifications.get_notification_config

# notification_config = "crm.notifications.get_notification_config"

# Permissions
# -----------
# Permissions evaluated in scripted ways

# permission_query_conditions = {
# "Event": "frappe.desk.doctype.event.event.get_permission_query_conditions",
# }
#
# has_permission = {
# "Event": "frappe.desk.doctype.event.event.has_permission",
# }

# DocType Class
# ---------------
# Override standard doctype classes

override_doctype_class = {
	"Contact": "crm.overrides.contact.CustomContact",
	"Email Template": "crm.overrides.email_template.CustomEmailTemplate",
}

# Document Events
# ---------------
# Hook on document methods and events

doc_events = {
	# Keep API keys out of the Error Log. Frappe already sanitises tracebacks, but
	# it matches its blocklist against variable NAMES and exact dict keys -- so a
	# variable called `headers` holding an `Authorization` key went straight
	# through, and 106 of 1,040 rows ended up carrying a live bearer token.
	# Hooked here rather than patched into apps/frappe because the image is rebuilt
	# FROM upstream, which would revert an edit there silently.
	"Error Log": {
		"before_insert": ["crm.api.error_redaction.on_error_log_insert"],
	},
	# The ops server scripts hold their credentials as module constants, and
	# `safe_exec` prints the WHOLE script source into any traceback raised inside
	# it -- so one failing script reprints its own key on every error. That was 839
	# of the leaked rows, and none of them contained the word "Authorization".
	"Scheduled Job Log": {
		"before_insert": ["crm.api.error_redaction.on_error_log_insert"],
	},
	"Deleted Document": {
		"before_insert": ["crm.api.error_redaction.on_error_log_insert"],
	},
	# Gated on ref_doctype inside the handler: Version rows are written on nearly
	# every save, so this must not put a regex in that path for every lead edit.
	"Version": {
		"before_insert": ["crm.api.error_redaction.on_version_insert"],
	},
	"Contact": {
		"validate": ["crm.api.contact.validate"],
	},
	"ToDo": {
		"after_insert": ["crm.api.todo.after_insert"],
		"on_update": ["crm.api.todo.on_update"],
	},
	# A rep's hand-made task due after today pauses the lead's sequences and
	# cancels the engine's open tasks on it. See crm/api/sequence_booking.py.
	"CRM Task": {
		"after_insert": ["crm.api.sequence_booking.on_task_change"],
		"on_update": ["crm.api.sequence_booking.on_task_change"],
	},
	"Communication": {
		"after_insert": ["crm.utils.on_communication_insert"],
		"on_update": ["crm.utils.on_communication_update"],
	},
	"Comment": {
		"after_insert": ["crm.utils.on_comment_insert"],
		"on_update": ["crm.api.comment.on_update"],
	},
	"WhatsApp Message": {
		"validate": ["crm.api.whatsapp.validate"],
		"on_update": ["crm.api.whatsapp.on_update"],
	},
	"CRM Deal": {
		"on_update": [
			"crm.fcrm.doctype.erpnext_crm_settings.erpnext_crm_settings.create_customer_in_erpnext"
		],
	},
	"User": {
		"before_validate": ["crm.api.live_demo.validate_user"],
		"validate_reset_password": ["crm.api.live_demo.validate_reset_password"],
	},
	# Core Frappe emails every `_assign` ("assigned to you") — a bulk lead handover
	# is a mailbox full of them. Off for new users; existing rows swept once via
	# notification_prefs.disable_assignment_emails.
	"Notification Settings": {
		"before_insert": ["crm.api.notification_prefs.default_assignment_emails_off"],
	},
	# Quo Message is a custom doctype (ops repo); the SMS server scripts can't
	# call publish_realtime (not whitelisted in the sandbox), so the live
	# refresh for the SMS thread/inbox is emitted here on insert instead.
	# An inbound "STOP"/"remove me" also has to survive being read: the opt-out
	# check flags the buyer on a field no integration writes (gw296), because the
	# Dispo board column it used to rely on is shared state InvestorLift can move.
	"Quo Message": {
		"after_insert": [
			"crm.api.sms.on_quo_message_insert",
			"crm.api.do_not_contact.check_inbound_opt_out",
		],
	},
	# CRM Message is Talk (ops setup_talk.py): the in-CRM chat that replaces
	# Mattermost. Insert/edit/delete publish `crm_talk` and, for rows written
	# here (origin=crm), mirror to Mattermost during the transition. Inbound
	# Mattermost posts arrive origin=mattermost and are never mirrored back.
	# See crm/api/talk.py + crm/integrations/mattermost/.
	"CRM Message": {
		"after_insert": ["crm.api.talk.on_message_insert"],
		"on_update": ["crm.api.talk.on_message_update"],
		"on_trash": ["crm.api.talk.on_message_trash"],
	},
	# CRM Property Tax Pull is a custom doctype (ops repo). The `pull-tax-info`
	# server script stores BatchData's raw property record; the sandbox can't
	# parse it richly or publish_realtime, so the parse + lead writeback + live
	# refresh happen here on insert. See crm/api/tax_info.py.
	"CRM Property Tax Pull": {
		"after_insert": ["crm.api.tax_info.on_tax_pull_insert"],
	},
	# CRM Esign Agreement is a custom doctype (ops repo). The create-agreement-draft
	# + documenso-webhook server scripts can't publish_realtime or stamp time in
	# the sandbox, so the live refresh (crm_esign event) + last_event_at stamping
	# happen here. See crm/api/agreement.py.
	"CRM Esign Agreement": {
		"after_insert": ["crm.api.agreement.on_agreement_insert"],
		"on_update": ["crm.api.agreement.on_agreement_update"],
	},
	# CRM Underwriting Workbook is a custom doctype (ops repo). The Google Sheets
	# copy/fill happens in app code (crm.api.underwriting, the sandbox can't sign
	# the OAuth JWT); the hook mirrors the sheet URL onto the lead + publishes the
	# crm_underwriting realtime event. See crm/api/underwriting.py.
	"CRM Underwriting Workbook": {
		"after_insert": ["crm.api.underwriting.on_workbook_insert"],
	},
	# CRM Buyer is a custom doctype (ops repo). Buyers sync two-way with Quo
	# (OpenPhone) contacts so every buyer's number shows their name on calls
	# and texts: push on create/edit, tombstone-unlink on delete; the pull
	# side is the sync_all cron below. See crm/api/quo_contacts.py.
	"CRM Buyer": {
		"after_insert": ["crm.api.quo_contacts.on_buyer_after_insert"],
		"on_update": ["crm.api.quo_contacts.on_buyer_update"],
		"on_trash": ["crm.api.quo_contacts.on_buyer_trash"],
	},
	# CRM Lead Buyer (ops doctype) is the buyer↔property engagement row: on
	# engage/disengage, re-push the buyer's Quo contact so its "Property"
	# multi-select tags reflect current engagements.
	"CRM Lead Buyer": {
		"after_insert": ["crm.api.quo_contacts.on_lead_buyer_change"],
		"on_trash": ["crm.api.quo_contacts.on_lead_buyer_change"],
	},
	# Sequence Events Log is where the OpenPhone `message.received` webhook lands
	# every inbound text (ops repo). When one is an InvestorLift "address request"
	# notification, pull that buyer onto the property's Dispo board in real time
	# (webhook-driven, no polling). See crm/api/investorlift_ingest.on_sequence_event.
	"Sequence Events Log": {
		"after_insert": ["crm.api.investorlift_ingest.on_sequence_event"],
	},
	# Real-time drainer for sub-minute sequence steps. The sandboxed sequence
	# engine can't sleep or enqueue cleanly into a worker, so the burst is driven
	# from here (non-sandboxed): enqueue a worker that sleeps the real waits and
	# reuses the engine for each step. Fires on the same condition as auto-enroll.
	# Statuses whose NAMES app logic depends on (Today board phases, dashboard
	# scopes, the dispo gate) — alert Lance if one is renamed or deleted, and
	# sweep daily in case it happened outside these hooks. crm/api/lead_status.py
	"CRM Lead Status": {
		"on_update": ["crm.api.lead_status.guard_on_update"],
		"on_trash": ["crm.api.lead_status.guard_on_trash"],
	},
	"CRM Deal Status": {
		"on_update": ["crm.api.lead_status.guard_on_update"],
		"on_trash": ["crm.api.lead_status.guard_on_trash"],
	},
	"CRM Lead": {
		# Round-robin the owner of a new ownerless (i.e. inbound webhook) lead
		# between the setters, so German and Exe split the day's intake instead of
		# every lead landing on one default owner. MUST stay a before_insert hook:
		# Frappe composes doc_events[doctype] BEFORE doc_events["*"], and the ops
		# `Lead Default Owner` server script runs via that wildcard entry — so this
		# claims the owner first and the server script degrades to a safety net.
		# See crm/api/lead_round_robin.py.
		"before_insert": ["crm.api.lead_round_robin.assign_round_robin_owner"],
		# Normalize odd-cased inbound names ("joe cholock" -> "Joe Cholock") on
		# creation only, so later manual edits are respected. Runs before the
		# controller rebuilds lead_name/title from the name parts in validate().
		"before_validate": ["crm.api.name_format.normalize_lead_names"],
		"after_insert": [
			# If before_insert took the owner from a LeadMarket reservation, record
			# which lead it produced so the desk can confirm the purchase landed
			# where it was pointed. See crm/api/lead_reservation.py.
			"crm.api.lead_reservation.stamp_matched_lead",
			# Bonus-wallet iSTL orders are non-refundable. LeadMarket knows the
			# wallet split; we only know the order id. Ask after commit so the
			# vendor webhook is not held open. See crm/api/refunds.py.
			"crm.api.refunds.on_istl_lead_insert",
			"crm.api.sequence_drain.enqueue_for_lead",
			# A new never-called lead may owe work immediately; add its card after commit.
			"crm.api.today_board.enqueue_today_sync",
			# Warm this lead's neighbourhood in redfin-scraper-api the moment we buy
			# it. A 2-mile sweep is ~75s and its parcels ~45min, so the work has to
			# start at purchase, not when a rep opens the desk. Enqueued (never inline)
			# so a slow sweep can't hold the inbound webhook open long enough for the
			# vendor to retry and duplicate the lead. No-op without site_config
			# redfin_scraper_url (legacy key geo_service_url also honoured).
			"crm.api.geo.on_lead_insert",
			# A phone that arrived with the lead (inbound webhook / manual create)
			# should pull its Quo history the same way typing one in later does.
			"crm.api.lead_phones.on_lead_phones_changed",
		],
		"on_update": [
			"crm.api.sequence_drain.enqueue_for_lead",
			# Lead moved out of the statuses a sequence runs in (CRM Sequence
			# .lead_statuses) → its Active enrollments are Paused, not Stopped, so
			# a mis-click can be resumed. See crm/api/sequence_status.py.
			"crm.api.sequence_status.on_lead_update",
			# lead newly linked to an InvestorLift property → tag its Quo
			# contact with the property address ("Property" multi-select)
			"crm.api.quo_contacts.on_lead_update",
			# lead moved to a dead status (CRM Lead Status type "Lost") → cancel
			# its open follow-up tasks, so dead leads stop showing up in the
			# to-do block and in every "due today" list. See crm/api/task_hygiene.py
			"crm.api.task_hygiene.on_lead_update",
			# Status moves during standup can make a lead newly eligible today.
			"crm.api.today_board.enqueue_today_sync",
			# Lead changed hands → its open tasks change hands too, and the previous
			# owner's automatic assignment is dropped. `CRM Lead.validate()` already
			# shares/assigns the NEW owner, but `assign_agent` only ever ADDS — so
			# without this a reassigned lead stays assigned to everyone who has ever
			# owned it, and its open tasks keep pointing at a rep who can no longer
			# see it. See crm/api/lead_owner_change.py.
			"crm.api.lead_owner_change.on_lead_update",
			# A refund field changed via doc.save (mail poller PUT, lead-page toggle)
			# → stamp custom_refund_updated_on, the Refunds board's "Updated".
			"crm.api.refunds.on_lead_update",
			# Side-panel / import edits of mobile_no/phone/extra_phones — the
			# dedicated add-phone API uses set_value (no hook) and backfills itself.
			"crm.api.lead_phones.on_lead_phones_changed",
		],
	},
}

# Scheduled Tasks
# ---------------

scheduler_events = {
	"daily_long": [
		"crm.lead_syncing.background_sync.sync_leads_from_sources_daily",
		# AI "Integrity Report": review yesterday's recorded calls + email Lance a digest
		"crm.api.call_review_ai.run_daily_integrity_report",
		# Status guard: email Lance if any code-referenced status name no longer
		# exists (rename/delete outside the doc hooks). Needs `sync_jobs` on prod.
		"crm.api.lead_status.daily_integrity_check",
	],
	"hourly_long": [
		"crm.lead_syncing.background_sync.sync_leads_from_sources_hourly",
		# InvestorLift Tier-1: refresh marketing metrics for every lead linked to an
		# IL property (needs `sync_jobs` on prod after deploy — see gw127/128).
		"crm.api.investorlift.sync_all_marketing",
		# Signed-contract parser backstop: enqueue any envelope signed in the
		# last CATCHUP_DAYS that has no parsed_at (needs `sync_jobs` on prod).
		"crm.api.contract_parse.catch_up_unparsed",
	],
	"monthly_long": ["crm.lead_syncing.background_sync.sync_leads_from_sources_monthly"],
	"cron": {
		# Single periodic driver for CRM Sequences: enqueue a drainer for every
		# due enrollment (the old `CRM Sequence Runner` core-cron is disabled in
		# favour of this). The drainer runs on the dedicated `seqdrain` queue.
		"* * * * *": [
			"crm.api.sequence_drain.drain_due",
			# Scheduled texts ("send Saturday 9am") whose time has come. NEW
			# scheduler method → needs `sync_jobs` on prod. See crm/api/scheduled_text.py
			"crm.api.scheduled_text.send_due",
		],
		"*/5 * * * *": [
			"crm.lead_syncing.background_sync.sync_leads_from_sources_5_minutes",
			# Safety net for new leads/tasks that land while an event-driven sync is
			# already finishing. Add-only and business-day guarded.
			"crm.api.today_board.run_today_sync",
		],
		"*/10 * * * *": [
			"crm.lead_syncing.background_sync.sync_leads_from_sources_10_minutes",
			# Buyer <-> Quo contact reconcile: pull team edits/tags from Quo,
			# push unlinked buyers, import 'buyer'-tagged Quo contacts.
			# (Needs `sync_jobs` on prod after deploy — see gw127/128.)
			"crm.api.quo_contacts.sync_all",
		],
		"*/15 * * * *": ["crm.lead_syncing.background_sync.sync_leads_from_sources_15_minutes"],
		# Daily standup list, 5:00am CENTRAL on business days. Frappe reads these
		# cron times in the SITE timezone (America/Chicago), not UTC — writing them
		# in UTC is what once turned an "8am" digest into a 1pm one. The job also
		# re-checks is_business_day() itself. NOTE: a new scheduler hook does not
		# run until `sync_jobs` creates its Scheduled Job Type row on prod (see
		# gw127/128 — the AI call review silently never fired for weeks).
		"0 5 * * 1-5": ["crm.api.daily_standup.send_daily_standup"],
		# Intraday Today-board pulse — a Mattermost group DM on the half hour.
		# Same SITE-timezone (America/Chicago) rule as the standup above. The cron
		# covers 9:00–17:30; the job itself enforces the real 9:30am–5:00pm window,
		# so the working hours live in one readable place instead of three cron
		# expressions. Also needs `sync_jobs` on prod before it will ever fire.
		"*/30 9-17 * * 1-5": ["crm.api.today_pulse.send_today_pulse"],
	},
}

# Testing
# -------

before_tests = "crm.tests.before_tests"

# Overriding Methods
# ------------------------------
#
# override_whitelisted_methods = {
# "frappe.desk.doctype.event.event.get_events": "crm.event.get_events"
# }
#
# each overriding function accepts a `data` argument;
# generated from the base implementation of the doctype dashboard,
# along with any modifications made in other Frappe apps
# override_doctype_dashboards = {
# "Task": "crm.task.get_dashboard_data"
# }

# exempt linked doctypes from being automatically cancelled
#
# auto_cancel_exempted_doctypes = ["Auto Repeat"]

# Ignore links to specified DocTypes when deleting documents
# -----------------------------------------------------------

ignore_links_on_delete = ["Failed Lead Sync Log"]

# Request Events
# ----------------
# before_request = ["crm.utils.before_request"]
# after_request = ["crm.utils.after_request"]

# Job Events
# ----------
# before_job = ["crm.utils.before_job"]
# after_job = ["crm.utils.after_job"]

# User Data Protection
# --------------------

# user_data_fields = [
# {
# "doctype": "{doctype_1}",
# "filter_by": "{filter_by}",
# "redact_fields": ["{field_1}", "{field_2}"],
# "partial": 1,
# },
# {
# "doctype": "{doctype_2}",
# "filter_by": "{filter_by}",
# "partial": 1,
# },
# {
# "doctype": "{doctype_3}",
# "strict": False,
# },
# {
# "doctype": "{doctype_4}"
# }
# ]

# Authentication and authorization
# --------------------------------

# auth_hooks = [
# "crm.auth.validate"
# ]

after_migrate = [
	"crm.fcrm.doctype.fcrm_settings.fcrm_settings.after_migrate",
	"crm.api.whatsapp.add_roles",
]

standard_dropdown_items = [
	{
		"name1": "app_selector",
		"label": "Apps",
		"type": "Route",
		"route": "#",
		"is_standard": 1,
	},
	{
		"name1": "settings",
		"label": "Settings",
		"type": "Route",
		"icon": "settings",
		"route": "#",
		"is_standard": 1,
	},
	{
		"name1": "login_to_fc",
		"label": "Login to Frappe Cloud",
		"type": "Route",
		"route": "#",
		"is_standard": 1,
	},
	{
		"name1": "about",
		"label": "About",
		"type": "Route",
		"icon": "info",
		"route": "#",
		"is_standard": 1,
	},
	{
		"name1": "separator",
		"label": "",
		"type": "Separator",
		"is_standard": 1,
	},
	{
		"name1": "logout",
		"label": "Log out",
		"type": "Route",
		"icon": "log-out",
		"route": "#",
		"is_standard": 1,
	},
]
