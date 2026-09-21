/**
 * Google Maps Embed Street View. Same key as the lead-desk mockup
 * (project claude-code-486305, name "CRM Street View (Maps Embed)").
 *
 * Maps Embed keys are meant to live in the page — this one is restricted to
 * the Embed API and to crm.groundworkpro.com + localhost:8080 / :8477.
 *
 * `location=` is lat,lng only. An address string returns Invalid 'location'.
 * Use referrerpolicy="origin" on the iframe so record ids never go to Google.
 */
const MAPS_EMBED_KEY = 'AIzaSyBAIpb09ornAPq4nWMhCQqsuk46AjrErFo'

export function bearingDeg(lat1, lng1, lat2, lng2) {
  const toRad = (d) => (Number(d) * Math.PI) / 180
  const φ1 = toRad(lat1)
  const φ2 = toRad(lat2)
  const Δλ = toRad(Number(lng2) - Number(lng1))
  const y = Math.sin(Δλ) * Math.cos(φ2)
  const x =
    Math.cos(φ1) * Math.sin(φ2) - Math.sin(φ1) * Math.cos(φ2) * Math.cos(Δλ)
  return ((Math.atan2(y, x) * 180) / Math.PI + 360) % 360
}

export function streetViewEmbedUrl(lat, lng, heading = 0, panoId = '') {
  if (lat == null || lng == null || Number.isNaN(Number(lat)) || Number.isNaN(Number(lng))) {
    return ''
  }
  const h = Number.isFinite(Number(heading)) ? Number(heading) : 0
  const base = `https://www.google.com/maps/embed/v1/streetview?key=${MAPS_EMBED_KEY}`
  // A pano ID is the exact camera location Google chose for this address. Use
  // it when we have it and look FROM the camera TO the house with `heading`.
  // Without it, `location=` asks Google to pick the nearest pano, which is the
  // old source of "wrong house / down the block".
  if (panoId) {
    return `${base}&pano=${encodeURIComponent(panoId)}&heading=${h}&pitch=10&fov=80`
  }
  return `${base}&location=${Number(lat)},${Number(lng)}&heading=${h}&pitch=10&fov=80`
}
