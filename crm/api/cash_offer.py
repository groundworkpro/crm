"""Save an offer calc to the lead's activity timeline.

This is the comps-page calculator, not the desk rail. The rail is
90% ARV − 2×repairs − fee and lives in `price_determination`. Cash runs
ARV × % − (mult × repairs) − assignment fee per scenario, where `mult` is
1 (the classic 70% rule) or 2 (the same shape the rail uses). Novation is
Current value − 10% − fee ($40k default) — no repairs. List it is as-is
− 6% commission − 2% closing − 2% concessions, and the number it produces
is the seller's takeaway if they listed the house themselves. Rental is
MAO = MIR × 80% − repairs − fee. They must not write the same field or
a re-price on one surface silently rewrites the other.

The numbers are recomputed here rather than trusted from the client, so the
timeline card and the calculator cannot disagree about what an offer was.
A cash save also upserts one FCRM Note on the lead titled with the wholesale
figure (after − rehab, before fee) so it is on the Notes tab without opening
comps. Practice writes stay on the attempt. A
scenario saved before the cash-formula toggle existed carries no `mult` and
is read as 1 — which is what its numbers meant when they were written. A
scenario with no `kind` is cash, for the same reason.
"""

import html as html_lib
import json
import re

import frappe
from frappe import _
from frappe.utils import escape_html, flt, get_url

from crm.api.comps import _guard


def _num(v):
	return flt(v or 0)


def _money(n):
	return "$" + f"{int(round(_num(n))):,}"


def _psf_money(n):
	"""$/sf, with cents only when it has them.

	A repair bill typed as a round number rarely divides evenly into the square
	footage ($45,000 over 1,260 sf is $35.714…), and the calculator keeps that
	remainder on the RATE so the bill stays exactly what was typed. Rounding it
	here would print "$36/sf × 1,260 sf = $45,000" on the timeline, which is off
	by $360 and reads as the card contradicting itself.
	"""
	v = _num(n)
	return _money(v) if float(v).is_integer() else f"${v:,.2f}"


def _street(addr):
	return (addr or "").split(",")[0].strip()


def _zillow(addr):
	slug = re.sub(r"[^A-Za-z0-9]+", "-", addr or "").strip("-")
	return f"https://www.zillow.com/homes/{slug}_rb/" if slug else ""


def _kind(raw):
	k = (raw.get("kind") or "cash") if isinstance(raw, dict) else "cash"
	if k in ("novation", "wholetail", "list", "rental", "auction"):
		return k
	return "cash"


def _pct(raw, default=0):
	if "pct" not in raw or raw.get("pct") in (None, ""):
		return default
	pct = _num(raw.get("pct"))
	if pct > 1:
		pct = pct / 100.0
	return pct


def _rate(raw, *keys, default=0):
	for k in keys:
		if k in raw and raw.get(k) not in (None, ""):
			n = _num(raw.get(k))
			return n / 100.0 if n > 1 else n
	return default


def _take_profit(net, raw, fee):
	"""Profit is a dollar takeoff ($25k default). Percent is derived for display.

	Legacy rows with a percent and no fee still split as % of acq.
	"""
	if fee:
		return net - fee, fee
	if any(k in raw and raw.get(k) not in (None, "") for k in ("profit_pct", "profitPct")):
		m = _rate(raw, "profit_pct", "profitPct", default=0.20)
		if m <= -1:
			return net, 0
		offer = round(net / (1.0 + m))
		return offer, net - offer
	return net, 0


