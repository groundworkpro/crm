import json

import frappe
from frappe import _
from frappe.custom.doctype.property_setter.property_setter import make_property_setter
from frappe.desk.form.assign_to import set_status
from frappe.model import no_value_fields
from frappe.model.document import get_controller
from frappe.query_builder.functions import Count, Max
from frappe.utils import make_filter_tuple
from pypika import Criterion

from crm.api import dispo_buyers
from crm.api.views import get_views
from crm.fcrm.doctype.crm_form_script.crm_form_script import get_form_script
from crm.utils import get_dynamic_linked_docs, get_linked_docs, is_frappe_version

COUNT_NAME = (
	{"COUNT": "name", "as": "total_count"}
	if is_frappe_version("16", above=True)
	else "count(name) as total_count"
)

# Kanban card values that are computed per record in `apply_counts`, not stored.
# They must never reach frappe.get_list, which would treat them as columns.
PSEUDO_FIELDS = (
	"_last_comm",
	"_next_task_due",
	"_first_call",
	"_new_lead_color",
	"_dispo_buyers",
)


@frappe.whitelist()
def sort_options(doctype: str):
	fields = frappe.get_meta(doctype).fields
	fields = [field for field in fields if field.fieldtype not in no_value_fields]
	fields = [
		{
			"label": _(field.label),
			"value": field.fieldname,
			"fieldname": field.fieldname,
		}
		for field in fields
		if field.label and field.fieldname
	]

	standard_fields = [
		{"label": "Name", "fieldname": "name"},
		{"label": "Created On", "fieldname": "creation"},
		{"label": "Last Modified", "fieldname": "modified"},
		{"label": "Modified By", "fieldname": "modified_by"},
		{"label": "Owner", "fieldname": "owner"},
	]

	for field in standard_fields:
		field["label"] = _(field["label"])
		field["value"] = field["fieldname"]
		fields.append(field)

	return fields


@frappe.whitelist()
def get_filterable_fields(doctype: str):
	allowed_fieldtypes = [
		"Check",
		"Data",
		"Float",
		"Int",
		"Currency",
		"Dynamic Link",
		"Link",
		"Long Text",
		"Select",
		"Small Text",
		"Text Editor",
		"Text",
		"Duration",
		"Rating",
		"Date",
		"Datetime",
	]

	c = get_controller(doctype)
	restricted_fields = []
	if hasattr(c, "get_non_filterable_fields"):
		restricted_fields = c.get_non_filterable_fields()

	fields = []

	meta = frappe.get_meta(doctype).as_dict()

	# append standard fields (getting error when using frappe.model.std_fields)
	standard_fields = [
		{"fieldname": "name", "fieldtype": "Link", "label": "Name", "options": doctype},
		{"fieldname": "owner", "fieldtype": "Link", "label": "Created By", "options": "User"},
		{
			"fieldname": "modified_by",
			"fieldtype": "Link",
			"label": "Last Updated By",
			"options": "User",
		},
		{"fieldname": "_user_tags", "fieldtype": "Data", "label": "Tags"},
		{"fieldname": "_liked_by", "fieldtype": "Data", "label": "Like"},
		{"fieldname": "_comments", "fieldtype": "Text", "label": "Comments"},
		{"fieldname": "_assign", "fieldtype": "Text", "label": "Assigned To"},
		{"fieldname": "creation", "fieldtype": "Datetime", "label": "Created On"},
		{"fieldname": "modified", "fieldtype": "Datetime", "label": "Last Updated On"},
	]

	for field in standard_fields + meta.get("fields", []):
		if field.get("fieldname") not in restricted_fields and field.get("fieldtype") in allowed_fieldtypes:
			field["name"] = field.get("fieldname")
			field["label"] = _(field.get("label"))
			field["value"] = field.get("fieldname")
			fields.append(field)

	return fields


@frappe.whitelist()
def get_group_by_fields(doctype: str):
	allowed_fieldtypes = [
		"Check",
		"Data",
		"Float",
		"Int",
		"Currency",
		"Dynamic Link",
		"Link",
		"Select",
		"Duration",
		"Date",
		"Datetime",
	]

	fields = frappe.get_meta(doctype).fields
	fields = [
		field
		for field in fields
		if field.fieldtype not in no_value_fields and field.fieldtype in allowed_fieldtypes
	]
	fields = [
		{
			"label": _(field.label),
			"fieldname": field.fieldname,
		}
		for field in fields
		if field.label and field.fieldname
	]

	standard_fields = [
		{"label": "Name", "fieldname": "name"},
		{"label": "Created On", "fieldname": "creation"},
		{"label": "Last Modified", "fieldname": "modified"},
		{"label": "Modified By", "fieldname": "modified_by"},
		{"label": "Owner", "fieldname": "owner"},
		{"label": "Like", "fieldname": "_liked_by"},
		{"label": "Assigned To", "fieldname": "_assign"},
		{"label": "Comments", "fieldname": "_comments"},
		{"label": "Created On", "fieldname": "creation"},
		{"label": "Modified On", "fieldname": "modified"},
	]

	for field in standard_fields:
		field["label"] = _(field["label"])
		fields.append(field)

	return fields


