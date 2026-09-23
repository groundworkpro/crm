import { call } from 'frappe-ui'

/**
 * Comp map palette: for sale RED, confirmed sold YELLOW, off-market SLATE,
 * the subject BLUE. A removed listing is not evidence of a sale.
 *
 * Defined ONCE here because three surfaces have to agree and none of them can
 * read the others' styles: the Leaflet pills are hand-built HTML strings, the
 * tray cards are Tailwind components, and the legend is a third thing. A pill
 * and its card disagreeing about what "sold" looks like is worse than either
 * colour being slightly off.
 *
 * This replaced a blue/amber pair chosen to be dichromat-safe. Red vs yellow is
 * a weaker hue signal for protan/deutan vision, so the two are kept far apart in
 * LIGHTNESS as well (a dark red against a light yellow), which survives losing
 * the hue entirely. Status is also written in words in the pin popup and on
 * every tray card, so colour is never the only carrier.
 *
 * `ink` is the text colour that belongs on `bg`: white on the red, near-black on
 * the yellow. Yellow with white text is the obvious way to make this unreadable.
 * `onLight` is the opposite case — the status written as TEXT on a white popup,
 * where the yellow fill itself would be invisible, so it darkens to a gold.
 */
export const COMP_COLORS = {
  active: { bg: '#d92d20', ink: '#ffffff', border: '#9f1d14', onLight: '#b42318' },
  sold: { bg: '#f5c518', ink: '#3a2f00', border: '#c99a06', onLight: '#8a6a00' },
  off_market: { bg: '#cbd5e1', ink: '#0f172a', border: '#64748b', onLight: '#475569' },
  subject: { bg: '#2563c9', ink: '#ffffff', border: '#1c4ea1', onLight: '#2563c9' },
  // Pending / under contract. Violet because that is the convention on MLS maps,
  // and because it is far from both the red and the yellow in hue.
  //
  // It is NOT far from them in lightness, which the note above says is what
  // survives losing hue — so this one leans harder on the other carrier: every
  // surface that can show a pending comp also writes the word "Pending" on it
  // (the pill, the tray badge, the pin popup and the detail header), rather than
  // leaving colour to say it alone.
  pending: { bg: '#7c3aed', ink: '#ffffff', border: '#5b21b6', onLight: '#6d28d9' },
  // For-rent listings. Teal, far from sale red / sold yellow / pending violet
  // in both hue and lightness. Live listings, so they never recency-fade.
  rent: { bg: '#0f766e', ink: '#ffffff', border: '#115e59', onLight: '#0f766e' },
  // For-sale auctions (Zillow listingSubType.is_forAuction). Orange, per Lance.
  // Live, so they never recency-fade. The word "Auction" is on every surface —
  // orange vs red is close in lightness, same rule as pending vs sold.
  auction: { bg: '#ea580c', ink: '#ffffff', border: '#c2410c', onLight: '#c2410c' },
}

/** True when a comp is still listed (an ASK), rather than off-market (a sale). */
export function isActiveStatus(status) {
  return String(status || '')
    .toLowerCase()
    .startsWith('activ')
}

/**
 * The states a comp can be in, resolved once so no surface has to re-derive
 * it: `for_sale`, `pending`, `sold`, `off_market`, `for_rent`, `auction`.
 *
 * The server sends `listing_state` on every row now. The fallback exists because
 * the frontend and backend deploy separately — for the length of a deploy window
 * the browser is talking to the other version, the same reason `hoodPoints`
 * accepts two shapes.
 *
 * The sold/off-market distinction is the app's oldest rule about this data: the
 * pooled index carries a last ASK, so a listing disappearing is not a confirmed
 * close, and only a Zillow-recorded transaction earns the word "sold".
 */
export function compState(comp) {
  const declared = String(comp?.listing_state || '')
  if (declared) return declared
  if (String(comp?.name || '').startsWith('zillow-rent::')) return 'for_rent'
  if (/^auction:\d+$/.test(String(comp?.source_lead || ''))) return 'unknown'
  if (isActiveStatus(comp?.status)) return 'for_sale'
  return comp?.source === 'zillow' || comp?.zillow_refreshed ? 'sold' : 'off_market'
}

export function isRentalComp(comp) {
  return (
    compState(comp) === 'for_rent' ||
    String(comp?.name || '').startsWith('zillow-rent::')
  )
}

/** True when a comp is spoken for but not closed — an agreed price, still live. */
export function isPending(comp) {
  return compState(comp) === 'pending'
}

export function isAuction(comp) {
  return compState(comp) === 'auction'
}

/** What to call a state on a badge. Short: these render inside 24px pills. */
export function compStateLabel(state) {
  if (state === 'unknown') return __('Current status unknown')
  if (state === 'pending') return __('Pending')
  if (state === 'auction') return __('Auction')
  if (state === 'for_sale') return __('For sale')
  if (state === 'for_rent') return __('For rent')
  if (state === 'sold') return __('Sold')
  return __('Off-market')
}

