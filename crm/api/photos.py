"""Per-lead property photos, stored in the shared "Wholesaling" Google Drive.

A user opens **Photos** on a lead → we find (or create) a folder named after the
property address inside `Wholesaling > Info & Photos`, upload straight into it,
and hand back a gallery the team can scroll, download as a zip, or share as a
plain Drive link.

Design notes worth knowing before you change anything here:

  - **Drive is the only home for the bytes.** Nothing is mirrored into Frappe's
    File table — the CRM just indexes what's in the folder. That keeps the site's
    disk flat and means a photo deleted in Drive is really gone.
  - **The Google call lives in app code**, not an ops server script, for the same
    reason as `underwriting.py`: a server-script sandbox can't sign the OAuth2
    JWT needed to mint a Google token. We reuse `underwriting._google_access_token`
    outright rather than duplicating the JWT dance.
  - **The parent is `Info & Photos` itself** (not its `Photos` subfolder) — Lance's
    call. That folder is a mixed bag of loose "… Property Info" Google Docs, a
    `Deed …` folder and a legacy `Photos` folder, so folder *adoption* matches on
    **folders only**, by normalized address, and never touches those docs.
  - **Link sharing is set at folder creation** (`type=anyone, role=reader`), which
    every file inside then inherits — so the gallery can render thumbnails and a
    listing agent can open the link without a Google login. The Wholesaling shared
    drive has `domainUsersOnly: false`, so this is permitted.

The folder id is cached on the lead (`photo_folder_id` / `photo_folder_url`) when
those custom fields exist, so a later address edit doesn't orphan the folder. Both
are `has_field`-guarded: without the ops script we simply re-resolve by name each
time and everything still works.
"""

import io
import json
import mimetypes
import re
import zipfile

import requests

import frappe
from frappe import _

# `Wholesaling > Info & Photos`. Overridable via site config if it ever moves.
INFO_PHOTOS_FOLDER_ID = "1myTT1jCSr2yyKwDnHtOh2NVSHnIP7jzh"
WHOLESALING_DRIVE_ID = "0ACiri-22KJSxUk9PVA"

FOLDER_MIME = "application/vnd.google-apps.folder"
DRIVE_FILES = "https://www.googleapis.com/drive/v3/files"
DRIVE_UPLOAD = "https://www.googleapis.com/upload/drive/v3/files"

# Photos + walkthrough clips. Anything else is refused with a clear message
# rather than silently landing in the property folder.
ALLOWED_PREFIXES = ("image/", "video/")

# "Download all" zips in memory on the backend. A cap keeps one click from
# pinning the box — past this we tell the user to use the Drive link instead.
MAX_ZIP_BYTES = 400 * 1024 * 1024

LEAD_CACHE_FIELDS = ("photo_folder_id", "photo_folder_url")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _token() -> str:
	"""Reuse the underwriting service account (already a full member of the
	Wholesaling shared drive — canAddChildren/canShare verified)."""
	from crm.api.underwriting import _google_access_token

	return _google_access_token()


def _headers(token: str, json_body: bool = False) -> dict:
	h = {"Authorization": f"Bearer {token}"}
	if json_body:
		h["Content-Type"] = "application/json; charset=UTF-8"
	return h


def _parent_folder_id() -> str:
	return frappe.conf.get("lead_photos_folder_id") or INFO_PHOTOS_FOLDER_ID


def _lead_has(field: str) -> bool:
	return frappe.get_meta("CRM Lead").has_field(field)


def _check(lead: str, ptype: str = "read"):
	if not frappe.db.exists("CRM Lead", lead):
		frappe.throw(_("Lead not found"), frappe.DoesNotExistError)
	if not frappe.has_permission("CRM Lead", ptype, lead):
		frappe.throw(_("Not permitted"), frappe.PermissionError)


def _address(lead: str) -> str:
	"""The folder name: the full property address, composed the same way
	agreements compose it (street + city + state + zip) so a manually-entered
	street-only lead still gets a fully-qualified folder name."""
	doc = frappe.get_doc("CRM Lead", lead)
	try:
		from crm.api.agreement import _full_property_address

		addr = (_full_property_address(doc) or "").strip()
		if addr:
			return addr
	except Exception:
		pass
	return (doc.get("property_address") or "").strip()


def _normalize(name: str) -> str:
	"""Compare folder names loosely: case/punctuation-insensitive, with a trailing
	"photos" dropped — the folders already in Drive are inconsistent about it
	("3321 Hennepin Ave, Minneapolis, MN - Photos" vs plain addresses)."""
	n = re.sub(r"[^a-z0-9]+", " ", (name or "").lower()).strip()
	n = re.sub(r"\bphotos?\b$", "", n).strip()
	return n