@frappe.whitelist()
def get_quick_filters(doctype: str, cached: bool = True):
	meta = frappe.get_meta(doctype, cached)
	quick_filters = []

	if global_settings := frappe.db.exists("CRM Global Settings", {"dt": doctype, "type": "Quick Filters"}):
		_quick_filters = frappe.db.get_value("CRM Global Settings", global_settings, "json")
		_quick_filters = json.loads(_quick_filters) or []

		fields = []

		for filter in _quick_filters:
			if filter == "name":
				fields.append({"label": "Name", "fieldname": "name", "fieldtype": "Data"})
			else:
				field = next((f for f in meta.fields if f.fieldname == filter), None)
				if field:
					fields.append(field)

	else:
		fields = [field for field in meta.fields if field.in_standard_filter]

	for field in fields:
		options = field.get("options")
		if field.get("fieldtype") == "Select" and options and isinstance(options, str):
			options = options.split("\n")
			options = [{"label": option, "value": option} for option in options]
			if not any([not option.get("value") for option in options]):
				options.insert(0, {"label": "", "value": ""})
		quick_filters.append(
			{
				"label": _(field.get("label")),
				"fieldname": field.get("fieldname"),
				"fieldtype": field.get("fieldtype"),
				"options": options,
			}
		)

	if doctype == "CRM Lead":
		# Curated Leads quick filters: drop `converted` (internal flag), the
		# Email/Organization fields (rarely searched here; freed for the To-do
		# "Tasks due" control that now lives in the view-controls row) and
		# `source` (asked for by Lance — the full Filter popover covers it, and
		# the row is tight now that Save-as-new sits there).
		_hidden = {"converted", "email", "organization", "source"}
		quick_filters = [filter for filter in quick_filters if filter.get("fieldname") not in _hidden]

	return quick_filters


@frappe.whitelist()
def update_quick_filters(quick_filters: str, old_filters: str, doctype: str):
	quick_filters = json.loads(quick_filters)
	old_filters = json.loads(old_filters)

	new_filters = [filter for filter in quick_filters if filter not in old_filters]
	removed_filters = [filter for filter in old_filters if filter not in quick_filters]

	# update or create global quick filter settings
	create_update_global_settings(doctype, quick_filters)

	# remove old filters
	for filter in removed_filters:
		update_in_standard_filter(filter, doctype, 0)

	# add new filters
	for filter in new_filters:
		update_in_standard_filter(filter, doctype, 1)


def create_update_global_settings(doctype, quick_filters):
	if global_settings := frappe.db.exists("CRM Global Settings", {"dt": doctype, "type": "Quick Filters"}):
		frappe.db.set_value("CRM Global Settings", global_settings, "json", json.dumps(quick_filters))
	else:
		# create CRM Global Settings doc
		doc = frappe.new_doc("CRM Global Settings")
		doc.dt = doctype
		doc.type = "Quick Filters"
		doc.json = json.dumps(quick_filters)
		doc.insert()


def update_in_standard_filter(fieldname, doctype, value):
	if property_name := frappe.db.exists(
		"Property Setter",
		{"doc_type": doctype, "field_name": fieldname, "property": "in_standard_filter"},
	):
		frappe.db.set_value("Property Setter", property_name, "value", value)
	else:
		make_property_setter(
			doctype,
			fieldname,
			"in_standard_filter",
			value,
			"Check",
			validate_fields_for_doctype=False,
		)


# Columns that hold a JSON array inside a Text field, with the LIKE needle each
# one needs. A multi-value filter on these can't be a SQL IN — `_assign` is
# '["a@b.com"]' and `import_lists` is '["LeadPack — Jun 2026"]', so "any of
# these" is an OR of LIKEs. import_lists quotes the needle so "Jun 2026" can't
# match "Jun 2026 (rerun)".
JSON_LIST_FILTER_FIELDS = {
	"_assign": "%{}%",
	"import_lists": '%"{}"%',
}


def expand_json_list_filters(doctype: str, filters: dict):
	"""Rewrite `field in [a, b]` on a JSON-array Text column to `name in [...]`.

	Resolving the OR-of-LIKEs to a concrete name list once, up front, means the
	rest of get_data keeps working with a plain dict filter — which matters
	because those filters are reused by the kanban's per-column queries, the
	per-column counts and the total count, none of which take or_filters.

	Mutates `filters` in place. A no-op unless a multi-value filter is present.
	"""
	if not isinstance(filters, dict):
		return

	for field, needle in JSON_LIST_FILTER_FIELDS.items():
		value = filters.get(field)
		if not isinstance(value, list | tuple) or len(value) != 2:
			continue
		operator = str(value[0]).lower()
		if operator not in ("in", "not in"):
			continue

		wanted = [v for v in frappe.parse_json(value[1]) or [] if v] if value[1] else []
		del filters[field]
		if not wanted:
			# "in nothing" matches nothing; "not in nothing" constrains nothing.
			if operator == "in":
				filters["name"] = ("in", [])
			continue

		matches = frappe.get_all(
			doctype,
			or_filters=[[field, "like", needle.format(v)] for v in wanted],
			pluck="name",
			limit_page_length=0,
		)
		_constrain_names(filters, matches, exclude=(operator == "not in"))


def _constrain_names(filters: dict, names: list, exclude: bool = False):
	"""AND a `name in/not in [...]` constraint into `filters`.

	`name` may already be spoken for — the dashboard drill-down injects one — so
	an existing `in` list is intersected rather than overwritten.
	"""
	names = list(dict.fromkeys(names))
	existing = filters.get("name")

	if isinstance(existing, list | tuple) and len(existing) == 2:
		prior_op = str(existing[0]).lower()
		prior = frappe.parse_json(existing[1]) or []
		if prior_op == "in":
			if exclude:
				blocked = set(names)
				filters["name"] = ("in", [n for n in prior if n not in blocked])
			else:
				allowed = set(names)
				filters["name"] = ("in", [n for n in prior if n in allowed])
			return

	filters["name"] = ("not in", names) if exclude else ("in", names)


