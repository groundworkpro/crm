"""ARV feedback: a rep disagrees with the AI pricer's after-repair value and
says why. It goes to the person who owns the pricer (Exe) as a Talk DM from the
rep, which Talk mirrors to Mattermost under the rep's name, so Exe can reply
and the two can talk it through. A copy is also filed in the suggested-fixes list
(crm.api.ai_suggestions, source Disagree) for review before it goes into the skill.

Site config `arv_feedback_to` overrides the recipient (a CRM login).
"""

from __future__ import annotations

import frappe
from frappe import _

from crm.api.live_one import _site_url
from crm.api.reports import validate_access

DEFAULT_RECIPIENT = "exe.ortiz@groundworkpro.com"
MAX_FEEDBACK = 4000
MAX_DETAILS = 8000


def recipient() -> str:
	return (frappe.conf.get("arv_feedback_to") or DEFAULT_RECIPIENT).strip().lower()


def _money(value) -> str:
	try:
		return f"${round(float(value)):,}"
	except (TypeError, ValueError):
		return ""


def build_message(site: str, lead: str | None, address: str, ai_arv, feedback: str, user_arv=None, details: str = "") -> str:
	where = address or lead or _("a property")
	if lead:
		where = f"[{where}]({site}/crm/leads/{lead}/comps)"
	lines = [f"I think the AI's ARV is off on {where}.", ""]
	ai = _money(ai_arv)
	mine = _money(user_arv)
	if ai:
		lines.append(f"AI's ARV: **{ai}**" + (f" · my ARV: **{mine}**" if mine else ""))
	elif mine:
		lines.append(f"My ARV: **{mine}**")
	lines += ["", "> " + feedback.strip().replace("\n", "\n> ")]
	details = (details or "").strip()[:MAX_DETAILS]
	if details:
		lines += ["", "The AI's math:", "```", details.replace("```", "'''"), "```"]
	return "\n".join(lines)


@frappe.whitelist()
def send(ai_arv=None, feedback: str = "", lead: str | None = None, address: str = "", user_arv=None, details: str = ""):
	"""Send the rep's feedback on an AI ARV to the pricer's owner. Returns {ok, to, channel}."""
	validate_access()
	feedback = (feedback or "").strip()
	if not feedback:
		frappe.throw(_("Say what looks wrong with the math first."))
	if len(feedback) > MAX_FEEDBACK:
		frappe.throw(_("That feedback is too long."))
	if lead:
		if not frappe.db.exists("CRM Lead", lead):
			frappe.throw(_("Lead not found"), frappe.DoesNotExistError)
		frappe.get_doc("CRM Lead", lead).check_permission("read")
	from crm.api import ai_suggestions

	to = recipient()
	suggestion = ai_suggestions.record(
		"Disagree", frappe.session.user, feedback, lead=lead, address=(address or "").strip(),
		ai_arv=ai_arv, rep_arv=user_arv, context=(details or "")[:MAX_DETAILS],
	)
	if to == (frappe.session.user or "").lower():
		# The owner disagreeing with the AI: no DM to himself, the list is the record.
		if not suggestion:
			frappe.throw(_("ARV feedback goes to you already, so there is no one to send it to."))
		return {"ok": True, "to": _("your suggestions list"), "channel": None, "suggestion": suggestion}

	from crm.api import talk

	channel = talk.dm_channel_between(frappe.session.user, to)
	if not channel:
		frappe.throw(_("Could not open a Talk message to {0}.").format(to))
	text = build_message(_site_url(), lead, (address or "").strip(), ai_arv, feedback, user_arv, details)
	doc = frappe.get_doc(
		{
			"doctype": talk.MESSAGE,
			"channel": channel,
			"author": frappe.session.user,
			"text": text[: talk.MAX_TEXT],
			"posted_at": frappe.utils.now(),
			"origin": "crm",  # mirrored to Mattermost by talk.on_message_insert
		}
	).insert(ignore_permissions=True)
	return {"ok": True, "to": frappe.utils.get_fullname(to) or to, "channel": channel, "message": doc.name,
	        "suggestion": suggestion}