def _sanitize(name: str) -> str:
	"""Drive tolerates most characters; slashes are the practical problem."""
	return re.sub(r"\s+", " ", (name or "").replace("/", "-")).strip()


def _drive_get(url: str, token: str, **params):
	params.setdefault("supportsAllDrives", "true")
	r = requests.get(url, params=params, headers=_headers(token), timeout=60)
	r.raise_for_status()
	return r.json()


# ---------------------------------------------------------------------------
# Folder resolution
# ---------------------------------------------------------------------------
def _find_existing_folder(token: str, address: str):
	"""An address-named FOLDER already sitting in Info & Photos, or None.

	Deliberately folder-only: the same parent holds "… Property Info" Google Docs
	whose names contain the very same address, and adopting one of those would be
	a quiet disaster.
	"""
	parent = _parent_folder_id()
	j = _drive_get(
		DRIVE_FILES,
		token,
		q=f"'{parent}' in parents and mimeType='{FOLDER_MIME}' and trashed=false",
		corpora="drive",
		driveId=WHOLESALING_DRIVE_ID,
		includeItemsFromAllDrives="true",
		fields="files(id,name,webViewLink)",
		pageSize=1000,
	)
	want = _normalize(address)
	if not want:
		return None
	for f in j.get("files", []):
		if _normalize(f.get("name")) == want:
			return f
	return None


def _share_anyone(token: str, file_id: str):
	"""Anyone with the link can view + download. Files inherit this from the
	folder, so we only ever set it once, at folder creation."""
	requests.post(
		f"{DRIVE_FILES}/{file_id}/permissions",
		params={"supportsAllDrives": "true"},
		headers=_headers(token, json_body=True),
		json={"role": "reader", "type": "anyone"},
		timeout=30,
	).raise_for_status()


def _create_folder(token: str, address: str) -> dict:
	r = requests.post(
		DRIVE_FILES,
		params={"supportsAllDrives": "true", "fields": "id,name,webViewLink"},
		headers=_headers(token, json_body=True),
		json={
			"name": _sanitize(address),
			"mimeType": FOLDER_MIME,
			"parents": [_parent_folder_id()],
		},
		timeout=60,
	)
	r.raise_for_status()
	folder = r.json()
	_share_anyone(token, folder["id"])
	return folder


def _cache_on_lead(lead: str, folder: dict):
	values = {}
	if _lead_has("photo_folder_id"):
		values["photo_folder_id"] = folder.get("id")
	if _lead_has("photo_folder_url"):
		values["photo_folder_url"] = folder.get("webViewLink") or _folder_url(folder.get("id"))
	if values:
		frappe.db.set_value("CRM Lead", lead, values, update_modified=False)


def _folder_url(folder_id: str) -> str:
	return f"https://drive.google.com/drive/folders/{folder_id}"


def _resolve_folder(token: str, lead: str, create: bool = False):
	"""The lead's photo folder: cached id → adopt by name → (optionally) create."""
	cached = (
		frappe.db.get_value("CRM Lead", lead, "photo_folder_id")
		if _lead_has("photo_folder_id")
		else None
	)
	if cached:
		try:
			f = _drive_get(f"{DRIVE_FILES}/{cached}", token, fields="id,name,webViewLink,trashed")
			if not f.get("trashed"):
				return f
		except requests.HTTPError:
			pass  # folder deleted/moved in Drive — fall through and re-resolve

	address = _address(lead)
	if not address:
		frappe.throw(_("Set a property address first — the photo folder is named after it."))

	found = _find_existing_folder(token, address)
	if found:
		_cache_on_lead(lead, found)
		return found

	if not create:
		return None

	folder = _create_folder(token, address)
	_cache_on_lead(lead, folder)
	return folder


def _publish(lead: str):
	frappe.publish_realtime(
		"crm_photos",
		{"reference_doctype": "CRM Lead", "reference_docname": lead},
		after_commit=True,
	)