@frappe.whitelist()
def get_data(
	doctype: str,
	filters: dict,
	order_by: str,
	page_length: int = 20,
	page_length_count: int = 20,
	column_field: str | None = None,
	title_field: str | None = None,
	columns: str | list | None = None,
	rows: str | list | None = None,
	kanban_columns: str | list | None = None,
	kanban_fields: str | list | None = None,
	view: str | dict | None = None,
	default_filters: dict | None = None,
):
	custom_view = False
	filters = frappe._dict(filters)
	rows = frappe.parse_json(rows or "[]")
	columns = frappe.parse_json(columns or "[]")
	kanban_fields = frappe.parse_json(kanban_fields or "[]")
	kanban_columns = frappe.parse_json(kanban_columns or "[]")

	custom_view_name = view.get("custom_view_name") if view else None
	view_type = view.get("view_type") if view else None
	group_by_field = view.get("group_by_field") if view else None

	for key in filters:
		value = filters[key]
		if isinstance(value, list):
			if "@me" in value:
				value[value.index("@me")] = frappe.session.user
			elif "%@me%" in value:
				index = [i for i, v in enumerate(value) if v == "%@me%"]
				for i in index:
					value[i] = "%" + frappe.session.user + "%"
		elif value == "@me":
			filters[key] = frappe.session.user

	if default_filters:
		default_filters = frappe.parse_json(default_filters)
		filters.update(default_filters)

	# Bulk-imported leads are parked: they carry import_hidden=1 and stay out of
	# the main board/list until promoted. Applied here, on the merged filters, so
	# the exclusion reaches list rows, kanban columns and total_count alike. Any
	# query that filters on an import field (i.e. the auto-created import views)
	# opts out and sees them.
	from crm.api.lead_import import apply_import_visibility

	apply_import_visibility(doctype, filters)

	# Must run AFTER apply_import_visibility, which opts a query out of the
	# parked-lead exclusion by looking for an `import_lists` key that this
	# rewrites away.
	expand_json_list_filters(doctype, filters)

	# "_next_task_due" is a computed pseudo-field (soonest open-task due date),
	# not a DB column, so it can't reach a SQL order_by. Pull the requested
	# direction out and neutralize order_by; the kanban path below re-derives the
	# card order from it per column (list view falls back to modified desc).
	next_task_dir = None
	if order_by and "_next_task_due" in order_by:
		next_task_dir = "desc" if "_next_task_due desc" in order_by else "asc"
		order_by = "modified desc"

	is_default = True
	data = []
	_list = get_controller(doctype)
	default_rows = []
	if hasattr(_list, "default_list_data"):
		default_rows = _list.default_list_data().get("rows")

	meta = frappe.get_meta(doctype)

	if view_type != "kanban":
		if columns or rows:
			custom_view = True
			is_default = False
			columns = frappe.parse_json(columns)
			rows = frappe.parse_json(rows)

		if not columns:
			columns = [
				{"label": "Name", "type": "Data", "key": "name", "width": "16rem"},
				{"label": "Last Modified", "type": "Datetime", "key": "modified", "width": "8rem"},
			]

		if not rows:
			rows = ["name"]

		default_view_filters = {
			"dt": doctype,
			"type": view_type or "list",
			"is_standard": 1,
			"user": frappe.session.user,
		}

		if not custom_view and frappe.db.exists("CRM View Settings", default_view_filters):
			list_view_settings = frappe.get_doc("CRM View Settings", default_view_filters)
			columns = frappe.parse_json(list_view_settings.columns)
			rows = frappe.parse_json(list_view_settings.rows)
			is_default = False
		elif not custom_view or (is_default and hasattr(_list, "default_list_data")):
			rows = default_rows
			columns = _list.default_list_data().get("columns")

		# check if rows has all keys from columns if not add them
		for column in columns:
			if column.get("key") not in rows:
				rows.append(column.get("key"))
			column["label"] = _(column.get("label"))

			if column.get("key") == "_liked_by" and column.get("width") == "10rem":
				column["width"] = "50px"

			# remove column if column.hidden is True
			column_meta = meta.get_field(column.get("key"))
			if column_meta and column_meta.get("hidden"):
				columns.remove(column)

		# check if rows has group_by_field if not add it
		if group_by_field and group_by_field not in rows:
			rows.append(group_by_field)

		data = (
			frappe.get_list(
				doctype,
				fields=rows,
				filters=filters,
				order_by=order_by,
				page_length=page_length,
			)
			or []
		)
		data = parse_list_data(data, doctype)

	if view_type == "kanban":
		if not rows:
			rows = default_rows

		if column_field:
			field_meta = frappe.get_meta(doctype).get_field(column_field)
			live_columns = []
			if field_meta.fieldtype == "Link":
				options_meta = frappe.get_meta(field_meta.options)
				options_order = "position asc" if options_meta.has_field("position") else "modified asc"
				options_fields = ["name", "color"] if options_meta.has_field("color") else ["name"]
				live_columns = frappe.get_all(
					field_meta.options,
					fields=options_fields,
					order_by=options_order,
				)
			elif field_meta.fieldtype == "Select":
				live_columns = [{"name": option} for option in (field_meta.options or "").split("\n")]

			if not kanban_columns:
				kanban_columns = live_columns
			else:
				# saved views snapshot kanban_columns at creation time; statuses
				# added, renamed or deleted since would otherwise never show up
				live_names = {column.get("name") for column in live_columns}
				kanban_columns = [kc for kc in kanban_columns if kc.get("name") in live_names]
				saved_names = {kc.get("name") for kc in kanban_columns}
				kanban_columns += [c for c in live_columns if c.get("name") not in saved_names]

				# the status-settings order (position asc) is the source of truth for
				# column order: re-sort the merged list by each column's live position
				# so reordering statuses or adding a new one in Settings is reflected
				# here, instead of the new status getting stuck at the end. The saved
				# snapshot still contributes per-column state (e.g. the hidden flag).
				live_order = {c.get("name"): i for i, c in enumerate(live_columns)}
				kanban_columns.sort(key=lambda kc: live_order.get(kc.get("name"), len(live_order)))

			# the status record's color wins over any color snapshotted in the view
			live_colors = {c.get("name"): c.get("color") for c in live_columns}
			for kc in kanban_columns:
				if live_colors.get(kc.get("name")):
					kc["color"] = live_colors[kc.get("name")]

		if not title_field:
			title_field = "name"
			if hasattr(_list, "default_kanban_settings"):
				title_field = _list.default_kanban_settings().get("title_field")

		if title_field not in rows:
			rows.append(title_field)

		if not kanban_fields:
			kanban_fields = ["name"]
			if hasattr(_list, "default_kanban_settings"):
				kanban_fields = json.loads(_list.default_kanban_settings().get("kanban_fields"))

		# kanban_fields entries may be a bare fieldname (legacy) or
		# {"fieldname", "label"} when a custom card label is set — only the
		# fieldname is a real DB column to fetch.
		for field in kanban_fields:
			fieldname = field.get("fieldname") if isinstance(field, dict) else field
			if fieldname not in rows:
				rows.append(fieldname)

		# every lead card carries its owner's initials (rendered in the title
		# row, not as a configurable card field), so the column is always fetched
		if doctype == "CRM Lead" and "lead_owner" not in rows:
			rows.append("lead_owner")

		# computed pseudo-fields (filled per-card in apply_counts) — not DB columns,
		# so they must never reach frappe.get_list
		rows = [row for row in rows if row not in PSEUDO_FIELDS]

		all_cards = []

		for kc in kanban_columns:
			column_filters = {column_field: kc.get("name")}
			order = kc.get("order")
			if (column_field in filters and filters.get(column_field) != kc.get("name")) or kc.get("delete"):
				column_data = []
			else:
				column_filters.update(filters.copy())
				page_length = 20

				if kc.get("page_length"):
					page_length = kc.get("page_length")

				# sorting by next-task-due overrides any manual drag order: derive
				# the card order from each card's soonest open task (undated last)
				if next_task_dir:
					order = get_next_task_due_order(doctype, column_filters.copy(), next_task_dir)

				if order:
					column_data = get_records_based_on_order(
						doctype, rows, column_filters, page_length, order
					)
				else:
					column_data = frappe.get_list(
						doctype,
						fields=rows,
						filters=convert_filter_to_tuple(doctype, column_filters),
						order_by=order_by,
						page_length=page_length,
					)

				new_filters = filters.copy()
				new_filters.update({column_field: kc.get("name")})

				all_count = frappe.get_list(
					doctype,
					filters=convert_filter_to_tuple(doctype, new_filters),
					fields=[COUNT_NAME],
				)[0].total_count

				kc["all_count"] = all_count
				kc["count"] = len(column_data)

				all_cards.extend(column_data)

			if order:
				column_data = sorted(
					column_data,
					key=lambda x: order.index(x.get("name")) if x.get("name") in order else len(order),
				)

			data.append({"column": kc, "fields": kanban_fields, "data": column_data})

		# One pass over every card on the board rather than per-column-per-card:
		# the pseudo-field queries are grouped by record, so filling them for the
		# whole board costs the same as filling them for a single column.
		apply_counts(all_cards, doctype)

	fields = frappe.get_meta(doctype).fields
	fields = [field for field in fields if field.fieldtype not in no_value_fields]
	fields = [
		{
			"label": _(field.label),
			"fieldtype": field.fieldtype,
			"fieldname": field.fieldname,
			"options": field.options,
		}
		for field in fields
		if field.label and field.fieldname
	]

	std_fields = [
		{"label": "Name", "fieldtype": "Data", "fieldname": "name"},
		{"label": "Created On", "fieldtype": "Datetime", "fieldname": "creation"},
		{"label": "Last Modified", "fieldtype": "Datetime", "fieldname": "modified"},
		{
			"label": "Modified By",
			"fieldtype": "Link",
			"fieldname": "modified_by",
			"options": "User",
		},
		{"label": "Assigned To", "fieldtype": "Text", "fieldname": "_assign"},
		{"label": "Owner", "fieldtype": "Link", "fieldname": "owner", "options": "User"},
		{"label": "Like", "fieldtype": "Data", "fieldname": "_liked_by"},
	]

	for field in std_fields:
		if field.get("fieldname") not in rows:
			rows.append(field.get("fieldname"))
		if field not in fields:
			field["label"] = _(field["label"])
			fields.append(field)

	if not is_default and custom_view_name:
		is_default = frappe.db.get_value("CRM View Settings", custom_view_name, "load_default_columns")

	if group_by_field and view_type == "group_by":

		def get_options(type, options):
			if type == "Select":
				return [option for option in options.split("\n")]
			else:
				has_empty_values = any([not d.get(group_by_field) for d in data])
				options = list(set([d.get(group_by_field) for d in data]))
				options = [u for u in options if u]
				if has_empty_values:
					options.append("")

				if order_by and group_by_field in order_by:
					order_by_fields = order_by.split(",")
					order_by_fields = [
						(field.split(" ")[0], field.split(" ")[1]) for field in order_by_fields
					]
					if (group_by_field, "asc") in order_by_fields:
						options.sort()
					elif (group_by_field, "desc") in order_by_fields:
						options.sort(reverse=True)
				else:
					options.sort()
				return options

		for field in fields:
			if field.get("fieldname") == group_by_field:
				group_by_field = {
					"label": field.get("label"),
					"fieldname": field.get("fieldname"),
					"fieldtype": field.get("fieldtype"),
					"options": get_options(field.get("fieldtype"), field.get("options")),
				}

	return {
		"data": data,
		"doctype": doctype,
		"columns": columns,
		"rows": rows,
		"fields": fields,
		"column_field": column_field,
		"title_field": title_field,
		"kanban_columns": kanban_columns,
		"kanban_fields": kanban_fields,
		"group_by_field": group_by_field,
		"page_length": page_length,
		"page_length_count": page_length_count,
		"is_default": is_default,
		"views": get_views(doctype),
		"total_count": frappe.get_list(doctype, filters=filters, fields=[COUNT_NAME])[0].total_count,
		"row_count": len(data),
		"form_script": get_form_script(doctype),
		"list_script": get_form_script(doctype, "List"),
		"view_type": view_type,
	}