/** The palette entry for a comp, pending included. */
export function compColor(statusOrComp) {
  // Accepts either a whole comp (which can be pending) or a bare status string,
  // because the older callers pass the string and both have to keep working.
  const comp =
    statusOrComp && typeof statusOrComp === 'object' ? statusOrComp : { status: statusOrComp }
  const state = compState(comp)
  if (state === 'pending') return COMP_COLORS.pending
  if (state === 'for_rent') return COMP_COLORS.rent
  if (state === 'auction') return COMP_COLORS.auction
  if (state === 'for_sale') return COMP_COLORS.active
  return state === 'sold' ? COMP_COLORS.sold : COMP_COLORS.off_market
}

/**
 * Property-type marks on the map pills.
 *
 * Colour is spoken for by status and the fill fades with recency, so type is an
 * ink glyph — the same channel the flip star used to occupy. Single family is
 * unmarked on purpose: it is most of the board, and a mark only earns its keep
 * when it means "this is not a house". Apartment shares condo.
 */
export const PROPERTY_TYPE_KINDS = {
  townhouse: { label: 'Townhouse' },
  condo: { label: 'Condo' },
  multi: { label: 'Multi-family' },
  manufactured: { label: 'Manufactured' },
  land: { label: 'Land' },
}

export const PROPERTY_TYPE_KIND_ORDER = [
  'townhouse',
  'condo',
  'multi',
  'manufactured',
  'land',
]

/**
 * Condition tags a rep can put on a PICKED comp ("what state was it in").
 * Must match COMP_CONDITION_TYPES in crm/api/comps.py exactly — the server
 * validates against its copy and rejects anything else.
 */
export const COMP_CONDITION_TYPES = [
  'New Build',
  'Fix and Flip',
  'Move-in Ready',
  'Remodel',
  'Full Gut',
  'Teardown',
]

export function propertyTypeKind(type) {
  const t = String(type || '').toLowerCase()
  if (!t) return null
  if (t.includes('town')) return 'townhouse'
  if (t.includes('condo') || t.includes('apartment') || /\bapt\b/.test(t)) return 'condo'
  if (t.includes('multi')) return 'multi'
  if (t.includes('manufact') || t.includes('mobile') || t.includes('trailer')) {
    return 'manufactured'
  }
  if (/\bland\b/.test(t) || /\blot\b/.test(t) || t.includes('vacant')) return 'land'
  return null
}

const TYPE_GLYPH_INNER = {
  townhouse:
    '<path fill="currentColor" d="M1 10.5V5L3.4 3.2v7.3H1zm3.6 0V4.4L6 2.8l1.4 1.6v6.1H4.6zM8.6 10.5V5L11 3.2v7.3H8.6z"/>',
  condo:
    '<rect x="1.2" y="1.2" width="9.6" height="9.6" rx="1" fill="none" stroke="currentColor" stroke-width="1.5"/><path d="M1.2 6h9.6M6 1.2v9.6" fill="none" stroke="currentColor" stroke-width="1.5"/>',
  multi:
    '<rect x="2.2" y="1.2" width="7.6" height="9.6" fill="none" stroke="currentColor" stroke-width="1.5"/><path d="M2.2 4.4h7.6M2.2 7.6h7.6" fill="none" stroke="currentColor" stroke-width="1.5"/>',
  manufactured:
    '<rect x="1.4" y="1.8" width="9.2" height="6" rx="1.1" fill="currentColor"/><circle cx="4" cy="9.5" r="1.35" fill="currentColor"/><circle cx="8" cy="9.5" r="1.35" fill="currentColor"/>',
  land: '<path fill="currentColor" d="M6 1.1l4.7 4.9-4.7 4.9L1.3 6z"/>',
}

export function propertyTypeGlyphSvg(kind, size = 12) {
  const inner = TYPE_GLYPH_INNER[kind]
  if (!inner) return ''
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 12 12" width="${size}" height="${size}" aria-hidden="true">${inner}</svg>`
}

/** Inline HTML for a Leaflet pill. Empty string when the type is unmarked. */
export function propertyTypeGlyphHtml(type, size = 12) {
  const kind = propertyTypeKind(type)
  if (!kind) return ''
  const label = PROPERTY_TYPE_KINDS[kind].label
  return `<span class="comps-type-glyph" title="${label}" style="display:inline-flex;width:${size}px;height:${size}px;margin-right:4px;flex:none;align-items:center;justify-content:center;overflow:visible">${propertyTypeGlyphSvg(kind, size)}</span>`
}

