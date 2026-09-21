"""Recent-contact context for CRM Sequence Jinja conditions.

Sequence Server Scripts can call ``frappe.call('crm.api.sequence_contact.context',
lead=lead.name)`` and use ``contact.contacted_within_one_business_day`` in a
Jinja condition.  The check is made by the runner immediately before sending,
so a conversation after enrollment still suppresses the outbound step.
"""

from __future__ import annotations

from datetime import datetime

import frappe
from frappe.utils import get_datetime, now_datetime

from crm.api.daily_standup import business_days_between


def latest_contact_timestamp(*timestamps):
	"""Return the newest usable timestamp from the supplied contact sources.

	Keeping selection pure lets the database query stay a small adapter and
	keeps the business-day policy independently testable.
	"""
	valid = []
	for timestamp in timestamps:
		if not timestamp:
			continue
		try:
			valid.append(get_datetime(timestamp))
		except (TypeError, ValueError):
			continue
	return max(valid, default=None)


def contacted_within_one_business_day(last_contact, now=None) -> bool:
	"""Whether ``last_contact`` is no more than one business day ago.

	``business_days_between`` is the shared calendar policy for weekends,
	federal holidays, and configured ``crm_holidays``.  Thus Friday contact is
	still recent on Monday; a contact from Thursday is not.
	"""
	if not last_contact:
		return False
	try:
		last_contact = get_datetime(last_contact)
	except (TypeError, ValueError):
		return False
	now = get_datetime(now or now_datetime())
	return business_days_between(last_contact, now) <= 1


def _contact_queries():
	"""Timestamp queries for genuine, lead-linked contacts.

	Quo placeholders are intentionally omitted: a scheduled or canceled text
	does not mean a seller was contacted.  The other two sources only contain
	actual recorded call/email activity once linked to the lead.
	"""
	queries = [
		"""
			select max(coalesce(end_time, start_time, creation)) as last_contact
			from `tabCRM Call Log`
			where reference_doctype = 'CRM Lead' and reference_docname = %(lead)s
		""",
		"""
			select max(coalesce(communication_date, creation)) as last_contact
			from `tabCommunication`
			where reference_doctype = 'CRM Lead' and reference_name = %(lead)s
		""",
	]
	if frappe.db.exists("DocType", "Quo Message"):
		queries.append(
			"""
				select max(coalesce(message_date, creation)) as last_contact
				from `tabQuo Message`
				where reference_doctype = 'CRM Lead' and reference_docname = %(lead)s
				  and coalesce(status, '') not in ('scheduled', 'canceled')
			"""
		)
	return queries


def latest_contact_for_lead(lead):
	"""Return the latest real Call Log, Quo Message, or Communication contact."""
	if not lead:
		return None
	timestamps = []
	for query in _contact_queries():
		rows = frappe.db.sql(query, {"lead": lead}, as_dict=True)
		if rows:
			timestamps.append(rows[0].get("last_contact"))
	return latest_contact_timestamp(*timestamps)


@frappe.whitelist()
def context(lead, now=None):
	"""JSON-safe recent-contact values for a CRM Sequence Server Script.

	``now`` exists for deterministic bench/testing calls; the sequence runner
	leaves it blank and always evaluates at send time.
	"""
	last_contact = latest_contact_for_lead(lead)
	return {
		"contacted_within_one_business_day": contacted_within_one_business_day(last_contact, now),
		"last_contact": last_contact.isoformat(sep=" ") if isinstance(last_contact, datetime) else None,
	}