def parse_list_data(data, doctype):
	_list = get_controller(doctype)
	if hasattr(_list, "parse_list_data"):
		data = _list.parse_list_data(data)
	return data


def convert_filter_to_tuple(doctype, filters):
	if isinstance(filters, dict):
		filters_items = filters.items()
		filters = []
		for key, value in filters_items:
			filters.append(make_filter_tuple(doctype, key, value))
	return filters


def get_records_based_on_order(doctype, rows, filters, page_length, order):
	records = []
	filters = convert_filter_to_tuple(doctype, filters)
	in_filters = filters.copy()
	in_filters.append([doctype, "name", "in", order[:page_length]])
	records = frappe.get_list(
		doctype,
		fields=rows,
		filters=in_filters,
		order_by="creation desc",
		page_length=page_length,
	)

	if len(records) < page_length:
		not_in_filters = filters.copy()
		not_in_filters.append([doctype, "name", "not in", order])
		remaining_records = frappe.get_list(
			doctype,
			fields=rows,
			filters=not_in_filters,
			order_by="creation desc",
			page_length=page_length - len(records),
		)
		for record in remaining_records:
			records.append(record)

	return records


def get_next_task_due_order(doctype, column_filters, direction="asc"):
	"""Card names (Lead/Deal) matching `column_filters`, ordered by their soonest
	open-task due date — the kanban "_next_task_due" pseudo-field. Cards with an
	open dated task sort by due date (asc/desc per `direction`); cards with no
	open task sink to the bottom. Returned as a name list the kanban `order`
	machinery (get_records_based_on_order) consumes."""
	names = frappe.get_list(
		doctype,
		filters=convert_filter_to_tuple(doctype, column_filters),
		pluck="name",
		limit_page_length=0,
	)
	if not names:
		return []

	# earliest open-task due date per card: fetch dated open tasks ascending and
	# keep the first (= soonest) seen per card. Avoids a SQL aggregate/group_by.
	task_rows = frappe.get_all(
		"CRM Task",
		filters={
			"reference_doctype": doctype,
			"reference_docname": ("in", names),
			"status": ("not in", ["Done", "Canceled"]),
			"due_date": (">", ""),
		},
		fields=["reference_docname", "due_date"],
		order_by="due_date asc",
	)
	due_map = {}
	for r in task_rows:
		due_map.setdefault(r.reference_docname, r.due_date)

	dated = sorted(
		(n for n in names if n in due_map),
		key=lambda n: due_map[n],
		reverse=(direction == "desc"),
	)
	undated = [n for n in names if n not in due_map]
	return dated + undated