const DIMENSIONS = [
  {
    key: 'type',
    label: 'type',
    subject: (s) => s?.property_type,
    comp: (c) => c?.property_type,
    matches: (a, b) => String(a).toLowerCase() === String(b).toLowerCase(),
  },
  {
    key: 'beds',
    label: 'beds',
    subject: (s) => number(s?.beds),
    comp: (c) => number(c?.bedrooms),
    matches: (a, b) => Math.abs(a - b) <= 1,
  },
  {
    key: 'baths',
    label: 'baths',
    subject: (s) => number(s?.baths),
    comp: (c) => number(c?.bathrooms),
    matches: (a, b) => Math.abs(a - b) <= 1,
  },
  {
    key: 'sqft',
    label: 'size',
    subject: (s) => number(s?.sqft),
    comp: (c) => number(c?.square_footage),
    matches: (a, b) => Math.abs(a - b) / Math.max(a, 1) <= 0.25,
  },
  {
    key: 'year',
    label: 'age',
    subject: (s) => number(s?.year_built),
    comp: (c) => number(c?.year_built),
    matches: (a, b) => Math.abs(a - b) <= 20,
  },
]

function number(value) {
  const n = Number(value)
  return Number.isFinite(n) && n > 0 ? n : null
}

/**
 * A day-count that is actually a number, or null.
 *
 * GOTCHA — `Number(null) === 0`. Sold comps whose listing chain we don't have
 * carry `days_to_sell: null`, and feeding that to `Number` painted "0d" /
 * "took 0d to sell" on most of the board (measured: 21 of 50 on one lead,
 * 64 of 211 across six). Check nullish BEFORE coercing.
 */
export function finiteDays(value) {
  if (value == null || value === '') return null
  const n = Number(value)
  return Number.isFinite(n) && n >= 0 ? n : null
}

/** Date-only `YYYY-MM-DD` as local midnight, or null. */
export function ymdDate(value) {
  if (!value) return null
  const m = String(value).match(/^(\d{4})-(\d{2})-(\d{2})/)
  if (!m) return null
  const d = new Date(+m[1], +m[2] - 1, +m[3])
  return Number.isFinite(d.getTime()) ? d : null
}

/**
 * How long a sold comp sat on the market.
 *
 * Prefer the listing-chain number (price cuts included). When Zillow's
 * `/property` miss leaves `sale_history` empty — 768 King Sword on lead 01103
 * — still compute from listed_date → removed_date, which the row already has.
 */
export function daysToSell(comp) {
  const fromHistory = finiteDays(comp?.sale_history?.days_to_sell)
  if (fromHistory != null) return fromHistory
  const listed = ymdDate(comp?.listed_date)
  const sold = ymdDate(comp?.removed_date)
  if (listed && sold && sold >= listed) {
    return Math.round((sold - listed) / 86400000)
  }
  return null
}

function present(value) {
  return value !== null && value !== undefined && value !== ''
}

/**
 * A transparent fit count, using the same five tolerances as the server's
 * “similar” preset. “4/5 fit” is more honest and useful than an opaque 80 score.
 */
export function compFit(comp, subject) {
  const dimensions = DIMENSIONS.map((dimension) => {
    const subjectValue = dimension.subject(subject)
    const compValue = dimension.comp(comp)
    if (!present(subjectValue) || !present(compValue)) return null
    return {
      key: dimension.key,
      label: dimension.label,
      matches: dimension.matches(subjectValue, compValue),
    }
  }).filter(Boolean)
  const matched = dimensions.filter((dimension) => dimension.matches).length
  const total = dimensions.length
  const ratio = total ? matched / total : 0
  return {
    matched,
    total,
    ratio,
    dimensions,
    theme: !total ? 'gray' : ratio >= 0.8 ? 'green' : ratio >= 0.6 ? 'blue' : 'orange',
  }
}

export function formatLotSize(value, { compact = false } = {}) {
  // Zillow's string ("4,530 sqft", "0.25 acres") is already the right claim;
  // compact just shortens it so the tray facts line can carry it next to living
  // area without looking like a second square-footage number.
  if (value == null || value === '') return ''
  const raw = String(value).trim()
  if (!raw) return ''
  const lower = raw.toLowerCase().replace(/,/g, '')
  const n = parseFloat(lower)
  if (!Number.isFinite(n) || n <= 0) return raw
  if (/\bacres?\b|\bac\b/.test(lower)) {
    return compact ? `${n} ac` : `${n} ${n === 1 ? 'acre' : 'acres'}`
  }
  const sqft = Math.round(n).toLocaleString()
  return compact ? `${sqft} lot` : `${sqft} sqft`
}

export function compFacts(comp) {
  return [
    number(comp?.bedrooms) ? `${formatDecimal(comp.bedrooms)} bd` : '',
    number(comp?.bathrooms) ? `${formatDecimal(comp.bathrooms)} ba` : '',
    number(comp?.square_footage)
      ? `${Math.round(Number(comp.square_footage)).toLocaleString()} sf`
      : '',
    formatLotSize(comp?.lot_size, { compact: true }),
    number(comp?.year_built) ? String(Math.round(Number(comp.year_built))) : '',
  ].filter(Boolean)
}