def _scene(raw, sqft):
	if not isinstance(raw, dict):
		return None
	arv = _num(raw.get("arv") or raw.get("value") or raw.get("current_value"))
	if arv <= 0:
		return None
	kind = _kind(raw)
	fee = _num(raw.get("fee"))
	if kind == "list":
		commission_pct = _rate(raw, "commission_pct", "commissionPct", default=0.06)
		closing_pct = _rate(raw, "closing_pct", "closingPct", default=0.02)
		concessions_pct = _rate(raw, "concessions_pct", "concessionsPct", default=0.02)
		commission = round(arv * commission_pct)
		closing = round(arv * closing_pct)
		concessions = round(arv * concessions_pct)
		after = round(arv - commission - closing - concessions)
		return {
			"kind": "list",
			"arv": arv,
			"commission_pct": commission_pct,
			"closing_pct": closing_pct,
			"concessions_pct": concessions_pct,
			"commission": commission,
			"closing": closing,
			"concessions": concessions,
			"after": after,
			"offer": after,
		}
	if kind == "rental":
		pct = _pct(raw, 0.80)
		if pct <= 0 or pct >= 1:
			return None
		rehab_psf = _num(raw.get("rehabPsf") or raw.get("rehab_psf"))
		after = round(arv * pct)
		repairs = round(rehab_psf * _num(sqft))
		wholesale = after - repairs
		offer, profit = _take_profit(wholesale, raw, fee)
		return {
			"kind": "rental",
			"arv": arv,
			"pct": pct,
			"rehab_psf": rehab_psf,
			"fee": profit,
			"profit_pct": (profit / offer) if offer else 0,
			"after": after,
			"repairs": repairs,
			"rehab": repairs,
			"wholesale": wholesale,
			"offer": offer,
		}
	if kind == "novation":
		pct = _pct(raw, 0.10)
		if pct < 0 or pct >= 1:
			return None
		cut = round(arv * pct)
		after = round(arv - cut)
		offer, profit = _take_profit(after, raw, fee)
		return {
			"kind": "novation",
			"arv": arv,
			"pct": pct,
			"fee": profit,
			"profit_pct": (profit / offer) if offer else 0,
			"cut": cut,
			"after": after,
			"offer": offer,
		}
	if kind == "wholetail":
		pct = _pct(raw, 0.10)
		if pct < 0 or pct >= 1:
			return None
		rate = _rate(raw, "interest_pct", "interestPct", default=0.12)
		months = _num(raw.get("hold_months") or raw.get("holdMonths") or 6)
		if months < 0:
			months = 0
		cut = round(arv * pct)
		after = round(arv - cut)
		capital = round(arv * rate * (months / 12.0))
		holding = round(arv * 0.015 * (months / 12.0) + 200 * months)
		offer, profit = _take_profit(after - capital - holding, raw, fee)
		return {
			"kind": "wholetail",
			"arv": arv,
			"pct": pct,
			"fee": profit,
			"profit_pct": (profit / offer) if offer else 0,
			"cut": cut,
			"after": after,
			"interest_pct": rate,
			"hold_months": months,
			"capital": capital,
			"holding": holding,
			"offer": offer,
		}
	pct = _pct(raw, 0)
	if pct <= 0:
		return None
	rehab_psf = _num(raw.get("rehabPsf") or raw.get("rehab_psf"))
	mult = 2 if int(_num(raw.get("mult")) or 1) == 2 else 1
	after = round(arv * pct)
	repairs = round(rehab_psf * _num(sqft))
	rehab = repairs * mult
	wholesale = after - rehab
	liens = _num(raw.get("liens")) if kind == "auction" else 0
	back_taxes = _num(raw.get("back_taxes") or raw.get("backTaxes")) if kind == "auction" else 0
	offer, profit = _take_profit(wholesale - liens - back_taxes, raw, fee)
	out = {
		"kind": "auction" if kind == "auction" else "cash",
		"arv": arv,
		"pct": pct,
		"mult": mult,
		"rehab_psf": rehab_psf,
		"fee": profit,
		"profit_pct": (profit / offer) if offer else 0,
		"after": after,
		"repairs": repairs,
		"rehab": rehab,
		"wholesale": wholesale,
		"offer": offer,
	}
	if kind == "auction":
		out["liens"] = liens
		out["back_taxes"] = back_taxes
	return out


def _comps(raw):
	out = []
	for c in raw or []:
		if not isinstance(c, dict):
			continue
		addr = (c.get("address") or "").strip()
		if not addr:
			continue
		out.append(
			{
				"name": c.get("name") or "",
				"address": addr,
				"price": _num(c.get("price")),
				"square_footage": _num(c.get("square_footage")),
				"distance_mi": _num(c.get("distance_mi")),
				"status": c.get("status") or "",
			}
		)
	return out


def _comp_facts(c):
	psf = (
		round(c["price"] / c["square_footage"])
		if c["price"] and c["square_footage"]
		else 0
	)
	bits = [_money(c["price"])]
	if c["square_footage"]:
		bits.append(f"{int(c['square_footage']):,} sf")
	if psf:
		bits.append(_money(psf) + "/sf")
	if c["distance_mi"]:
		bits.append(f"{c['distance_mi']:.2f} mi")
	return " · ".join(bits)