@frappe.whitelist()
def remove_assignments(doctype: str, name: str, assignees: str | list, ignore_permissions: bool = False):
	assignees = frappe.parse_json(assignees)

	if not assignees:
		return

	for assign_to in assignees:
		set_status(
			doctype,
			name,
			todo=None,
			assign_to=assign_to,
			status="Cancelled",
			ignore_permissions=ignore_permissions,
		)


@frappe.whitelist()
def get_assigned_users(doctype: str, name: str, default_assigned_to: str | None = None):
	assigned_users = frappe.get_all(
		"ToDo",
		fields=["allocated_to"],
		filters={
			"reference_type": doctype,
			"reference_name": name,
			"status": ("!=", "Cancelled"),
		},
		pluck="allocated_to",
	)

	users = list(set(assigned_users))

	# if users is empty, add default_assigned_to
	if not users and default_assigned_to:
		users = [default_assigned_to]
	return users


@frappe.whitelist()
def get_fields(doctype: str, allow_all_fieldtypes: bool = False):
	not_allowed_fieldtypes = [*list(frappe.model.no_value_fields), "Read Only"]
	if allow_all_fieldtypes:
		not_allowed_fieldtypes = []
	fields = frappe.get_meta(doctype).fields

	_fields = []

	for field in fields:
		if field.fieldtype not in not_allowed_fieldtypes and field.fieldname:
			_fields.append(field)

	return _fields