# ---------------------------------------------------------------------------
# File listing
# ---------------------------------------------------------------------------
def _shape(f: dict) -> dict:
	"""One Drive file → what the gallery needs.

	Thumbnails come from `drive.google.com/thumbnail`, which works without a
	Google session precisely because the folder is link-shared — Drive's own
	`thumbnailLink` is short-lived and often blocked by referrer policy.
	"""
	fid = f.get("id")
	mime = f.get("mimeType") or ""
	is_video = mime.startswith("video/")
	return {
		"id": fid,
		"name": f.get("name"),
		"mime_type": mime,
		"is_video": is_video,
		"size": int(f.get("size") or 0),
		"created": f.get("createdTime"),
		"thumb": f"https://drive.google.com/thumbnail?id={fid}&sz=w400",
		"full": f"https://drive.google.com/thumbnail?id={fid}&sz=w1600",
		"view": f.get("webViewLink") or f"https://drive.google.com/file/d/{fid}/view",
		"download": f"https://drive.google.com/uc?export=download&id={fid}",
		# set when the photo was saved from a text/email (see save_media_to_photos)
		"source": (f.get("appProperties") or {}).get("crm_source") or "",
	}


def _list_files(token: str, folder_id: str) -> list:
	files, page = [], None
	while True:
		params = {
			"q": f"'{folder_id}' in parents and trashed=false",
			"corpora": "drive",
			"driveId": WHOLESALING_DRIVE_ID,
			"includeItemsFromAllDrives": "true",
			"supportsAllDrives": "true",
			"orderBy": "createdTime",
			"fields": "nextPageToken,files(id,name,mimeType,size,createdTime,webViewLink,appProperties)",
			"pageSize": 1000,
		}
		if page:
			params["pageToken"] = page
		j = requests.get(DRIVE_FILES, params=params, headers=_headers(token), timeout=60)
		j.raise_for_status()
		j = j.json()
		files.extend(j.get("files", []))
		page = j.get("nextPageToken")
		if not page:
			break
	# Sub-folders aren't photos; skip them rather than rendering a broken tile.
	return [_shape(f) for f in files if f.get("mimeType") != FOLDER_MIME]


def _upload_bytes(token: str, folder_id: str, name: str, mime: str, data: bytes, source: str = "") -> dict:
	"""Resumable Drive upload: metadata first, then the bytes to the session URI.

	`source` (see `save_media_to_photos`) is stamped into the file's
	appProperties so the activity feed can tell which texted/emailed pictures
	are already in the folder and never saves the same one twice.
	"""
	meta = {"name": _sanitize(name or "photo"), "parents": [folder_id]}
	if source:
		meta["appProperties"] = {"crm_source": source}
	try:
		start = requests.post(
			DRIVE_UPLOAD,
			params={
				"uploadType": "resumable",
				"supportsAllDrives": "true",
				"fields": "id,name,mimeType,size,createdTime,webViewLink,appProperties",
			},
			headers={**_headers(token, json_body=True), "X-Upload-Content-Type": mime},
			json=meta,
			timeout=60,
		)
		start.raise_for_status()
		session_uri = start.headers.get("Location")
		if not session_uri:
			frappe.throw(_("Google didn't start the upload."))

		put = requests.put(
			session_uri,
			headers={"Content-Type": mime},
			data=data,
			timeout=600,
		)
		put.raise_for_status()
		created = put.json()
	except frappe.ValidationError:
		raise
	except Exception:
		frappe.log_error(title="Lead photos: upload failed", message=frappe.get_traceback())
		frappe.throw(_("Upload to Google Drive failed."))
	return {**created, "mimeType": created.get("mimeType") or mime}


# ---------------------------------------------------------------------------
# Whitelisted API
# ---------------------------------------------------------------------------
@frappe.whitelist()
def get_lead_photos(lead: str):
	"""Gallery payload. Never creates anything — opening a lead shouldn't litter
	Drive with empty folders for properties nobody photographed."""
	_check(lead)
	try:
		token = _token()
		folder = _resolve_folder(token, lead, create=False)
	except Exception:
		frappe.log_error(title="Lead photos: folder lookup failed", message=frappe.get_traceback())
		return {"folder": None, "files": [], "error": _("Couldn't reach Google Drive.")}

	if not folder:
		return {"folder": None, "files": []}

	try:
		files = _list_files(token, folder["id"])
	except Exception:
		frappe.log_error(title="Lead photos: listing failed", message=frappe.get_traceback())
		files = []

	return {
		"folder": {
			"id": folder["id"],
			"name": folder.get("name"),
			"url": folder.get("webViewLink") or _folder_url(folder["id"]),
		},
		"files": files,
	}