def _scene_payload(sc):
	if sc.get("kind") == "list":
		return {
			"kind": "list",
			"arv": sc["arv"],
			"commission_pct": sc["commission_pct"],
			"closing_pct": sc["closing_pct"],
			"concessions_pct": sc["concessions_pct"],
			"commission": sc["commission"],
			"closing": sc["closing"],
			"concessions": sc["concessions"],
			"after": sc["after"],
			"offer": sc["offer"],
		}
	if sc.get("kind") == "rental":
		return {
			"kind": "rental",
			"arv": sc["arv"],
			"pct": sc["pct"],
			"rehab_psf": sc["rehab_psf"],
			"fee": sc["fee"],
			"profit_pct": sc.get("profit_pct") or 0,
			"after": sc["after"],
			"repairs": sc["repairs"],
			"rehab": sc["rehab"],
			"offer": sc["offer"],
		}
	if sc.get("kind") == "novation":
		return {
			"kind": "novation",
			"arv": sc["arv"],
			"pct": sc["pct"],
			"fee": sc["fee"],
			"profit_pct": sc.get("profit_pct") or 0,
			"cut": sc["cut"],
			"after": sc["after"],
			"offer": sc["offer"],
		}
	if sc.get("kind") == "wholetail":
		return {
			"kind": "wholetail",
			"arv": sc["arv"],
			"pct": sc["pct"],
			"fee": sc["fee"],
			"profit_pct": sc.get("profit_pct") or 0,
			"cut": sc["cut"],
			"after": sc["after"],
			"interest_pct": sc.get("interest_pct") or 0,
			"hold_months": sc.get("hold_months") or 0,
			"capital": sc.get("capital") or 0,
			"holding": sc.get("holding") or 0,
			"offer": sc["offer"],
		}
	if sc.get("kind") == "auction":
		return {
			"kind": "auction",
			"arv": sc["arv"],
			"pct": sc["pct"],
			"mult": sc["mult"],
			"rehab_psf": sc["rehab_psf"],
			"fee": sc["fee"],
			"profit_pct": sc.get("profit_pct") or 0,
			"liens": sc.get("liens") or 0,
			"back_taxes": sc.get("back_taxes") or 0,
			"after": sc["after"],
			"repairs": sc["repairs"],
			"rehab": sc["rehab"],
			"wholesale": sc["wholesale"],
			"offer": sc["offer"],
		}
	return {
		"kind": "cash",
		"arv": sc["arv"],
		"pct": sc["pct"],
		"mult": sc["mult"],
		"rehab_psf": sc["rehab_psf"],
		"fee": sc["fee"],
		"profit_pct": sc.get("profit_pct") or 0,
		"after": sc["after"],
		"repairs": sc["repairs"],
		"rehab": sc["rehab"],
		"offer": sc["offer"],
	}


def _wholesale_note(scenes):
	"""Title + HTML for the lead Notes tab. None when the calc has no wholesale."""
	rows = [
		sc for sc in scenes if sc.get("kind") == "cash" and sc.get("wholesale") is not None
	]
	if not rows:
		return None, None
	amounts = [_money(sc["wholesale"]) for sc in rows]
	title = _("Wholesale {0}").format(" · ".join(amounts))
	parts = []
	for i, sc in enumerate(rows):
		label = _money(sc["wholesale"])
		if len(rows) > 1:
			label = _("Scenario {0}: {1}").format(i + 1, label)
		parts.append("<div><b>{}</b></div>".format(escape_html(label)))
	return title, "".join(parts)


def _upsert_wholesale_note(lead, scenes, street=""):
	"""Keep one Wholesale note per lead so re-saves do not stack.

	A calc saved on a property linked to the lead (`street` set) keeps its own
	note, titled "<street> · Wholesale …", so it neither overwrites nor is
	overwritten by the seller's own house (whose title starts with "Wholesale")."""
	title, content = _wholesale_note(scenes)
	if not title:
		return
	prefix = f"{street} · " if street else ""
	title = prefix + title
	existing = frappe.get_all(
		"FCRM Note",
		filters={
			"reference_doctype": "CRM Lead",
			"reference_docname": lead,
			"title": ["like", prefix.replace("%", "\\%").replace("_", "\\_") + "Wholesale %"],
		},
		pluck="name",
		order_by="modified desc",
		limit=1,
	)
	if existing:
		doc = frappe.get_doc("FCRM Note", existing[0])
		doc.title = title
		doc.content = content
		doc.save(ignore_permissions=True)
		return
	frappe.get_doc(
		{
			"doctype": "FCRM Note",
			"title": title,
			"content": content,
			"reference_doctype": "CRM Lead",
			"reference_docname": lead,
		}
	).insert(ignore_permissions=True)