# ---------------------------------------------------------------------------
# Per-card pseudo-fields (kanban badges).
#
# These used to be computed one card at a time: getCounts(d) fired ~18 separate
# queries per record, so a 140-card Leads kanban cost ~2,600 round-trips and
# get_data took ~2.6s (the list view, which skips this entirely, takes ~80ms).
# Every filter keystroke paid that twice. Everything here keys off
# reference_docname/reference_name, so one grouped query per source now serves
# the whole board regardless of how many cards are on it.
# ---------------------------------------------------------------------------

# (result key, doctype, link field, extra filters) for the plain counts.
_COUNT_SOURCES = (
	("_comment_count", "Comment", "reference_name", {"comment_type": "Comment"}),
	("_task_count", "CRM Task", "reference_docname", {}),
	("_note_count", "FCRM Note", "reference_docname", {}),
)


def _count_by_doc(dt, link_field, doctype, names, extra=None, split_field=None):
	"""{docname: count} — or {(docname, split_value): count} when `split_field`
	is given — in a single grouped query."""
	if not names:
		return {}

	table = frappe.qb.DocType(dt)
	key = table[link_field]
	query = frappe.qb.from_(table).where(
		(table.reference_doctype == doctype) & key.isin(names)
	)
	for field, value in (extra or {}).items():
		query = query.where(table[field] == value)

	if split_field:
		split = table[split_field]
		rows = (
			query.select(key.as_("_key"), split.as_("_split"), Count("*").as_("_count"))
			.groupby(key, split)
			.run(as_dict=True)
		)
		return {(r["_key"], r["_split"]): r["_count"] for r in rows}

	rows = query.select(key.as_("_key"), Count("*").as_("_count")).groupby(key).run(as_dict=True)
	return {r["_key"]: r["_count"] for r in rows}


def _max_by_doc(dt, link_field, doctype, names, value_field, extra=None):
	"""{docname: max(value_field)} in a single grouped query."""
	if not names:
		return {}

	table = frappe.qb.DocType(dt)
	key = table[link_field]
	query = frappe.qb.from_(table).where(
		(table.reference_doctype == doctype) & key.isin(names)
	)
	for field, value in (extra or {}).items():
		query = query.where(table[field] == value)

	rows = (
		query.select(key.as_("_key"), Max(table[value_field]).as_("_value"))
		.groupby(key)
		.run(as_dict=True)
	)
	return {r["_key"]: r["_value"] for r in rows if r["_value"]}


def apply_counts(rows, doctype):
	"""Fill the kanban pseudo-fields on a whole page of records at once.

	Mutates `rows` in place (the caller holds the same dicts) and returns it.
	"""
	if not rows:
		return rows

	names = [d.get("name") for d in rows if d.get("name")]
	if not names:
		return rows

	# Quo Message is a site-resident custom doctype — guard so a site without it
	# doesn't 500 the kanban.
	has_quo_message = bool(frappe.db.exists("DocType", "Quo Message"))

	counts = {
		key: _count_by_doc(dt, link_field, doctype, names, extra)
		for key, dt, link_field, extra in _COUNT_SOURCES
	}

	# Emails: total spans Communication + Automated Message, but the
	# outbound/inbound split counts real Communications only (an automated
	# message has no meaningful sent_or_received for these badges).
	email_by_type = _count_by_doc(
		"Communication", "reference_name", doctype, names, split_field="communication_type"
	)
	email_by_direction = _count_by_doc(
		"Communication",
		"reference_name",
		doctype,
		names,
		extra={"communication_type": "Communication"},
		split_field="sent_or_received",
	)
	calls_by_type = _count_by_doc(
		"CRM Call Log", "reference_docname", doctype, names, split_field="type"
	)
	texts_by_direction = (
		_count_by_doc("Quo Message", "reference_docname", doctype, names, split_field="direction")
		if has_quo_message
		else {}
	)

	last_email = _max_by_doc(
		"Communication",
		"reference_name",
		doctype,
		names,
		"communication_date",
		extra={"communication_type": "Communication"},
	)
	last_call = _max_by_doc("CRM Call Log", "reference_docname", doctype, names, "start_time")
	last_text = (
		_max_by_doc("Quo Message", "reference_docname", doctype, names, "message_date")
		if has_quo_message
		else {}
	)

	# Soonest due date among each card's still-open tasks — drives the kanban
	# "next task due" badge (frontend colors it red when overdue, amber today).
	# Fetched ascending and first-seen-wins per card, mirroring
	# get_next_task_due_order rather than adding a MIN() aggregate.
	next_task_due = {}
	for row in frappe.get_all(
		"CRM Task",
		filters={
			"reference_doctype": doctype,
			"reference_docname": ("in", names),
			"status": ("not in", ["Done", "Canceled"]),
			"due_date": (">", ""),
		},
		fields=["reference_docname", "due_date"],
		order_by="due_date asc",
	):
		next_task_due.setdefault(row.reference_docname, row.due_date)

	lead_meta = _lead_card_meta(doctype, names)

	for d in rows:
		name = d.get("name")

		d["_comment_count"] = counts["_comment_count"].get(name, 0)
		d["_task_count"] = counts["_task_count"].get(name, 0)
		d["_note_count"] = counts["_note_count"].get(name, 0)
		d["_email_count"] = email_by_type.get((name, "Communication"), 0) + email_by_type.get(
			(name, "Automated Message"), 0
		)
		d["_email_out_count"] = email_by_direction.get((name, "Sent"), 0)
		d["_email_in_count"] = email_by_direction.get((name, "Received"), 0)
		d["_call_out_count"] = calls_by_type.get((name, "Outgoing"), 0)
		d["_call_in_count"] = calls_by_type.get((name, "Incoming"), 0)
		d["_text_out_count"] = texts_by_direction.get((name, "Outgoing"), 0)
		d["_text_in_count"] = texts_by_direction.get((name, "Incoming"), 0)

		comm_dates = [
			dt
			for dt in (last_email.get(name), last_call.get(name), last_text.get(name))
			if dt
		]
		d["_last_comm"] = max(comm_dates) if comm_dates else None

		d["_next_task_due"] = next_task_due.get(name)

		meta = lead_meta.get(name, {})
		d["_first_call"] = meta.get("_first_call", "|")
		d["_new_lead_color"] = meta.get("_new_lead_color", "")
		d["_dispo_buyers"] = meta.get("_dispo_buyers")

	return rows


