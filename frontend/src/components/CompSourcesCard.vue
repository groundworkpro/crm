<template>
  <!--
    Where this board's comps came from, one line per source.

    Exists because the board used to be decided in ONE request that gave Redfin
    a second: on a cold area Redfin's store read takes 5-8s, so it silently
    dropped out, and in a non-disclosure state the thin board then bought
    BatchData while Redfin was about to deliver priced sales (Myesha Moore's
    lead, 2026-09-24). The server now reports each source's state and the page
    re-checks while one is still on its way; this card is what the rep sees.

    Open by default while anything is loading, collapsed once all are in; the
    rep's own open/closed choice wins and is remembered.
  -->
  <div
    v-if="rows.length"
    class="rounded-xl border border-outline-gray-2 bg-surface-white text-ink-gray-8 shadow-lg"
    :class="overlay ? [open ? 'w-[18rem]' : 'w-max', 'max-w-[calc(100%-1rem)]'] : 'w-full'"
    data-testid="comp-sources"
  >
    <button
      v-if="!open"
      type="button"
      class="flex w-full items-center gap-2 whitespace-nowrap px-2.5 py-1.5 text-left text-[11px]"
      :aria-expanded="false"
      @click="setOpen(true)"
    >
      <span class="font-semibold">{{ __('Sources') }}</span>
      <span
        v-for="r in rows"
        :key="r.key"
        class="inline-flex items-center gap-1 text-ink-gray-6"
      >
        <SourceIcon :kind="r.icon" small />{{ r.short }}
      </span>
      <span class="ml-auto text-ink-gray-5">▸</span>
    </button>

    <div v-else class="px-3 py-2">
      <button
        type="button"
        class="mb-1 flex w-full items-center justify-between text-xs font-semibold"
        :aria-expanded="true"
        @click="setOpen(false)"
      >
        {{ __('Sources') }}
        <span class="flex items-center gap-1.5 text-[11px] font-normal text-ink-gray-5">
          {{ summary }}
          <span class="rounded bg-surface-gray-2 px-1 text-[10px] text-ink-gray-7">▾</span>
        </span>
      </button>
      <div
        v-for="(r, i) in rows"
        :key="r.key"
        class="grid grid-cols-[16px_64px_1fr] items-start gap-1.5 py-1.5 text-xs"
        :class="i ? 'border-t border-outline-gray-1' : ''"
      >
        <SourceIcon :kind="r.icon" />
        <span class="font-semibold">{{ r.name }}</span>
        <span class="text-[11px] leading-snug text-ink-gray-5">
          {{ r.what }}
          <template v-if="r.where">
            <br />
            <span
              class="mt-0.5 inline-block rounded px-1.5 py-px text-[10px]"
              :class="r.whereTone === 'queue' ? 'bg-blue-50 text-blue-700' : 'bg-surface-gray-2 text-ink-gray-6'"
            >{{ r.where }}</span>
          </template>
          <template v-if="r.progress">
            <span class="mt-1 block h-1 overflow-hidden rounded bg-surface-gray-3">
              <i class="block h-full bg-blue-500" :style="{ width: r.progress.pct + '%' }" />
            </span>
            {{ r.progress.label }}
          </template>
        </span>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, h, ref } from 'vue'

const props = defineProps({
  /** `get_lead_comps().sources`. */
  sources: { type: Object, default: null },
  /** Floating over the map (desktop) vs a full-width strip under it (phone). */
  overlay: { type: Boolean, default: false },
})