@frappe.whitelist()
def ensure_photo_folder(lead: str):
	"""Create (or adopt) the folder without uploading — powers "Get folder link"."""
	_check(lead, "write")
	try:
		token = _token()
		folder = _resolve_folder(token, lead, create=True)
	except frappe.ValidationError:
		raise
	except Exception:
		frappe.log_error(title="Lead photos: folder creation failed", message=frappe.get_traceback())
		frappe.throw(_("Couldn't create the photo folder (Google Drive error)."))

	_publish(lead)
	return {
		"id": folder["id"],
		"name": folder.get("name"),
		"url": folder.get("webViewLink") or _folder_url(folder["id"]),
	}


@frappe.whitelist()
def upload_lead_photo(lead: str):
	"""Upload ONE file (multipart form field `file`) into the lead's folder.

	One file per request on purpose: a 40-photo batch from a phone is hundreds of
	megabytes, and a single giant POST is exactly what trips nginx's body limit
	and gives the user one opaque failure instead of 39 successes and one retry.
	"""
	_check(lead, "write")

	upload = (frappe.request.files or {}).get("file")
	if not upload:
		frappe.throw(_("No file received."))

	mime = upload.mimetype or "application/octet-stream"
	if not mime.startswith(ALLOWED_PREFIXES):
		frappe.throw(_("Only photos and videos can be added here."))

	data = upload.stream.read()
	if not data:
		frappe.throw(_("That file was empty."))

	token = _token()
	folder = _resolve_folder(token, lead, create=True)
	created = _upload_bytes(token, folder["id"], upload.filename or "photo", mime, data)

	_publish(lead)
	return _shape(created)


@frappe.whitelist()
def delete_lead_photo(lead: str, file_id: str):
	"""Trash a single photo (recoverable from Drive's bin for 30 days)."""
	_check(lead, "write")
	token = _token()
	folder = _resolve_folder(token, lead, create=False)
	if not folder:
		frappe.throw(_("No photo folder for this lead."))

	# Only ever touch a file that is actually in THIS lead's folder.
	meta = _drive_get(f"{DRIVE_FILES}/{file_id}", token, fields="id,parents")
	if folder["id"] not in (meta.get("parents") or []):
		frappe.throw(_("That file isn't in this lead's photo folder."), frappe.PermissionError)

	r = requests.patch(
		f"{DRIVE_FILES}/{file_id}",
		params={"supportsAllDrives": "true"},
		headers=_headers(token, json_body=True),
		json={"trashed": True},
		timeout=30,
	)
	r.raise_for_status()
	_publish(lead)
	return {"ok": True}


@frappe.whitelist()
def download_all_photos(lead: str):
	"""Stream every photo in the folder back as one zip.

	Drive has no zip-a-folder API, and its web UI download needs a Google session
	— which the whole point of link-sharing is to avoid. So we zip server-side.
	"""
	_check(lead)
	token = _token()
	folder = _resolve_folder(token, lead, create=False)
	if not folder:
		frappe.throw(_("No photos yet."))

	files = _list_files(token, folder["id"])
	if not files:
		frappe.throw(_("No photos yet."))

	total = sum(f["size"] for f in files)
	if total > MAX_ZIP_BYTES:
		frappe.throw(
			_("These photos are too large to zip ({0} MB). Use the Drive link instead.").format(
				int(total / 1024 / 1024)
			)
		)

	buf = io.BytesIO()
	with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:
		seen = {}
		for f in files:
			r = requests.get(
				f"{DRIVE_FILES}/{f['id']}",
				params={"alt": "media", "supportsAllDrives": "true"},
				headers=_headers(token),
				timeout=300,
			)
			if not r.ok:
				continue
			# Drive allows duplicate names in a folder; a zip does not.
			name = f["name"] or f["id"]
			if name in seen:
				seen[name] += 1
				stem, _sep, ext = name.rpartition(".")
				name = f"{stem} ({seen[name]}).{ext}" if stem else f"{name} ({seen[name]})"
			else:
				seen[name] = 0
			zf.writestr(name, r.content)

	frappe.local.response.filename = f"{_sanitize(folder.get('name') or 'photos')}.zip"
	frappe.local.response.filecontent = buf.getvalue()
	frappe.local.response.type = "download"


# ---------------------------------------------------------------------------
# Save a texted / emailed picture into the folder
# ---------------------------------------------------------------------------
# Hard stop for one fetched attachment — MMS tops out far below this; it only
# guards against a surprise giant video pinning a worker.
MAX_MEDIA_BYTES = 200 * 1024 * 1024

# iPhone photos arrive by email as HEIC, which older Pythons don't map.
mimetypes.add_type("image/heic", ".heic")
mimetypes.add_type("image/heif", ".heif")