def _payload(lead, scenes, comps, sqft, notes="", address=""):
	kind = scenes[0]["kind"] if scenes else "cash"
	out = {
		"lead": lead,
		"kind": kind,
		"sqft": sqft,
		"notes": (notes or "").strip(),
		"scenarios": [_scene_payload(sc) for sc in scenes],
		"comps": [
			{
				"name": c.get("name") or "",
				"address": c["address"],
				"price": c["price"],
				"square_footage": c["square_footage"],
				"distance_mi": c["distance_mi"],
				"status": c.get("status") or "",
			}
			for c in comps
		],
	}
	# The house a property calc was for. The card shows it, so a calc posted on
	# the seller's timeline for their second house is not read as their own.
	if address:
		out["address"] = address
	return out


def _html(lead, scenes, comps, sqft, notes="", address=""):
	# Vertical on purpose: the activity bubble is ~20rem and `prose-f` is
	# `break-all`, so a single "Comps: A · B" line wraps mid-number and reads
	# as a text blob. One row per comp, each a real link. `data-cash-offer` is
	# the structured copy the timeline card hydrates; the HTML is the fallback
	# for email / edit-source / anything that is not CommentArea.
	from crm.api.comps import SCRATCH_DOCTYPE, subject_doctype

	page = get_url(
		f"/crm/properties/{lead}"
		if subject_doctype(lead) == SCRATCH_DOCTYPE
		else f"/crm/leads/{lead}/comps"
	)
	attr = html_lib.escape(
		json.dumps(_payload(lead, scenes, comps, sqft, notes, address), separators=(",", ":")),
		quote=True,
	)
	kind = scenes[0].get("kind") if scenes else "cash"
	if kind == "novation":
		title = _("Novation offer")
	elif kind == "list":
		title = _("List it")
	elif kind == "rental":
		title = _("Rental MAO")
	elif kind == "auction":
		title = _("Auction max bid")
	elif kind == "wholetail":
		title = _("Wholetail offer")
	else:
		title = _("Cash offer")
	parts = [
		'<div class="cash-offer" data-cash-offer="{}">'.format(attr),
		"<div><b>{}</b></div>".format(escape_html(title)),
	]
	if address:
		parts.append("<div>{}</div>".format(escape_html(_("For {0}").format(address))))
	for i, sc in enumerate(scenes):
		label = _("Scenario {0}").format(i + 1)
		if sc.get("kind") == "list":
			parts.append(
				"<div>{label}</div>"
				"<div>{arv} {asis}</div>"
				"<div>− {comm_l} {comm_pct:.0f}% {comm}</div>"
				"<div>− {clos_l} {clos_pct:.0f}% {clos}</div>"
				"<div>− {conc_l} {conc_pct:.0f}% {conc}</div>"
				'<div>= <b style="white-space:nowrap">{offer}</b> {take}</div>'.format(
					label=escape_html(label),
					arv=_money(sc["arv"]),
					asis=escape_html(_("as-is")),
					comm_l=escape_html(_("commission")),
					comm_pct=sc["commission_pct"] * 100,
					comm=_money(sc["commission"]),
					clos_l=escape_html(_("closing")),
					clos_pct=sc["closing_pct"] * 100,
					clos=_money(sc["closing"]),
					conc_l=escape_html(_("concessions")),
					conc_pct=sc["concessions_pct"] * 100,
					conc=_money(sc["concessions"]),
					offer=_money(sc["offer"]),
					take=escape_html(_("takeaway")),
				)
			)
			continue
		if sc.get("kind") == "rental":
			parts.append(
				"<div>{label} ({pct:.0f}%)</div>"
				"<div>{arv} × {pct:.0f}% = {after}</div>"
				"<div>− {repairs_l} {repairs}</div>"
				'<div>− {profit_l} {pp:.0f}% {fee} = <b style="white-space:nowrap">{offer}</b> {mao}</div>'.format(
					label=escape_html(label),
					pct=sc["pct"] * 100,
					arv=_money(sc["arv"]),
					after=_money(sc["after"]),
					repairs_l=escape_html(_("repairs")),
					repairs=_money(sc["repairs"]),
					profit_l=escape_html(_("profit")),
					pp=(sc.get("profit_pct") or 0) * 100,
					fee=_money(sc["fee"]),
					offer=_money(sc["offer"]),
					mao=escape_html(_("MAO")),
				)
			)
			continue
		if sc.get("kind") == "novation":
			parts.append(
				"<div>{label} ({pct:.0f}%)</div>"
				"<div>{arv} − {pct:.0f}% = {after}</div>"
				'<div>− {profit_l} {pp:.0f}% {fee} = <b style="white-space:nowrap">{offer}</b></div>'.format(
					label=escape_html(label),
					pct=sc["pct"] * 100,
					arv=_money(sc["arv"]),
					after=_money(sc["after"]),
					profit_l=escape_html(_("profit")),
					pp=(sc.get("profit_pct") or 0) * 100,
					fee=_money(sc["fee"]),
					offer=_money(sc["offer"]),
				)
			)
			continue
		if sc.get("kind") == "wholetail":
			parts.append(
				"<div>{label} ({pct:.0f}%)</div>"
				"<div>{arv} − {pct:.0f}% = {after}</div>"
				"<div>− {cap_l} {capital} ({rate:.0f}% × {months:.0f} mo)</div>"
				"<div>− {hold_l} {holding}</div>"
				'<div>− {profit_l} {pp:.0f}% {fee} = <b style="white-space:nowrap">{offer}</b></div>'.format(
					label=escape_html(label),
					pct=sc["pct"] * 100,
					arv=_money(sc["arv"]),
					after=_money(sc["after"]),
					cap_l=escape_html(_("capital")),
					capital=_money(sc.get("capital") or 0),
					rate=(sc.get("interest_pct") or 0) * 100,
					months=sc.get("hold_months") or 0,
					hold_l=escape_html(_("holding")),
					holding=_money(sc.get("holding") or 0),
					profit_l=escape_html(_("profit")),
					pp=(sc.get("profit_pct") or 0) * 100,
					fee=_money(sc["fee"]),
					offer=_money(sc["offer"]),
				)
			)
			continue
		if sc.get("kind") == "auction":
			shape = (
				_("{0:.0f}% · 2× repairs").format(sc["pct"] * 100)
				if sc["mult"] == 2
				else _("{0:.0f}%").format(sc["pct"] * 100)
			)
			parts.append(
				"<div>{label} ({shape})</div>"
				"<div>{arv} × {pct:.0f}% = {after}</div>"
				"<div>− {rehab_l} {rehab}</div>"
				"<div>− {liens_l} {liens}</div>"
				"<div>− {tax_l} {tax}</div>"
				"<div>− {profit_l} {pp:.0f}% {fee}</div>"
				'<div>= <b style="white-space:nowrap">{offer}</b> {maxbid}</div>'.format(
					label=escape_html(label),
					shape=escape_html(shape),
					pct=sc["pct"] * 100,
					arv=_money(sc["arv"]),
					after=_money(sc["after"]),
					rehab_l=escape_html(_("repairs")),
					rehab=_money(sc["rehab"]),
					profit_l=escape_html(_("profit")),
					pp=(sc.get("profit_pct") or 0) * 100,
					fee=_money(sc["fee"]),
					liens_l=escape_html(_("liens")),
					liens=_money(sc.get("liens") or 0),
					tax_l=escape_html(_("back taxes")),
					tax=_money(sc.get("back_taxes") or 0),
					offer=_money(sc["offer"]),
					maxbid=escape_html(_("max bid")),
				)
			)
			continue
		# The formula is named on the card. Without it a doubled deduction reads
		# as an arithmetic mistake to anyone reading the timeline later.
		shape = (
			_("{0:.0f}% · 2× repairs").format(sc["pct"] * 100)
			if sc["mult"] == 2
			else _("{0:.0f}%").format(sc["pct"] * 100)
		)
		bill = "{psf}/sf × {sf} sf".format(
			psf=_psf_money(sc["rehab_psf"]), sf=f"{int(sqft):,}" if sqft else "—"
		)
		if sc["mult"] == 2:
			bill += " = {0} × 2".format(_money(sc["repairs"]))
		parts.append(
			"<div>{label} ({shape})</div>"
			"<div>{arv} × {pct:.0f}% = {after}</div>"
			"<div>− {word} {rehab} ({bill})</div>"
			'<div>− {profit_l} {pp:.0f}% {fee} = <b style="white-space:nowrap">{offer}</b></div>'.format(
				label=escape_html(label),
				shape=escape_html(shape),
				word=escape_html(_("repairs") if sc["mult"] == 2 else _("rehab")),
				pct=sc["pct"] * 100,
				arv=_money(sc["arv"]),
				after=_money(sc["after"]),
				rehab=_money(sc["rehab"]),
				bill=escape_html(bill),
				profit_l=escape_html(_("profit")),
				pp=(sc.get("profit_pct") or 0) * 100,
				fee=_money(sc["fee"]),
				offer=_money(sc["offer"]),
			)
		)
	if comps:
		parts.append("<div><b>{}</b></div>".format(escape_html(_("Comps"))))
		for c in comps:
			z = _zillow(c["address"])
			label = escape_html(_street(c["address"]) or c["address"])
			href = z or page
			parts.append(
				'<div><a href="{href}" target="_blank" rel="noopener noreferrer">'
				"{label}</a> {facts}</div>".format(
					href=escape_html(href),
					label=label,
					facts=escape_html(_comp_facts(c)),
				)
			)
	else:
		parts.append("<div>{}</div>".format(escape_html(_("No comps picked."))))
	note = (notes or "").strip()
	if note:
		parts.append(
			"<div><b>{}</b><br>{}</div>".format(
				escape_html(_("Notes")),
				escape_html(note).replace("\n", "<br>"),
			)
		)
	parts.append(
		'<div><a href="{href}" target="_blank" rel="noopener noreferrer">{label}</a></div>'.format(
			href=escape_html(page),
			label=escape_html(_("Tweak calcs")),
		)
	)
	parts.append("</div>")
	return "".join(parts)