export function subjectFacts(subject) {
  return [
    number(subject?.beds) ? `${formatDecimal(subject.beds)} bd` : '',
    number(subject?.baths) ? `${formatDecimal(subject.baths)} ba` : '',
    number(subject?.sqft) ? `${Math.round(Number(subject.sqft)).toLocaleString()} sf` : '',
    formatLotSize(subject?.lot_size, { compact: true }),
    number(subject?.year_built) ? String(Math.round(Number(subject.year_built))) : '',
  ].filter(Boolean)
}

export function compDifferences(comp, subject) {
  const out = []
  const sqft = delta(comp?.square_footage, subject?.sqft)
  if (sqft !== null) out.push(`${signed(Math.round(sqft))} sf`)

  const year = delta(comp?.year_built, subject?.year_built)
  if (year !== null && year !== 0) {
    out.push(`${Math.abs(Math.round(year))} yr ${year > 0 ? 'newer' : 'older'}`)
  }
  if (number(comp?.distance_mi) !== null) {
    out.push(`${Number(comp.distance_mi).toFixed(comp.distance_mi < 1 ? 1 : 1)} mi`)
  }
  return out
}

export function formatCompMoney(value) {
  const n = number(value)
  return n === null ? '—' : '$' + Math.round(n).toLocaleString()
}

/** The street only — "705 Cliff St", not the city/state/ZIP that follows. */
/**
 * A comps SUBJECT is a CRM Lead or a scratch `CRM Property` (comps + calcs on a
 * house that is not a lead). The backend dispatches on the `PROP-` prefix
 * (`crm.api.comps.subject_doctype`); this is the one place the frontend does.
 */
export const SCRATCH_PREFIX = 'PROP-'

export function isScratchSubject(name) {
  return String(name || '').startsWith(SCRATCH_PREFIX)
}

export function subjectDoctype(name) {
  return isScratchSubject(name) ? 'CRM Property' : 'CRM Lead'
}

/** The page that hosts the comps map for this subject. */
export function compsPagePath(name) {
  if (!name) return ''
  return isScratchSubject(name)
    ? `/crm/properties/${name}`
    : `/crm/leads/${name}/comps`
}

/** Query token that opens the SUBJECT gallery, not a named comp. */
export const COMP_QUERY_SUBJECT = 'subject'

/** Path (+ query) that opens one property's photo gallery on the comps page. */
export function compsDetailPath(subjectName, { compName, subject = false } = {}) {
  const path = compsPagePath(subjectName)
  if (!path) return ''
  const q = subject ? COMP_QUERY_SUBJECT : String(compName || '')
  if (!q) return path
  return `${path}?comp=${encodeURIComponent(q)}`
}

/** Absolute URL for copying / right-click. Empty when we cannot name the page. */
export function compsDetailUrl(subjectName, opts) {
  const path = compsDetailPath(subjectName, opts)
  if (!path) return ''
  if (typeof window === 'undefined' || !window.location?.origin) return path
  return `${window.location.origin}${path}`
}

export function streetAddress(address) {
  const raw = String(address || '').trim()
  if (!raw) return ''
  return raw.split(',')[0].trim()
}

/**
 * Photos for one comp, fetched once and remembered for the session.
 * Hits `get_comp_details` (Zillow /property + /photos), which is already cached
 * 30 days server-side — this just stops a hover-then-open from asking twice.
 */
const photoPromises = new Map()
export function loadCompPhotos(lead, name, address = '', lat = null, lng = null, city = '', state = '', zip = '') {
  if (!lead || !name) return Promise.resolve([])
  const key = `${lead}:${name}`
  if (photoPromises.has(key)) return photoPromises.get(key)
  // address rescues pins that are not CRM Comp rows (zillow / redfin / realtor);
  // city/state/zip keep a street-only line from resolving the wrong house;
  // lat/lng feed the Redfin photo rung's neighbourhood sweep
  const p = call('crm.api.comps.get_comp_details', {
    lead, comp: name, address, lat, lng, city, state, zip,
  })
    .then((r) => (Array.isArray(r?.photos) ? r.photos : []))
    .catch(() => [])
  photoPromises.set(key, p)
  return p
}

function delta(a, b) {
  const aa = number(a)
  const bb = number(b)
  return aa === null || bb === null ? null : aa - bb
}

function signed(value) {
  if (!value) return 'same'
  return value > 0 ? `+${value.toLocaleString()}` : value.toLocaleString()
}

function formatDecimal(value) {
  const n = Number(value)
  return Number.isInteger(n) ? String(n) : n.toFixed(1).replace(/\.0$/, '')
}
