"""Authenticated CRM relay for PropWarehouse Street View stills.

The browser cannot reach PropWarehouse's bridge address, and it must never see
its Google key. This endpoint therefore accepts ONLY a coordinate pair, asks
PropWarehouse whether imagery exists, follows the relative image path returned
by that trusted service, and streams the JPEG through the logged-in CRM origin.

Do not add a caller-supplied URL/path parameter. That turns this relay into an
SSRF primitive. `/streetview/image?...` is accepted only when it came from the
metadata response and still receives a strict prefix + parsed-path check before
being followed.
"""
from __future__ import annotations

import math
from urllib.parse import urlsplit

import frappe

META_TIMEOUT = (3.05, 12)
IMAGE_TIMEOUT = (3.05, 30)
MAX_JPEG_BYTES = 2 * 1024 * 1024
IMAGE_PATH_PREFIX = "/streetview/image?"


def _response_set(name, value):
	response = frappe.local.response
	try:
		response[name] = value
	except TypeError:
		setattr(response, name, value)


def _no_image():
	"""A clean image miss: status 404 and no upstream details in the body."""
	_response_set("http_status_code", 404)
	return None


def _point(lat, lng):
	try:
		lat, lng = float(lat), float(lng)
	except (TypeError, ValueError):
		return None
	if not math.isfinite(lat) or not math.isfinite(lng):
		return None
	if not -90 <= lat <= 90 or not -180 <= lng <= 180:
		return None
	return lat, lng


def _safe_image_path(path) -> str | None:
	"""Only PropWarehouse's one image route; never a host, fragment, or odd path."""
	if not isinstance(path, str) or not path.startswith(IMAGE_PATH_PREFIX):
		return None
	parsed = urlsplit(path)
	if parsed.scheme or parsed.netloc or parsed.fragment:
		return None
	if parsed.path != "/streetview/image" or not parsed.query:
		return None
	return path


def _jpeg_bytes(response) -> bytes | None:
	if getattr(response, "status_code", 500) != 200:
		return None
	ctype = str((getattr(response, "headers", {}) or {}).get("Content-Type") or "")
	if ctype.split(";", 1)[0].strip().lower() != "image/jpeg":
		return None
	length = str((getattr(response, "headers", {}) or {}).get("Content-Length") or "").strip()
	if length:
		try:
			if int(length) > MAX_JPEG_BYTES:
				return None
		except ValueError:
			return None

	chunks, size = [], 0
	try:
		for chunk in response.iter_content(chunk_size=64 * 1024):
			if not chunk:
				continue
			size += len(chunk)
			if size > MAX_JPEG_BYTES:
				return None
			chunks.append(chunk)
	except Exception:
		return None
	body = b"".join(chunks)
	# A 200 JPEG header with no bytes is still not an image.
	return body or None


@frappe.whitelist(methods=["GET"])
def comp_streetview(lat=None, lng=None):
	"""Stream one Street View JPEG for an explicitly opened comp, or return 404.

	The frontend invokes this only after Redfin, Realtor and Zillow all returned
	no listing image. This method deliberately knows nothing about gallery order;
	its security contract is narrower: authenticated sales user, coordinates in,
	JPEG bytes out, and no upstream location or secret in an error response.
	"""
	from crm.api.comps import _guard
	from crm.api.vendor_facts import _base_url

	_guard()
	point = _point(lat, lng)
	base = (_base_url() or "").rstrip("/")
	if not point or not base:
		return _no_image()

	import requests

	try:
		meta_response = requests.get(
			f"{base}/streetview", params={"lat": point[0], "lng": point[1]},
			timeout=META_TIMEOUT,
		)
		if meta_response.status_code != 200:
			return _no_image()
		meta = meta_response.json()
		if not isinstance(meta, dict) or not meta.get("available"):
			return _no_image()
		image_path = _safe_image_path(meta.get("image_path"))
		if not image_path:
			return _no_image()
		image_response = requests.get(
			f"{base}{image_path}", timeout=IMAGE_TIMEOUT, stream=True,
		)
		body = _jpeg_bytes(image_response)
		if body is None:
			return _no_image()
	except Exception:
		# Never surface requests' exception text: it can contain the full internal
		# URL, and an upstream URL may itself carry credentials or a provider key.
		return _no_image()

	_response_set("filename", "street-view.jpg")
	_response_set("filecontent", body)
	_response_set("type", "download")
	_response_set("display_content_as", "inline")
	_response_set("content_type", "image/jpeg")
	# The method URL carries the user's authenticated CRM session, so shared/public
	# caches must not store it. PropWarehouse permanently caches the provider bytes;
	# this merely avoids a repeat relay inside one browser.
	_response_set("headers", {"Cache-Control": "private, max-age=86400"})
	return None