// Tiny inline icon: a check, a spinner, a "…", a "$", a "!" -- the same five
// shapes as the approved mockups, no icon-font dependency.
const SourceIcon = (p) => {
  const size = p.small ? 'size-3 text-[8px]' : 'mt-px size-3.5 text-[10px]'
  if (p.kind === 'spin')
    return h('span', {
      class: `${p.small ? '' : 'mt-0.5'} inline-block size-3 shrink-0 animate-spin rounded-full`,
      // Explicit colours: the theme's border palette is not guaranteed to
      // carry blue-200/700, and a missing class drew half a ring.
      style: 'border: 2px solid #bfdbfe; border-top-color: #1d4ed8',
    })
  const map = {
    ok: ['bg-green-600', '✓'],
    wait: ['bg-gray-400', '…'],
    paid: ['bg-violet-600', '$'],
    bad: ['bg-red-600', '!'],
    warn: ['bg-amber-500', '!'],
    off: ['bg-gray-300', '–'],
  }
  const [bg, glyph] = map[p.kind] || map.off
  return h(
    'span',
    { class: `${size} inline-flex shrink-0 items-center justify-center rounded-full text-white ${bg}` },
    glyph,
  )
}
SourceIcon.props = ['kind', 'small']

const KEY = 'compsSourcesOpen'
const pref = ref(localStorage.getItem(KEY))
const pending = computed(() => !!props.sources?.pending)
const open = computed(() => (pref.value == null ? pending.value : pref.value === '1'))
function setOpen(v) {
  pref.value = v ? '1' : '0'
  localStorage.setItem(KEY, pref.value)
}

function when(iso) {
  if (!iso) return null
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? null : d
}
function ago(iso) {
  const d = when(iso)
  if (!d) return ''
  const mins = (Date.now() - d.getTime()) / 60000
  if (mins < 2) return __('just now')
  if (mins < 60) return __('{0} min ago', [Math.round(mins)])
  const today = new Date()
  if (d.toDateString() === today.toDateString()) return __('today')
  const days = Math.round((today.setHours(0, 0, 0, 0) - new Date(d).setHours(0, 0, 0, 0)) / 86400000)
  if (days === 1) return __('yesterday')
  if (days < 14) return __('{0} days ago', [days])
  return day(iso)
}
function day(iso) {
  const d = when(iso)
  return d ? d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' }) : ''
}
function dayTime(iso) {
  const d = when(iso)
  if (!d) return ''
  return `${day(iso)}, ${d.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' })}`
}
function minutes(sec) {
  if (sec == null) return null
  if (sec < 60) return __('under a minute')
  return __('about {0} min', [Math.round(sec / 60)])
}
const OFF = {
  rentals: () => __('Not used for rentals'),
  not_configured: () => __('Not set up'),
  no_subject: () => __('No map point for this property'),
}

function zillowRow(s) {
  const r = { key: 'zillow', name: 'Zillow', short: 'Zillow' }
  if (s.state === 'off') return { ...r, icon: 'off', what: (OFF[s.reason] || OFF.not_configured)() }
  if (s.state === 'error') return { ...r, icon: 'bad', what: __("Didn't answer") }
  const bits = []
  if (s.rentals) bits.push(__('{0} rentals', [s.rentals]))
  if (s.for_sale) bits.push(__('{0} listings', [s.for_sale]))
  bits.push(s.sold ? __('{0} sales', [s.sold]) : __('no priced sales here'))
  return {
    ...r,
    icon: 'ok',
    what: bits.join(' · '),
    where: s.checked_at
      ? __('Saved area · checked {0}', [ago(s.checked_at)]) + (s.complete ? '' : __(' · partial'))
      : null,
  }
}