def _lead_card_meta(doctype, names):
	"""{lead: {_first_call, _new_lead_color}} for CRM Lead cards.

	_first_call is the First-Call Read 2x2 chip ("motivated|on_price", e.g.
	"Yes|No"); blank on either axis = not qualified yet.

	_new_lead_color is the age tint: a lead sitting in "New" is colored purely by
	how long it's been there — amber on the day it was created, red once it's a
	day or more old and STILL "New" — regardless of any contact activity or open
	tasks. Empty for any other status (those cards keep the task-due tint).
	Calendar-day math in the site timezone (creation is stored site-tz).
	"""
	if doctype != "CRM Lead" or not names:
		return {}

	from frappe.utils import getdate

	has_first_call = frappe.db.has_column("CRM Lead", "first_call_motivated")
	fields = ["name", "status", "creation"]
	if has_first_call:
		fields += ["first_call_motivated", "first_call_on_price"]
	# Location for the disposition-buyer badges. Guarded because these are custom
	# fields: a site without them still gets a board, just no badges.
	has_location = frappe.db.has_column("CRM Lead", "property_state")
	if has_location:
		fields += ["property_city", "property_state", "property_county"]

	leads = frappe.get_all("CRM Lead", filters={"name": ("in", names)}, fields=fields)

	meta = {}
	for lead in leads:
		first_call = "|"
		if has_first_call:
			first_call = f"{lead.get('first_call_motivated') or ''}|{lead.get('first_call_on_price') or ''}"

		color = ""
		if lead.status == "New" and lead.creation:
			color = "red" if getdate(lead.creation) < getdate() else "amber"

		# Pure in-memory dict lookups against a bundled snapshot -- no query, no
		# network -- so this costs nothing per card. `summary` returns None when
		# neither buyer covers the area, which keeps "No" off the wire for the
		# ~42% of leads nobody buys in.
		buyers = None
		if has_location:
			buyers = dispo_buyers.summary(
				lead.get("property_city"),
				lead.get("property_state"),
				lead.get("property_county"),
			)

		meta[lead.name] = {
			"_first_call": first_call,
			"_new_lead_color": color,
			"_dispo_buyers": buyers,
		}

	return meta


def getCounts(d, doctype):
	"""Single-record wrapper around apply_counts (kept for external callers)."""
	apply_counts([d], doctype)
	return d


@frappe.whitelist()
def get_kanban_card(doctype: str, name: str, rows: str | list | None = None):
	"""One kanban card, refreshed — the same shape `get_data` puts in a column.

	Exists so a realtime nudge (a task ticked, a First-Call Read saved) can update
	the ONE card it concerns instead of refetching the whole board. A full board
	fetch is ~300KB and a few hundred ms of server time, and `crm_task_update` is
	broadcast site-wide — so every task anyone completed used to re-render every
	open Leads board in the company.

	Returns None when the record is gone or the caller can't see it; the client
	treats that as "fall back to a full reload", which also covers the card having
	moved to a column this board isn't showing.
	"""
	rows = frappe.parse_json(rows or "[]")

	# Only ever select real columns: pseudo-fields are computed below, and an
	# arbitrary client-supplied string has no business reaching the query builder.
	meta = frappe.get_meta(doctype)
	allowed = {"name", "owner", "creation", "modified", "modified_by", "_assign", "_liked_by"}
	fields = [
		row
		for row in rows
		if row not in PSEUDO_FIELDS and (row in allowed or meta.get_field(row))
	]
	if "name" not in fields:
		fields.append("name")

	# get_list applies permissions and permission query conditions, so a user only
	# ever refreshes a card they were entitled to see in the first place.
	records = frappe.get_list(doctype, fields=fields, filters={"name": name}, page_length=1)
	if not records:
		return None

	apply_counts(records, doctype)
	return records[0]


