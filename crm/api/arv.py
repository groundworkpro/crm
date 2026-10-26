"""Save an after-repair value (ARV) onto a lead — the Ask AI pricer's "Save ARV" button.

The lead's `arv` Currency field (ops repo `scripts/setup_arv_field.py`, shown in the
side panel's Property section) holds the CURRENT ARV, and every save also writes a
Comment on the lead's timeline so an earlier ARV is never lost when someone re-prices.

Kept apart from `price_determination` on purpose: that snapshot is the lead desk's
full derivation (repair level, margin, fee) and refuses an offer its own formula does
not produce. The pricer's offers use a different formula, so it only hands us the ARV
plus what it said about it; the desk and the comps page keep their own math.

The field write is column-guarded like price_determination: deployed before the ops
script, a save still lands on the timeline and returns `stored: false`.
"""

import frappe
from frappe.utils import escape_html

# A typo guard, not a market opinion: no Groundwork lead is worth $50M.
MAX_ARV = 50_000_000


def _money(n):
	return "${:,.0f}".format(n)


def _amount(v, label):
	try:
		n = round(float(v))
	except (TypeError, ValueError):
		frappe.throw("{0} must be a number.".format(label))
	if n <= 0 or n > MAX_ARV:
		frappe.throw("{0} {1} is not a believable amount.".format(label, _money(n)))
	return n


def _optional(v, label):
	return None if v in (None, "", 0, "0") else _amount(v, label)


@frappe.whitelist()
def save_arv(lead: str, arv, arv_low=None, arv_high=None, offer=None, source: str = "AI pricer"):
	"""Set the lead's ARV and log it on the timeline. Returns `{stored, arv}`."""
	if not frappe.db.exists("CRM Lead", lead):
		frappe.throw("Lead {0} not found.".format(lead))
	frappe.has_permission("CRM Lead", "write", lead, throw=True)

	arv = _amount(arv, "ARV")
	low, high, offer = _optional(arv_low, "Low ARV"), _optional(arv_high, "High ARV"), _optional(offer, "Offer")
	source = (source or "AI pricer").strip()[:60]

	stored = frappe.db.has_column("CRM Lead", "arv")
	if stored:
		frappe.db.set_value("CRM Lead", lead, "arv", arv)

	bits = ["range {0}–{1}".format(_money(low), _money(high))] if low and high else []
	if offer:
		bits.append("offer {0}".format(_money(offer)))
	content = "<div><b>ARV {0}</b> saved from {1}{2}</div>".format(
		_money(arv), escape_html(source), (" · " + " · ".join(bits)) if bits else ""
	)
	frappe.get_doc(
		{
			"doctype": "Comment",
			"comment_type": "Comment",
			"reference_doctype": "CRM Lead",
			"reference_name": lead,
			"content": content,
			"comment_email": frappe.session.user,
			"comment_by": frappe.session.user,
		}
	).insert(ignore_permissions=True)
	return {"stored": stored, "arv": arv}