@frappe.whitelist()
def save_cash_offer(lead, scenarios=None, comps=None, subject_sqft=None, notes=None):
	"""Write the current cash, novation, list-it, or rental calc onto the lead timeline. Does not touch the desk rail."""
	from crm.api.comps import SCRATCH_DOCTYPE, subject_doctype

	_guard()
	dt = subject_doctype(lead)
	if not frappe.db.exists(dt, lead):
		frappe.throw(_("Lead {0} does not exist.").format(lead), frappe.DoesNotExistError)
	if not frappe.has_permission(dt, "write", lead):
		frappe.throw(_("Not permitted"), frappe.PermissionError)

	if isinstance(scenarios, str):
		scenarios = json.loads(scenarios or "[]")
	if isinstance(comps, str):
		comps = json.loads(comps or "[]")

	sqft = _num(subject_sqft)
	scenes = [s for s in (_scene(x, sqft) for x in (scenarios or [])) if s]
	if not scenes:
		frappe.throw(_("Type a value in at least one scenario first."))

	used = _comps(comps)
	if dt == SCRATCH_DOCTYPE:
		# A scratch property has no activity timeline and no Notes tab. The calc
		# lives on the property itself, newest first, and the latest one seeds the
		# calculator on the next open (see properties.py). When the property hangs
		# off a lead (a second house, a neighbour), the same card is ALSO posted on
		# that lead's timeline, labelled with the property's street, so the rep
		# working the seller sees it without opening Properties.
		from crm.api.properties import linked_lead_and_street, record_offer

		parent, street = linked_lead_and_street(lead)
		label = (street or lead) if parent else ""
		content = _html(lead, scenes, used, sqft, notes or "", label)
		record_offer(lead, _payload(lead, scenes, used, sqft, notes or "", label), content)
		if parent:
			_post_on_lead(parent, content)
			_upsert_wholesale_note(parent, scenes, label)
		return {"ok": True, "comps": len(used), "scenarios": len(scenes), "lead": parent}
	content = _html(lead, scenes, used, sqft, notes or "")
	_post_on_lead(lead, content)
	_upsert_wholesale_note(lead, scenes)
	return {"ok": True, "comps": len(used), "scenarios": len(scenes)}


def _post_on_lead(lead, content):
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