@frappe.whitelist()
def get_docs_with_due_tasks(doctype: str, scope: str = "today_overdue"):
	"""Names of `doctype` docs (CRM Lead / CRM Deal) that have at least one open
	task (status not Done/Canceled) due in `scope`. Feeds the kanban "tasks due"
	filter as a `name in [...]` injection. scope: 'today' | 'overdue' |
	'today_overdue'."""
	from frappe.utils import add_days, get_datetime, now_datetime, today

	now = now_datetime()
	start_today = get_datetime(today())  # today 00:00:00
	end_today = add_days(start_today, 1)  # tomorrow 00:00:00 (exclusive)

	filters = [
		["reference_doctype", "=", doctype],
		["status", "not in", ["Done", "Canceled"]],
	]
	if scope == "overdue":
		filters.append(["due_date", "<", now])
	elif scope == "today":
		filters.append(["due_date", ">=", start_today])
		filters.append(["due_date", "<", end_today])
	else:  # today_overdue — anything due up to end of today
		filters.append(["due_date", "is", "set"])
		filters.append(["due_date", "<", end_today])

	names = frappe.get_all("CRM Task", filters=filters, pluck="reference_docname", distinct=True)
	return list({n for n in names if n})


@frappe.whitelist()
def get_linked_docs_of_document(doctype: str, docname: str):
	try:
		doc = frappe.get_doc(doctype, docname)
	except frappe.DoesNotExistError:
		return []

	linked_docs = get_linked_docs(doc)
	dynamic_linked_docs = get_dynamic_linked_docs(doc)

	linked_docs.extend(dynamic_linked_docs)
	linked_docs = list({doc["reference_docname"]: doc for doc in linked_docs}.values())

	docs_data = []
	for doc in linked_docs:
		if not doc.get("reference_doctype") or not doc.get("reference_docname"):
			continue

		try:
			data = frappe.get_doc(doc["reference_doctype"], doc["reference_docname"])
		except (frappe.DoesNotExistError, frappe.ValidationError):
			continue

		title = data.get("title")
		if data.doctype == "CRM Call Log":
			title = f"Call from {data.get('from')} to {data.get('to')}"

		if data.doctype == "CRM Deal":
			title = data.get("organization")

		if data.doctype == "CRM Notification":
			title = data.get("message")

		docs_data.append(
			{
				"doc": data.doctype,
				"title": title or data.get("name"),
				"reference_docname": doc["reference_docname"],
				"reference_doctype": doc["reference_doctype"],
			}
		)
	return docs_data


def remove_doc_link(doctype, docname):
	if not doctype or not docname:
		return

	try:
		linked_doc_data = frappe.get_doc(doctype, docname)
		if doctype == "CRM Notification":
			delete_notification_type = {
				"notification_type_doctype": "",
				"notification_type_doc": "",
			}
			delete_references = {
				"reference_doctype": "",
				"reference_name": "",
			}
			if linked_doc_data.get("notification_type_doctype") == linked_doc_data.get("reference_doctype"):
				delete_references.update(delete_notification_type)

			linked_doc_data.update(delete_references)
		else:
			linked_doc_data.update(
				{
					"reference_doctype": "",
					"reference_docname": "",
				}
			)
		linked_doc_data.save(ignore_permissions=True)
	except (frappe.DoesNotExistError, frappe.ValidationError):
		pass


def remove_contact_link(doctype, docname):
	if not doctype or not docname:
		return

	try:
		linked_doc_data = frappe.get_doc(doctype, docname)
		linked_doc_data.update(
			{
				"contact": None,
				"contacts": [],
			}
		)
		linked_doc_data.save(ignore_permissions=True)
	except (frappe.DoesNotExistError, frappe.ValidationError):
		pass


@frappe.whitelist()
def remove_linked_doc_reference(items: str | list, remove_contact: bool = False, delete: bool = False):
	if isinstance(items, str):
		items = frappe.parse_json(items)

	for item in items:
		if not item.get("doctype") or not item.get("docname"):
			continue

		if not frappe.has_permission(item["doctype"], "write", item["docname"]):
			continue

		try:
			if remove_contact:
				remove_contact_link(item["doctype"], item["docname"])
			else:
				remove_doc_link(item["doctype"], item["docname"])
			if delete:
				frappe.delete_doc(item["doctype"], item["docname"])
		except (frappe.DoesNotExistError, frappe.ValidationError):
			# Skip if document doesn't exist or has validation errors
			continue

	return "success"


@frappe.whitelist()
def delete_bulk_docs(doctype: str, items: str | list, delete_linked: bool = False):
	from frappe.desk.reportview import delete_bulk

	if not doctype:
		frappe.throw(_("Doctype is required"))

	if not items:
		frappe.throw(_("Items are required"))

	items = frappe.parse_json(items)
	if not isinstance(items, list):
		frappe.throw(_("Items must be a list"))

	for doc in items:
		try:
			if not frappe.db.exists(doctype, doc):
				frappe.log_error(f"Document {doctype} {doc} does not exist", "Bulk Delete Error")
				continue

			linked_docs = get_linked_docs_of_document(doctype, doc)
			for linked_doc in linked_docs:
				if not linked_doc.get("reference_doctype") or not linked_doc.get("reference_docname"):
					continue

				remove_linked_doc_reference(
					[
						{
							"doctype": linked_doc["reference_doctype"],
							"docname": linked_doc["reference_docname"],
						}
					],
					remove_contact=doctype == "Contact",
					delete=delete_linked,
				)
		except Exception as e:
			frappe.log_error(f"Error processing linked docs for {doctype} {doc}: {e!s}", "Bulk Delete Error")

	if len(items) > 10:
		frappe.enqueue("frappe.desk.reportview.delete_bulk", doctype=doctype, items=items)
	else:
		delete_bulk(doctype, items)
	return "success"
