"""Stop texting numbers that can't receive texts.

Why (Quo compliance warning, 2026-11-10): Quo suspends an account whose 30-day
failure rate passes 10%, and ours hit 9.1%. Measured: 184 of 2,951 texts failed,
but they went to only 52 numbers — the same dead numbers were texted day after
day (one 19 times), because the CRM never learned a text failed. Quo sends no
webhook for an undelivered text, and `message.delivered` for a CRM-sent text is
deduped away, so every CRM row stayed "sent" forever. 18 of the 52 were landlines.

Two parts:
  * `refresh_statuses` (cron) re-reads recent "sent" Quo texts from the Quo API,
    so an undelivered one shows its badge in the lead's thread.
  * `text_block_reason(number)` is the check every text path calls before
    sending: it refuses a number whose last text was undelivered (until it
    texts us or a later text is delivered) and a landline (Telnyx carrier lookup,
    cached per number).
"""

import time

import frappe
import requests
from frappe.utils import add_to_date, now_datetime

from crm.api.telephony import last10

QUO_MESSAGE_API = "https://api.openphone.com/v1/messages/"
TELNYX_LOOKUP_API = "https://api.telnyx.com/v2/number_lookup/"
PENDING = ("sent", "queued", "")
LINE_TYPE_TTL = 180 * 86400


def block_reason_from(history, line_type, shown):
	"""Pure rule. `history`: this number's texts, newest first, as
	(direction, status, message_date). `shown`: the number as the rep sees it."""
	for direction, status, when in history:
		if direction == "Incoming" or status in ("delivered", "read"):
			break
		if status == "undelivered":
			return (
				f"Not sent: your last text to {shown} ({when:%b %-d}) was not delivered, "
				"so the number can't receive texts. Call it or add another number."
			)
	if line_type == "fixed":
		return f"Not sent: {shown} is a landline and can't receive texts. Call it instead."
	return ""


def _line_type(key):
	"""Telnyx carrier type ('mobile', 'fixed', 'voip', ...) or '' if unknown."""
	cache_key = "text_delivery:line_type:" + key
	cached = frappe.cache().get_value(cache_key)
	if cached is not None:
		return cached
	token = (frappe.conf.get("telnyx_api_key") or "").strip()
	if not token:
		return ""
	try:
		r = requests.get(
			TELNYX_LOOKUP_API + "+1" + key,
			params={"type": "carrier"},
			headers={"Authorization": "Bearer " + token},
			timeout=10,
		)
		r.raise_for_status()
		kind = (((r.json() or {}).get("data") or {}).get("carrier") or {}).get("type") or ""
	except Exception:
		frappe.log_error(title="text_delivery: line type lookup failed")
		return ""  # never block a text because the lookup is down
	frappe.cache().set_value(cache_key, kind, expires_in_sec=LINE_TYPE_TTL)
	return kind


@frappe.whitelist()
def text_block_reason(number):
	"""'' when it's fine to text `number`, else the message to show the rep."""
	key = last10(number)
	if not key:
		return ""
	rows = frappe.get_all(
		"Quo Message",
		filters=[["to", "like", "%" + key], ["direction", "=", "Outgoing"]],
		fields=["direction", "status", "message_date"],
		order_by="message_date desc",
		limit_page_length=20,
	) + frappe.get_all(
		"Quo Message",
		filters=[["from", "like", "%" + key], ["direction", "=", "Incoming"]],
		fields=["direction", "status", "message_date"],
		order_by="message_date desc",
		limit_page_length=1,
	)
	history = sorted(
		((r.direction, (r.status or "").lower(), r.message_date) for r in rows if r.message_date),
		key=lambda h: h[2],
		reverse=True,
	)
	shown = f"({key[:3]}) {key[3:6]}-{key[6:]}"
	return block_reason_from(history, _line_type(key), shown)


def refresh_statuses(days=3, limit=300):
	"""Cron: pull the real status of recent Quo texts still marked sent.
	Backfill once with `bench execute ... --kwargs '{"days": 30, "limit": 5000}'`."""
	token = (frappe.conf.get("quo_api_key") or "").strip()
	if not token:
		return
	now = now_datetime()
	rows = frappe.get_all(
		"Quo Message",
		filters=[
			["direction", "=", "Outgoing"],
			["status", "in", PENDING],
			["id", "not like", "sched-%"],
			["provider", "in", ("", "quo", "Quo")],
			["message_date", "between", [add_to_date(now, days=-int(days)), add_to_date(now, minutes=-5)]],
		],
		fields=["name", "id", "status", "reference_doctype", "reference_docname"],
		limit_page_length=int(limit),
	)
	for r in rows:
		try:
			resp = requests.get(
				QUO_MESSAGE_API + r.id,
				headers={"Authorization": token, "User-Agent": "curl/8.1.0"},
				timeout=15,
			)
			if resp.status_code == 404:
				continue
			resp.raise_for_status()
			status = ((resp.json() or {}).get("data") or {}).get("status") or ""
		except Exception:
			frappe.log_error(title="text_delivery: status refresh failed " + r.id)
			continue
		if status and status != r.status:
			frappe.db.set_value("Quo Message", r.name, "status", status, update_modified=False)
			frappe.db.commit()
			if r.reference_docname:
				frappe.publish_realtime(
					"quo_message",
					{"reference_doctype": r.reference_doctype, "reference_docname": r.reference_docname},
				)
		time.sleep(0.12)  # Quo allows 10 requests/second