function redfinRow(s) {
  const r = { key: 'redfin', name: 'Redfin' }
  const added = s.added ? __('+{0} homes', [s.added]) : __('Nothing new beyond the others')
  switch (s.state) {
    case 'ready':
      return {
        ...r,
        icon: 'ok',
        short: 'Redfin',
        what: added,
        where: s.collected_at
          ? __('In PropWarehouse · collected {0}', [dayTime(s.collected_at)])
          : __('In PropWarehouse'),
      }
    case 'partial':
      return {
        ...r,
        icon: 'warn',
        short: 'Redfin',
        what: added,
        where: __("Some map sections couldn't be collected"),
      }
    case 'loading':
      return {
        ...r,
        icon: 'spin',
        short: 'Redfin',
        what: __('Reading PropWarehouse…'),
        where: __('Checking again in a few seconds'),
        whereTone: 'queue',
      }
    case 'queued': {
      const q = s.queue || {}
      let where
      if (!q.ahead && (q.running || 0) > 0) where = __('Collecting now')
      else where = __('Queued · #{0} in line', [(q.ahead || 0) + 1])
      const eta = minutes(q.eta_seconds)
      where += eta ? ` · ${eta}` : __(' · no estimate yet')
      const total = s.cells || 0
      const done = s.ready_cells || 0
      return {
        ...r,
        icon: 'spin',
        short: q.ahead ? `Redfin #${q.ahead + 1}` : 'Redfin',
        what: s.added
          ? __('+{0} homes so far', [s.added])
          : __('Not in PropWarehouse yet for this area'),
        where,
        whereTone: 'queue',
        progress: total
          ? { pct: Math.round((done / total) * 100), label: __('{0} of {1} map sections collected', [done, total]) }
          : null,
      }
    }
    case 'error':
      return { ...r, icon: 'bad', short: 'Redfin', what: __("Didn't answer — board is without Redfin") }
    default:
      return { ...r, icon: 'off', short: 'Redfin', what: (OFF[s.reason] || OFF.not_configured)() }
  }
}

function realtorRow(s) {
  const r = { key: 'realtor', name: 'Realtor', short: 'Realtor' }
  if (s.state === 'off') return { ...r, icon: 'off', what: (OFF[s.reason] || OFF.not_configured)() }
  if (s.state === 'error') return { ...r, icon: 'bad', what: __("Didn't answer") }
  let where = s.live ? __('Fetched live just now') : __('Saved area · checked {0}', [ago(s.fetched_at)])
  if (s.no_zip) where += __(' · no ZIP, partial')
  return {
    ...r,
    icon: 'ok',
    what: s.added ? __('+{0} homes', [s.added]) : __('Nothing new beyond the others'),
    where,
  }
}

function batchRow(s) {
  const r = { key: 'batchdata', name: 'BatchData', short: 'BatchData' }
  switch (s.state) {
    case 'saved':
      return {
        ...r,
        icon: 'paid',
        what: __('{0} recorded sales', [s.count]),
        where: __('Saved on this lead · bought {0} · no new charge', [day(s.saved_at)]),
      }
    case 'bought':
      return { ...r, icon: 'paid', what: __('{0} recorded sales', [s.count]), where: __('Bought just now ($0.15)') }
    case 'none_found':
      return {
        ...r,
        icon: 'ok',
        what: __('No recorded sales nearby'),
        where: s.saved_at ? __('Checked {0}', [day(s.saved_at)]) : __('Checked just now'),
      }
    case 'waiting':
      return {
        ...r,
        icon: 'wait',
        what: __('Waiting on Redfin. Buys ($0.15) only if no recent priced sales turn up'),
      }
    case 'skipped':
      return {
        ...r,
        icon: 'off',
        what: s.priced_solds
          ? __('Not needed · {0} recent priced sales already here', [s.priced_solds])
          : __('Not needed'),
      }
    case 'error':
      return { ...r, icon: 'bad', what: __("Didn't answer") }
    default:
      return { ...r, icon: 'off', what: (OFF[s.reason] || OFF.not_configured)() }
  }
}

const rows = computed(() => {
  const s = props.sources
  if (!s) return []
  return [
    s.zillow && zillowRow(s.zillow),
    s.redfin && redfinRow(s.redfin),
    s.realtor && realtorRow(s.realtor),
    s.batchdata && batchRow(s.batchdata),
  ].filter(Boolean)
})

const summary = computed(() => {
  const busy = rows.value.filter((r) => r.icon === 'spin' || r.icon === 'wait').length
  const bad = rows.value.filter((r) => r.icon === 'bad').length
  if (busy) return __('{0} of {1} ready', [rows.value.length - busy, rows.value.length])
  if (bad) return bad === 1 ? __("1 didn't answer") : __("{0} didn't answer", [bad])
  return __('all ready')
})
</script>