def media_source_key(url: str = "", file: str = "") -> str:
	"""Stable id for one texted/emailed picture, stored on the Drive file.

	Mirrored EXACTLY by `sourceKey()` in frontend/src/composables/leadPhotoSaves.js
	— change both together. A text picture is keyed by the last path segment of its
	media URL (the query string is a signature that can rotate); an email picture
	by its File docname. Drive caps key+value at 124 bytes, hence the slice.
	"""
	if file:
		return f"file:{file}"[:100]
	path = (url or "").split("?", 1)[0].rstrip("/")
	return f"quo:{path.rsplit('/', 1)[-1]}"[:100]


def _text_media(lead: str, url: str):
	"""The media entry + message for `url`, only if it was texted on THIS lead.

	We fetch the URL stored on the message, never the one the browser sent, so
	this endpoint can't be pointed at an arbitrary address.
	"""
	want = media_source_key(url=url)
	base = (url or "").split("?", 1)[0]
	rows = frappe.db.sql(
		"""select name, media, message_date, creation from `tabQuo Message`
		where reference_doctype='CRM Lead' and reference_docname=%s and media like %s""",
		(lead, f"%{base}%"),
		as_dict=True,
	)
	for r in rows:
		try:
			media = json.loads(r.media or "[]")
		except Exception:
			continue
		for m in media or []:
			if m.get("url") and media_source_key(url=m["url"]) == want:
				return m, r
	return None, None


def _email_file(lead: str, file: str):
	"""The File doc, only if it hangs off an email on THIS lead."""
	f = frappe.db.get_value(
		"File", file, ["name", "attached_to_doctype", "attached_to_name", "file_name"], as_dict=True
	)
	if not f or f.attached_to_doctype != "Communication":
		return None
	comm = f.attached_to_name
	linked = frappe.db.exists(
		"Communication", {"name": comm, "reference_doctype": "CRM Lead", "reference_name": lead}
	) or frappe.db.exists(
		"Communication Link",
		{"parent": comm, "link_doctype": "CRM Lead", "link_name": lead},
	)
	return frappe.get_doc("File", file) if linked else None


@frappe.whitelist()
def save_media_to_photos(lead: str, url: str = "", file: str = ""):
	"""Copy ONE picture from a text (`url`) or an email (`file`) into the lead's
	Photos folder. Idempotent: a picture already saved comes back `already=True`.

	One picture per request for the same reason as `upload_lead_photo`; the
	activity feed's "Save all N photos" button just loops over this.
	"""
	_check(lead, "write")
	if not (url or file):
		frappe.throw(_("Nothing to save."))

	source = media_source_key(url=url, file=file)

	if file:
		fdoc = _email_file(lead, file)
		if not fdoc:
			frappe.throw(_("That attachment isn't on this lead."), frappe.PermissionError)
		data = fdoc.get_content()
		if isinstance(data, str):
			data = data.encode()
		name = fdoc.file_name or file
		mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
	else:
		media, msg = _text_media(lead, url)
		if not media:
			frappe.throw(_("That picture isn't on this lead."), frappe.PermissionError)
		try:
			r = requests.get(media["url"], timeout=120, stream=True)
			r.raise_for_status()
			buf = io.BytesIO()
			for chunk in r.iter_content(1024 * 1024):
				buf.write(chunk)
				if buf.tell() > MAX_MEDIA_BYTES:
					frappe.throw(_("That file is too large to copy."))
			data = buf.getvalue()
		except frappe.ValidationError:
			raise
		except Exception:
			frappe.log_error(title="Lead photos: text media fetch failed", message=frappe.get_traceback())
			frappe.throw(_("Couldn't download that picture from the text."))
		mime = (media.get("type") or r.headers.get("Content-Type") or "").split(";")[0].strip()
		when = frappe.utils.get_datetime(msg.message_date or msg.creation)
		ext = source.rsplit(".", 1)[-1] if "." in source[-6:] else (mime.split("/")[-1] or "jpg")
		name = f"Texted {when:%Y-%m-%d %H%M} {source[4:].rsplit('.', 1)[0][:8]}.{ext}"

	if not mime.startswith(ALLOWED_PREFIXES):
		frappe.throw(_("Only photos and videos can be saved to Photos."))
	if not data:
		frappe.throw(_("That file was empty."))

	token = _token()
	folder = _resolve_folder(token, lead, create=True)
	for existing in _list_files(token, folder["id"]):
		if existing.get("source") == source:
			return {**existing, "already": True}

	created = _upload_bytes(token, folder["id"], name, mime, data, source=source)
	_publish(lead)
	return _shape(created)
