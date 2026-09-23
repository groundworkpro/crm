<template>
  <div
    class="group/card relative cursor-pointer border-b border-outline-gray-1 transition-colors last:border-b-0"
    :class="[
      discarded
        ? 'bg-surface-gray-1'
        : active
          ? 'bg-surface-gray-2'
          : selected
            ? 'bg-surface-blue-1'
            : 'hover:bg-surface-gray-1',
    ]"
    @mouseenter="prefetchPhotos(); $emit('hover', comp.name)"
    @mouseleave="$emit('hover', null)"
    @click="$emit('open', comp.name)"
  >
    <!-- A discarded comp is dimmed and desaturated rather than removed: the rep
         has to be able to see what they threw out to know whether to undo it.
         Kept non-interactive so a stray click can't re-open a rejected house. -->
    <div :class="discarded ? 'pointer-events-none opacity-45 grayscale' : ''">
      <div
        ref="photoEl"
        class="group/photo relative aspect-[3/2] w-full overflow-hidden bg-surface-gray-2"
        @mouseenter="prefetchPhotos"
      >
        <!-- GOTCHA — `loading="lazy"` DOES NOT WORK inside this tray, and it is
             not subtly broken, it simply never fires. Measured on the comps page:
             ten cards rendered, ten <img> in the DOM with valid srcs, and ZERO
             loaded (`complete:false`, `naturalWidth:0`) including the first card,
             which was fully on screen at y=572 of an 863px viewport. Nudging the
             tray's scroll changed nothing; flipping one image to `eager` loaded
             it instantly, so the URLs were always fine.

             That is what "photos don't load until I hover" was: hovering calls
             `prefetchPhotos`, which replaces the src, and a src change is what
             finally makes Chrome evaluate the image. The tray is a nested scroll
             container that the document itself never scrolls, and Chrome's lazy
             heuristic does not re-run for it.

             So the laziness is ours now, via an IntersectionObserver rooted on
             the scroller. Still lazy on purpose -- a 200-comp board must not fetch
             200 thumbnails on open -- but lazy in a way that actually resolves. -->
        <img
          v-if="photo && inView"
          :src="photo"
          :alt="comp.address || ''"
          decoding="async"
          referrerpolicy="no-referrer"
          class="size-full object-cover"
          @error="broken = true"
          v-show="!broken"
        />
        <!-- No photo is the normal case for a pooled-index comp that Zillow's
             area search never matched. Say so quietly instead of showing a
             broken frame. -->
        <!-- A card that has not scrolled into view yet is blank, not "No photo" -
             claiming we have no picture for a house nobody has looked at would be
             a lie the width of the tray. -->
        <div
          v-if="!inView"
          class="size-full"
        />
        <div
          v-else-if="!photo || broken"
          class="flex size-full flex-col items-center justify-center gap-1 text-ink-gray-4"
        >
          <FeatherIcon name="home" class="size-6" />
          <span class="text-2xs">{{ __('No photo') }}</span>
        </div>

        <!-- Status is the first thing that decides whether a row is evidence of a
             sale or of an ask, so it sits on the image rather than below it.
             Pending is called out by name, not by colour alone. -->
        <span
          class="absolute left-2 top-2 rounded px-1.5 py-0.5 text-2xs font-semibold shadow-sm"
          :style="{ background: palette.bg, color: palette.ink }"
        >
          {{ stateLabel }}
        </span>
        <span
          v-if="selected"
          class="absolute right-2 top-2 rounded bg-surface-blue-3 px-1.5 py-0.5 text-2xs font-semibold text-white shadow-sm"
        >
          {{ __('Using') }}
        </span>

        <!-- Hover either edge of the photo to page through the rest. Arrows
             stay hidden until the pointer is actually on that half, so a dense
             tray does not grow a forest of chevrons. Clicking an arrow must
             not open the detail modal — that's the rest of the card. -->
        <button
          v-if="photoIndex > 0"
          class="absolute bottom-0 left-0 top-9 z-20 flex w-10 items-center justify-center bg-gradient-to-r from-black/40 to-transparent text-white opacity-0 transition group-hover/photo:opacity-100"
          :aria-label="__('Previous photo')"
          @click.stop.prevent="stepPhoto(-1)"
        >
          <FeatherIcon name="chevron-left" class="size-5 drop-shadow" />
        </button>
        <button
          v-if="photoIndex < photos.length - 1"
          class="absolute bottom-0 right-0 top-9 z-20 flex w-10 items-center justify-center bg-gradient-to-l from-black/40 to-transparent text-white opacity-0 transition group-hover/photo:opacity-100"
          :aria-label="__('Next photo')"
          @click.stop.prevent="stepPhoto(1)"
        >
          <FeatherIcon name="chevron-right" class="size-5 drop-shadow" />
        </button>
        <span
          v-if="photos.length > 1"
          class="pointer-events-none absolute bottom-1.5 left-1.5 z-20 rounded bg-black/55 px-1.5 py-0.5 text-2xs text-white"
        >
          {{ photoIndex + 1 }}/{{ photos.length }}
        </span>
      </div>

      <div class="px-3 py-2">
        <div class="flex items-baseline justify-between gap-2">
          <span class="flex items-center gap-1 text-base font-semibold text-ink-gray-9">
            <span
              v-if="typeGlyph"
              class="inline-flex size-3.5 shrink-0 text-ink-gray-7"
              :title="typeLabel"
              aria-hidden="true"
              v-html="typeGlyph"
            />
            {{ price }}
          </span>
          <span class="shrink-0 text-xs tabular-nums text-ink-gray-5">
            {{ comp.distance_mi }} mi
          </span>
        </div>
        <div v-if="comp.sale_source === 'Auction.com'" class="mt-0.5 text-xs text-ink-gray-5">
          ADC reported sale · {{ comp.sale_date || __('date unknown') }}
        </div>
        <div class="mt-0.5 text-xs text-ink-gray-7">{{ facts }}</div>

        <!-- How this comp differs from the subject, which is the actual question
             being asked of it. Without this the rep reads "1,744 sqft", then has
             to remember the subject was 1,749 and do the subtraction — for every
             card. Only non-zero differences are shown: a comp that matches on
             beds says nothing by saying "+0 bd". -->
        <div v-if="deltas.length" class="mt-1 flex flex-wrap gap-x-1.5 gap-y-0.5">
          <span
            v-for="d in deltas"
            :key="d.key"
            class="rounded bg-surface-gray-2 px-1 text-2xs font-medium tabular-nums text-ink-gray-6"
            :title="d.title"
          >{{ d.text }}</span>
        </div>

        <!-- Condition tag on a PICKED comp: what state the house was in, which
             the numbers cannot say ("square footage says nothing about a gutted
             shell beside a renovated flip"). A native select, not frappe-ui's —
             reka-ui silently drops an empty-string item value, and '' here IS
             the honest "untagged" state. Optional on purpose: picking stays one
             click and this row only appears after it. -->
        <div v-if="selected && (canTag || comp.comp_type)" class="mt-1" @click.stop>
          <select
            v-if="canTag"
            class="h-5 cursor-pointer rounded border-0 bg-surface-gray-2 py-0 pl-1 pr-5 text-2xs font-medium focus:ring-1 focus:ring-outline-gray-3"
            :class="comp.comp_type ? 'text-ink-gray-8' : 'text-ink-gray-5'"
            :value="comp.comp_type || ''"
            :title="__('Condition of this comp')"
            @change="$emit('setType', comp.name, $event.target.value)"
          >
            <option value="">{{ __('Condition…') }}</option>
            <option v-for="t in COMP_CONDITION_TYPES" :key="t" :value="t">{{ t }}</option>
          </select>
          <span
            v-else
            class="rounded bg-surface-gray-2 px-1 py-0.5 text-2xs font-medium text-ink-gray-7"
          >{{ comp.comp_type }}</span>
        </div>

        <div class="mt-0.5 truncate text-xs font-medium text-ink-gray-8" :title="comp.address">
          {{ street }}
        </div>
        <div
          class="mt-1 text-2xs"
          :class="
            isPendingComp
              ? 'font-medium text-ink-violet-1'
              : state === 'auction'
                ? 'font-medium'
                : isActive
                  ? 'text-ink-red-3'
                  : 'text-ink-gray-5'
          "
          :style="state === 'auction' ? { color: palette.onLight } : undefined"
        >
          {{ timing }}
        </div>
        <!-- Same warning the map pin's dashed outline carries, in words the card
             can afford. Amber, not red: a flip is "look closer", not an error,
             and plenty are perfectly good comps once you have seen the photos. -->
        <div
          v-if="flipBadge"
          class="mt-1 flex items-baseline gap-1 rounded bg-surface-amber-1 px-1.5 py-1 text-2xs text-ink-amber-3"
          :title="`${flipBadge.label} — ${flipBadge.detail}`"
        >
          <span class="shrink-0 font-semibold">{{ flipBadge.label }}</span>
          <span class="truncate">{{ flipBadge.detail }}</span>
        </div>
      </div>
    </div>

    <!-- Undo sits ON the discarded card, not in a separate list somewhere else:
         the thing you want to undo is the thing you are looking at. -->
    <div v-if="discarded" class="absolute inset-0 flex items-center justify-center">
      <Button
        :label="__('Undo discard')"
        iconLeft="rotate-ccw"
        @click.stop="$emit('undiscard', comp.name)"
      />
    </div>

    <!-- ALWAYS visible, not hover-revealed. Use and discard are the two
         judgements this tray exists to capture, and the same view opens on a
         phone from MobileLead — where there is no hover at all, so a
         hover-only control is simply unreachable. They sit at low contrast
         over the photo and strengthen on hover, which keeps a dense tray calm
         without hiding its primary actions. -->
    <div v-else class="absolute right-2 top-2 z-30 flex gap-1">
      <button
        class="rounded bg-surface-white/80 px-1.5 py-1 shadow-sm ring-1 ring-outline-gray-2 transition hover:bg-surface-white"
        :class="selected ? 'text-ink-blue-3' : 'text-ink-gray-6 hover:text-ink-gray-9'"
        :title="__('Use as comp') + ' (U)'"
        @click.stop="$emit('use', comp.name)"
      >
        <!-- plus / check, not an unchecked-checkbox square: beside the discard
             ✕ an empty square reads as a second empty box rather than as an
             action. "+" says add it, "✓" says it is in. -->
        <FeatherIcon :name="selected ? 'check' : 'plus'" class="size-3.5" />
      </button>
      <button
        class="rounded bg-surface-white/80 px-1.5 py-1 text-ink-gray-6 shadow-sm ring-1 ring-outline-gray-2 transition hover:bg-surface-white hover:text-ink-red-3"
        :title="__('Discard this comp') + ' (H)'"
        @click.stop="$emit('discard', comp.name)"
      >
        <FeatherIcon name="x" class="size-3.5" />
      </button>
      <button
        v-if="comp.lat != null && comp.lng != null"
        class="rounded bg-surface-white/80 px-1.5 py-1 text-ink-gray-6 shadow-sm ring-1 ring-outline-gray-2 transition hover:bg-surface-white hover:text-ink-gray-9"
        :title="__('Street View')"
        @click.stop="$emit('street', comp.name)"
      >
        <FeatherIcon name="navigation" class="size-3.5" />
      </button>
    </div>
  </div>
</template>

<script setup>
/**
 * One property in the comps tray: photo, price, facts, and the two judgements a
 * rep makes about it (use it / discard it).
 *
 * Split out of CompsView because that file is already long, and because the card
 * is the unit the tray repeats — a change to how a comp reads should be one edit,
 * not a hunt through a 2,000-line template.
 */
import { Button, FeatherIcon } from 'frappe-ui'
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import {
  COMP_CONDITION_TYPES,
  PROPERTY_TYPE_KINDS,
  compColor,
  compState,
  compStateLabel,
  daysToSell,
  finiteDays,
  formatLotSize,
  isActiveStatus,
  loadCompPhotos,
  propertyTypeGlyphSvg,
  propertyTypeKind,
  streetAddress,
} from '@/utils/comps'

const props = defineProps({
  comp: { type: Object, required: true },
  lead: { type: String, default: '' },
  active: { type: Boolean, default: false },
  discarded: { type: Boolean, default: false },
  // The resolved subject facts from `get_lead_comps`, for the +/- comparison.
  subject: { type: Object, default: null },
  // Condition tagging is a team-wide write on the lead, so hosts turn it off
  // in practice mode / on pre-provision sites; a stored tag still renders.
  canTag: { type: Boolean, default: false },
})
defineEmits(['hover', 'open', 'use', 'discard', 'undiscard', 'street', 'setType'])

// Shared with the map pills and the legend, so a card and its pin can never
// disagree about what "sold" looks like. Passed the whole comp, not just the
// status string, because pending is not visible in `status` alone.
const palette = computed(() => compColor(props.comp))
const state = computed(() => compState(props.comp))
const stateLabel = computed(() => compStateLabel(state.value))
const isPendingComp = computed(() => state.value === 'pending')
const typeKind = computed(() => propertyTypeKind(props.comp.property_type))
const typeLabel = computed(() => (typeKind.value ? PROPERTY_TYPE_KINDS[typeKind.value].label : ''))
const typeGlyph = computed(() =>
  typeKind.value ? propertyTypeGlyphSvg(typeKind.value, 14) : '',
)

// --- our own lazy loading ------------------------------------------------
// See the GOTCHA in the template: the platform's `loading="lazy"` never fires
// inside this tray. 400px of margin means a card is fetched just before it is
// scrolled to, so the picture is there rather than arriving under the eye.
const photoEl = ref(null)
const inView = ref(false)
let io = null

onMounted(() => {
  if (typeof IntersectionObserver === 'undefined') {
    inView.value = true
    return
  }
  io = new IntersectionObserver(
    (entries) => {
      if (!entries.some((e) => e.isIntersecting)) return
      // One-way: once a photo has loaded, scrolling past must not throw it away
      // and re-download it on the way back.
      inView.value = true
      io?.disconnect()
      io = null
    },
    // Rooted on the TRAY, not the viewport. `rootMargin` only expands the root
    // box -- it cannot see past an intermediate clip -- so a viewport-rooted
    // observer inside this scroller starts a fetch exactly as the card appears
    // and the 400px of lead time is silently lost (measured: one new photo per
    // scroll step instead of the three the margin should buy). Rooted here, the
    // margin does what it says and the photo is ready before the card arrives.
    { root: photoEl.value?.closest('[data-comp-tray]') || null, rootMargin: '400px 0px' },
  )
  if (photoEl.value) io.observe(photoEl.value)
})

onBeforeUnmount(() => {
  io?.disconnect()
  io = null
})

const broken = ref(false)
const photos = ref([])
const photoIndex = ref(0)
const photo = computed(() => {
  if (photos.value.length) return photos.value[photoIndex.value] || ''
  return props.comp.photo || ''
})
const street = computed(() => streetAddress(props.comp.address) || props.comp.address || '')

function prefetchPhotos() {
  if (photos.value.length || !props.lead) return
  loadCompPhotos(
    props.lead,
    props.comp.name,
    props.comp.address || '',
    props.comp.lat ?? null,
    props.comp.lng ?? null,
    props.comp.city || '',
    props.comp.state || '',
    props.comp.zip || '',
  ).then((urls) => {
    if (!urls.length) return
    photos.value = urls
    const cover = props.comp.photo
    const i = cover ? urls.indexOf(cover) : -1
    photoIndex.value = i >= 0 ? i : 0
  })
}
function stepPhoto(dir) {
  const next = photoIndex.value + dir
  if (next < 0 || next >= photos.value.length) return
  photoIndex.value = next
  broken.value = false
}
const selected = computed(() => !!props.comp.selected)
const isActive = computed(() => isActiveStatus(props.comp.status))

const price = computed(() => {
  const p = Number(props.comp.price)
  if (!p) return '—'
  const s = '$' + Math.round(p).toLocaleString()
  return state.value === 'for_rent' ? s + '/mo' : s
})

const facts = computed(() => {
  const c = props.comp
  const bits = []
  if (c.property_type) bits.push(c.property_type)
  if (c.bedrooms) bits.push(__('{0} bd', [c.bedrooms]))
  if (c.bathrooms) bits.push(__('{0} ba', [c.bathrooms]))
  if (c.square_footage) bits.push(Number(c.square_footage).toLocaleString() + ' ' + __('sqft'))
  const lot = formatLotSize(c.lot_size, { compact: true })
  if (lot) bits.push(lot)
  if (c.year_built) bits.push(__('built {0}', [c.year_built]))
  return bits.join(' · ') || __('No details')
})

/**
 * The comp minus the subject, per fact.
 *
 * Uses the subject's EXACT numbers only (`beds_exact` etc.). The lead's own
 * pick-list fields are bands -- "1000 - 2000" sqft, "3 Bedroom" -- and
 * subtracting a band midpoint would invent precision the source never had, then
 * print it as a hard "+244 sqft". When Zillow gave us a real number the delta is
 * real; otherwise there is simply no chip, which is honest.
 */
const DELTA_FIELDS = [
  { key: 'bedrooms', subj: 'beds', unit: 'bd', dp: 0 },
  { key: 'bathrooms', subj: 'baths', unit: 'ba', dp: 1 },
  { key: 'square_footage', subj: 'sqft', unit: 'sqft', dp: 0 },
  { key: 'year_built', subj: 'year_built', unit: 'yr', dp: 0 },
]

const deltas = computed(() => {
  const s = props.subject
  if (!s) return []
  const out = []
  for (const f of DELTA_FIELDS) {
    // `*_exact` marks a number that came from a real source rather than a band.
    if (f.subj !== 'year_built' && s[`${f.subj}_exact`] === false) continue
    const a = Number(props.comp[f.key])
    const b = Number(s[f.subj])
    if (!Number.isFinite(a) || !Number.isFinite(b) || !a || !b) continue
    const diff = a - b
    const r = f.dp ? Math.round(diff * 10) / 10 : Math.round(diff)
    if (!r) continue
    const n = f.unit === 'sqft' ? Math.abs(r).toLocaleString() : Math.abs(r)
    out.push({
      key: f.key,
      text: `${r > 0 ? '+' : '−'}${n} ${f.unit}`,
      title: __('{0} vs this property', [`${r > 0 ? '+' : '−'}${n} ${f.unit}`]),
    })
  }
  return out
})

/**
 * "99 days" means opposite things on the two kinds of pin, so they are never
 * rendered the same way: days ON the market for a live listing (it is not
 * selling) versus days SINCE it left for an off-market one (how current the
 * evidence is). Same rule as the map popup.
 */
const timing = computed(() => {
  const c = props.comp
  if (state.value === 'unknown') return __('Current status unknown — ADC sale evidence only')
  // A pending house is not "listed" any more and has not sold either. What
  // matters about it is that the price is agreed, so that is what it says.
  if (isPendingComp.value) {
    const dom = daysOnMarket(c)
    return dom
      ? __('Under contract · listed {0} days', [dom])
      : __('Under contract · price agreed')
  }
  if (state.value === 'auction') {
    const dom = daysOnMarket(c)
    return dom ? __('Auction · listed {0} days', [dom]) : __('Auction')
  }
  if (isActive.value) {
    const dom = daysOnMarket(c)
    return dom ? __('Listed · {0} days on market', [dom]) : __('Listed')
  }
  const d = fmtDate(c.removed_date)
  const ago = agoLabel(c.recency_days)
  // Both numbers, and never the same word for them. How long AGO is what makes a
  // comp good evidence; how long it TOOK is what the market thought of the price.
  // Time-to-sell comes from the listing chain, not from `days_on_market`, which
  // on a sold Zillow row is days since the sale rather than days on market.
  const took = soldInDays(c)
  const base = d
    ? ago
      ? `${__('Off-market')} ${d} · ${ago}`
      : `${__('Off-market')} ${d}`
    : __('Off-market')
  return took ? `${base} · ${__('took {0}d to sell', [took])}` : base
})

/** Days from the first listing of the run that ended in the sale. null if unknown. */
function soldInDays(c) {
  const n = daysToSell(c)
  return n == null ? null : Math.round(n)
}

/**
 * The flip warning for the tray, as a badge rather than a sentence.
 *
 * The map pin uses a dashed outline (the type glyph took the interior slot the
 * star used to occupy). The card can afford the words the pill cannot.
 */
const flipBadge = computed(() => {
  const f = props.comp?.sale_history?.flip
  if (!f) return null
  const pct = Math.round((f.pct || 0) * 100)
  return {
    label: f.kind === 'relist' ? __('Possible flip in progress') : __('Possible flip'),
    detail:
      f.kind === 'relist'
        ? __('bought {0} ago · asking {1}% more', [agoLabel(f.hold_days), pct])
        : __('held {0} · resold {1}% higher', [agoLabel(f.hold_days), pct]),
  }
})

/**
 * Days on market, or null when nobody knows.
 *
 * Zillow reports `daysOnZillow: -1` for unknown. The server drops that now, but
 * a circle cached before it did still holds negatives for up to a week — and
 * "listed -1 days" is the kind of thing a rep screenshots.
 */
function daysOnMarket(c) {
  const n =
    finiteDays(c?.days_on_market) ?? finiteDays(c?.sale_history?.days_on_market)
  return n && n > 0 ? Math.round(n) : null
}

/**
 * GOTCHA — `Date.parse('YYYY-MM-DD')` is UTC midnight, so `toLocaleDateString`
 * renders the PREVIOUS day everywhere west of Greenwich. A date-only value is a
 * calendar date with no timezone; build it as local.
 */
function fmtDate(v) {
  if (!v) return ''
  const m = String(v).match(/^(\d{4})-(\d{2})-(\d{2})/)
  const d = m ? new Date(+m[1], +m[2] - 1, +m[3]) : new Date(v)
  if (isNaN(d)) return ''
  return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })
}

function agoLabel(days) {
  if (days == null) return ''
  const n = Number(days)
  if (!Number.isFinite(n)) return ''
  if (n < 45) return __('{0}d ago', [Math.round(n)])
  if (n < 365) return __('{0} mo ago', [Math.round(n / 30)])
  return __('{0}y ago', [(n / 365).toFixed(1)])
}
</script>
