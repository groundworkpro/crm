<template>
  <Dialog
    v-model="show"
    :options="{ size: '4xl', title: comp?.address || __('Comparable property') }"
  >
    <template #body>
      <div
        id="comp-detail-modal"
        class="flex h-[calc(100dvh-0.5rem)] max-h-[calc(100dvh-0.5rem)] flex-col overflow-hidden bg-surface-modal sm:h-[82vh] sm:max-h-[82vh]"
      >
        <div class="flex items-start justify-between gap-3 border-b px-4 py-3 sm:gap-4 sm:px-6 sm:py-4">
          <div class="min-w-0">
            <div class="mb-1 flex flex-wrap items-center gap-2">
              <Badge
                v-if="subjectMode"
                variant="subtle"
                theme="blue"
                :label="__('This property')"
              />
              <!-- Hand-styled from the shared palette rather than a Badge:
                   frappe-ui's Badge only knows gray/blue/green/orange/red, so a
                   `theme="violet"` would silently render an unstyled chip. This
                   also means the four states look identical here, on the tray
                   card and on the map, which is the whole point of one palette. -->
              <span
                v-else
                class="rounded px-1.5 py-0.5 text-xs font-semibold"
                :style="{ background: palette.bg, color: palette.ink }"
              >
                {{ statusLabel }}
              </span>
              <!-- A comp is not similar to itself; "5/5 fit" on the subject is
                   noise where a real judgement belongs. -->
              <Badge
                v-if="fit.total && !subjectMode"
                variant="subtle"
                :theme="fit.theme"
                :label="__('{0}/{1} fit', [fit.matched, fit.total])"
              />
              <Badge v-if="comp?.selected" variant="subtle" theme="blue" :label="__('Using')" />
              <!-- Same condition tag as the tray card. Native select (reka-ui
                   drops an empty-string item value, and '' is "untagged"). -->
              <select
                v-if="comp?.selected && canTag && !subjectMode"
                class="h-6 cursor-pointer rounded border-0 bg-surface-gray-2 py-0 pl-1.5 pr-6 text-xs font-medium focus:ring-1 focus:ring-outline-gray-3"
                :class="comp.comp_type ? 'text-ink-gray-8' : 'text-ink-gray-5'"
                :value="comp.comp_type || ''"
                :title="__('Condition of this comp')"
                @change="$emit('setType', comp.name, $event.target.value)"
              >
                <option value="">{{ __('Condition…') }}</option>
                <option v-for="t in COMP_CONDITION_TYPES" :key="t" :value="t">{{ t }}</option>
              </select>
              <span
                v-else-if="comp?.comp_type && !subjectMode"
                class="rounded bg-surface-gray-2 px-1.5 py-0.5 text-xs font-medium text-ink-gray-7"
              >{{ comp.comp_type }}</span>
            </div>
            <h2 class="truncate text-xl font-semibold text-ink-gray-9">
              <a
                v-if="shareUrl"
                :href="shareUrl"
                class="hover:underline"
                :title="__('Copy link to this property') + ' (L)'"
                @click="onShareClick"
              >{{ comp?.address || __('Comparable property') }}</a>
              <template v-else>{{ comp?.address || __('Comparable property') }}</template>
            </h2>
            <div class="mt-0.5 text-sm text-ink-gray-5">
              {{ compLocation }}
            </div>
          </div>
          <div class="flex shrink-0 items-center gap-2">
            <Button
              v-if="shareUrl"
              variant="ghost"
              icon="link"
              :title="__('Copy link to this property') + ' (L)'"
              @click="copyDetailLink"
            />
            <!-- A house cannot be a comp for itself, so the subject gets no
                 add-as-comp button rather than one that would corrupt the calc. -->
            <Button
              v-if="!subjectMode"
              class="hidden sm:inline-flex"
              :variant="comp?.selected ? 'subtle' : 'solid'"
              :label="comp?.selected ? __('Remove from table') : __('Add as comp')"
              @click="$emit('use', comp.name)"
            />
            <Button
              v-if="hasStreetView"
              class="hidden sm:inline-flex"
              variant="subtle"
              :label="__('Street View')"
              @click="$emit('street')"
            />
            <Button variant="ghost" icon="x" @click="show = false" />
          </div>
        </div>

        <!-- GOTCHA — the stacked (phone) column MUST be `minmax(0,1fr)`, not the
             implicit `auto` track. A scroll container's overflow does not shrink
             its min-content CONTRIBUTION to the grid, so the 25-thumbnail strip
             sized the whole column to ~2,600px (measured at 390px: 2616). The
             dialog's overflow-hidden clipped it, the aside text ran off the right
             edge, and the `object-contain` hero centred itself ~1,300px
             off-screen — a black frame with working arrows and a working thumb
             strip, i.e. "the pictures aren't loading on mobile". Same reason the
             md columns already carried minmax(0,…). -->
        <div class="grid min-h-0 flex-1 grid-cols-[minmax(0,1fr)] md:grid-cols-[minmax(0,1.55fr)_minmax(18rem,0.85fr)]">
          <div class="flex min-h-0 min-w-0 flex-col border-b bg-surface-gray-2 md:border-b-0 md:border-r">
            <!-- Stacked, the frame takes a definite 4:3 height (flex-1 has nothing
                 to fill in an auto row) and the hero is absolute so its size never
                 depends on percentage-height resolution inside a min-h flex box. -->
            <div class="relative flex aspect-[4/3] min-h-[12rem] items-center justify-center overflow-hidden bg-black/90 sm:min-h-[16rem] md:aspect-auto md:flex-1">
              <!-- no-referrer matches CompTrayCard/CompSubjectCard: Zillow's
                   imgSrc for photo-less/land homes is a Google staticmap URL
                   signed with THEIR referer-restricted key — 200 with no
                   Referer, 403 with ours — so the tray thumbnail loaded while
                   this, the first gallery frame, broke on click. -->
              <!-- Hero URL is often known before the bytes are: subject
                   cover_photo, a hover-prefetch of get_comp_details, or the
                   in-modal cache. Gating the spinner on `!photos.length`
                   left a black frame for the whole CDN wait. Overlay until
                   @load; keep the img mounted (opacity-0) so it can paint. -->
              <img
                v-if="photos.length && !photoBroken"
                ref="photoEl"
                :src="photoSrc"
                :alt="comp?.address"
                class="absolute inset-0 size-full object-contain transition-opacity"
                :class="heroLoaded ? 'opacity-100' : 'opacity-0'"
                referrerpolicy="no-referrer"
                @error="onPhotoError"
                @load="onPhotoLoad"
              />
              <!-- A Google streetscape is context, not a listing photo. Label it
                   on the image itself so a rep cannot mistake the fallback for
                   seller/MLS evidence. It only exists when every real-photo rung
                   (Redfin → Realtor → Zillow) above it returned empty. -->
              <div
                v-if="isStaticStreetView && heroLoaded"
                class="absolute left-3 top-3 rounded-full bg-black/70 px-2.5 py-1 text-xs font-medium text-white"
              >
                {{ __('Street View') }}
              </div>
              <div
                v-if="photoPending"
                class="absolute inset-0 z-10 flex flex-col items-center justify-center gap-2 text-sm text-white"
              >
                <span class="size-6 animate-spin rounded-full border-2 border-white/25 border-t-white" />
                {{ __('Loading photos…') }}
              </div>
              <div
                v-else-if="photoBroken || !photos.length"
                class="flex flex-col items-center gap-2 px-6 text-center text-sm text-white/80"
              >
                <FeatherIcon name="image" class="size-7" />
                <template v-if="photoBroken">
                  {{ __('This photo did not load.') }}
                  <button class="font-medium text-white hover:underline" @click="retryPhoto">
                    {{ __('Retry') }}
                  </button>
                </template>
                <template v-else>
                  {{ __('No photos found on Zillow, Realtor, or Redfin for this property.') }}
                </template>
              </div>

              <template v-if="photos.length > 1">
                <button
                  class="absolute left-3 top-1/2 flex size-9 -translate-y-1/2 items-center justify-center rounded-full bg-black/55 text-white hover:bg-black/75"
                  :aria-label="__('Previous photo')"
                  @click="previousPhoto"
                >
                  <FeatherIcon name="chevron-left" class="size-5" />
                </button>
                <button
                  class="absolute right-3 top-1/2 flex size-9 -translate-y-1/2 items-center justify-center rounded-full bg-black/55 text-white hover:bg-black/75"
                  :aria-label="__('Next photo')"
                  @click="nextPhoto"
                >
                  <FeatherIcon name="chevron-right" class="size-5" />
                </button>
                <div class="absolute bottom-3 right-3 rounded-full bg-black/60 px-2.5 py-1 text-xs text-white">
                  {{ photoIndex + 1 }} / {{ photos.length }}
                </div>
              </template>
              <div
                v-if="!photoPending && (photoDateText || photoDateLoading)"
                class="absolute bottom-3 left-3 rounded-full bg-black/60 px-2.5 py-1 text-xs text-white"
              >
                {{ photoDateText || __('Dating…') }}
              </div>
            </div>

            <div v-if="photos.length > 1" class="flex shrink-0 gap-2 overflow-x-auto p-3">
              <button
                v-for="(photo, index) in photos"
                :key="photo"
                class="h-16 w-24 shrink-0 overflow-hidden rounded-md border-2 bg-surface-gray-3"
                :class="index === photoIndex ? 'border-outline-blue-2' : 'border-transparent opacity-70 hover:opacity-100'"
                @click="photoIndex = index"
              >
                <img :src="photo" :alt="__('Photo {0}', [index + 1])" class="size-full object-cover" loading="lazy" referrerpolicy="no-referrer" />
              </button>
            </div>
          </div>

          <aside class="min-h-0 overflow-y-auto bg-surface-white p-5 sm:p-6">
            <p v-if="comp?.sale_source === 'Auction.com'" class="mb-3 text-sm text-ink-gray-6">
              {{ __('ADC reported sale') }}: {{ formatCompMoney(comp.sale_price) }} · {{ comp.sale_date || __('date unknown') }}.
              {{ __('Historical evidence, not a current asking price or status.') }}
            </p>
            <div class="flex flex-wrap items-baseline justify-between gap-3">
              <div>
                <div class="text-2xl font-semibold text-ink-gray-9">
                  {{ formatCompMoney(displayPrice) }}
                </div>
                <div class="text-xs text-ink-gray-5">
                  {{ displayPriceLabel }}
                </div>
              </div>
              <div class="flex flex-wrap gap-x-3 gap-y-2">
                <a
                  v-for="link in propertyLinks"
                  :key="link.label"
                  :href="link.href"
                  target="_blank"
                  rel="noopener noreferrer"
                  class="flex items-center gap-1 text-sm font-medium text-ink-blue-3 hover:underline"
                >
                  {{ __(link.label) }}
                  <FeatherIcon name="arrow-up-right" class="size-3.5" />
                </a>
              </div>
            </div>

            <div v-if="staleNote" class="mt-3 rounded-lg bg-surface-amber-1 p-3 text-xs leading-relaxed text-ink-amber-3">
              {{ staleNote }}
            </div>

            <div class="mt-5 grid grid-cols-2 gap-x-4 gap-y-3 text-sm">
              <div v-for="fact in facts" :key="fact.label">
                <div class="text-xs text-ink-gray-5">{{ fact.label }}</div>
                <!-- The subject's living area is the one fact a rep can correct
                     from here: Zillow is sometimes plainly wrong about the house
                     being priced, and this panel is where they are looking when
                     they notice. Same write as the tray card's pencil. -->
                <template v-if="fact.editable">
                  <div v-if="editingSqft" class="mt-0.5 flex flex-wrap items-center gap-1">
                    <input
                      ref="sqftInput"
                      v-model="sqftDraft"
                      type="text"
                      inputmode="numeric"
                      :placeholder="__('sqft')"
                      class="h-7 w-24 rounded border border-outline-gray-2 bg-surface-white px-2 text-sm text-ink-gray-9 focus:border-outline-gray-4 focus:outline-none"
                      @keydown.enter.prevent="saveSqft"
                      @keydown.esc.prevent="editingSqft = false"
                    />
                    <Button size="sm" variant="solid" :label="__('Save')" @click="saveSqft" />
                    <Button
                      v-if="manualSqft"
                      size="sm"
                      variant="subtle"
                      :label="__('Reset')"
                      :title="__('Clear the manual value and go back to Zillow/listing data')"
                      @click="clearSqft"
                    />
                    <Button size="sm" variant="ghost" icon="x" @click="editingSqft = false" />
                  </div>
                  <div v-else class="mt-0.5 flex items-center gap-1.5">
                    <button
                      type="button"
                      class="group flex items-center gap-1 rounded font-medium text-ink-gray-8 hover:text-ink-gray-9"
                      :title="manualSqft ? __('Edit square footage (set manually)') : __('Edit square footage')"
                      @click="startSqftEdit"
                    >
                      {{ fact.value }}
                      <FeatherIcon name="edit-2" class="size-3 text-ink-gray-4 group-hover:text-ink-gray-8" />
                    </button>
                    <span
                      v-if="manualSqft"
                      class="rounded bg-surface-gray-2 px-1 text-2xs text-ink-gray-6"
                      :title="__('Square footage was set manually and overrides Zillow/listing data')"
                    >
                      {{ __('Manual') }}
                    </span>
                  </div>
                </template>
                <div v-else class="mt-0.5 font-medium text-ink-gray-8">{{ fact.value }}</div>
              </div>
            </div>

            <div v-if="fit.total" class="mt-5 rounded-lg border border-outline-gray-1 bg-surface-gray-1 p-3">
              <div class="text-sm font-medium text-ink-gray-8">
                {{ __('Similarity to the subject') }}
              </div>
              <div class="mt-2 flex flex-wrap gap-1.5">
                <span
                  v-for="dimension in fit.dimensions"
                  :key="dimension.key"
                  class="rounded px-2 py-1 text-xs"
                  :class="dimension.matches ? 'bg-surface-green-2 text-ink-green-3' : 'bg-surface-amber-1 text-ink-amber-3'"
                >
                  {{ dimension.matches ? '✓' : '△' }} {{ __(dimension.label) }}
                </span>
              </div>
              <div class="mt-2 text-xs leading-relaxed text-ink-gray-5">
                {{ __('Fit uses the same type, beds, baths, size and age tolerances as the comps filter.') }}
              </div>
            </div>

            <div v-if="timeline.length" class="mt-5">
              <div class="text-sm font-medium text-ink-gray-8">{{ __('Property history') }}</div>
              <div class="mt-2 flex flex-col gap-2 text-sm">
                <div v-for="row in timeline" :key="row.label" class="flex justify-between gap-3">
                  <span class="text-ink-gray-5">{{ row.label }}</span>
                  <span class="text-right font-medium text-ink-gray-8">{{ row.value }}</span>
                </div>
              </div>
            </div>

            <div v-if="details?.description" class="mt-5">
              <div class="text-sm font-medium text-ink-gray-8">{{ __('About this property') }}</div>
              <p class="mt-2 whitespace-pre-line text-sm leading-relaxed text-ink-gray-6">
                {{ details.description }}
              </p>
            </div>

            <div
              v-if="error || (!loading && response && !response.available && !photos.length)"
              class="mt-5 rounded-lg border border-outline-gray-2 bg-surface-gray-1 p-3 text-sm text-ink-gray-6"
            >
              {{ error || response?.message || __('More details are unavailable right now.') }}
              <button class="ml-1 font-medium text-ink-blue-3 hover:underline" @click="load(true)">
                {{ __('Retry') }}
              </button>
            </div>
          </aside>
        </div>
        <!-- Phone: the header only has room for ✕. These three are the actions
             a rep actually takes from this screen. -->
        <div class="flex shrink-0 gap-2 border-t px-4 py-3 sm:hidden">
          <Button class="flex-1" :label="__('Close')" @click="show = false" />
          <Button
            v-if="hasStreetView"
            class="flex-1"
            variant="subtle"
            :label="__('Street View')"
            @click="$emit('street')"
          />
          <Button
            v-if="!subjectMode"
            class="flex-1"
            :variant="comp?.selected ? 'subtle' : 'solid'"
            :label="comp?.selected ? __('Remove') : __('Add as comp')"
            @click="$emit('use', comp.name)"
          />
        </div>
      </div>
    </template>
  </Dialog>
</template>

<script setup>
import { COMP_CONDITION_TYPES, compsDetailUrl, compColor, compFit, compState, compStateLabel, daysToSell, formatCompMoney } from '@/utils/comps'
import { copyToClipboard } from '@/utils'
import { propertySearchAddress, providerLink, zillowUrl } from '@/utils/propertyLinks'
import { Badge, Button, Dialog, FeatherIcon, call } from 'frappe-ui'
import { computed, nextTick, onBeforeUnmount, reactive, ref, watch } from 'vue'

const emit = defineEmits(['use', 'street', 'setType', 'saveSqft'])

const props = defineProps({
  lead: { type: String, required: true },
  comp: { type: Object, default: null },
  subject: { type: Object, default: null },
  // Condition tagging is a team-wide write; hosts turn it off in practice mode.
  canTag: { type: Boolean, default: false },
  // The same panel, showing the SUBJECT rather than a comp. Only two things
  // actually differ -- which endpoint supplies the photos, and the handful of
  // comp-only affordances below -- so this is a flag rather than a second
  // component that would drift away from this one.
  subjectMode: { type: Boolean, default: false },
  // Subject-mode only: the sqft override is a team-wide fact write on the
  // lead, so hosts turn it off in practice runs (same flag CompSubjectCard takes).
  canEditSqft: { type: Boolean, default: false },
  // Practice writes to an attempt, not the lead — a comps-page link would open
  // the real house, which is the wrong surface mid-run.
  canShare: { type: Boolean, default: true },
})

const show = defineModel({ type: Boolean })

// --- manual subject sqft (mirrors CompSubjectCard) ------------------------
const editingSqft = ref(false)
const sqftDraft = ref('')
const sqftInput = ref(null)
const manualSqft = computed(() => props.subjectMode && props.subject?.source?.sqft === 'manual')
// The subject's OWN facts win over the Zillow detail blob here: an override
// lives on props.subject, and the detail call is exactly the source it overrides.
const subjectSqft = computed(() =>
  props.subjectMode && props.subject?.sqft_exact && props.subject?.sqft ? props.subject.sqft : null,
)

const shareUrl = computed(() => {
  if (!props.canShare || !props.lead) return ''
  return compsDetailUrl(props.lead, {
    compName: props.comp?.name,
    subject: props.subjectMode,
  })
})

function copyDetailLink() {
  if (!shareUrl.value) return
  copyToClipboard(shareUrl.value)
}

function onShareClick(e) {
  if (e.metaKey || e.ctrlKey) return
  e.preventDefault()
  copyDetailLink()
}

function startSqftEdit() {
  sqftDraft.value = subjectSqft.value ? String(Math.round(subjectSqft.value)) : ''
  editingSqft.value = true
  nextTick(() => sqftInput.value?.focus())
}

function saveSqft() {
  const n = Math.round(Number(String(sqftDraft.value).replace(/[^0-9.]/g, '')))
  if (!Number.isFinite(n) || n <= 0) return
  editingSqft.value = false
  emit('saveSqft', n)
}

function clearSqft() {
  editingSqft.value = false
  emit('saveSqft', null)
}

watch(show, (v) => {
  if (!v) editingSqft.value = false
  else nextTick(syncHeroFromEl)
})
const loading = ref(false)
const error = ref('')
const response = ref(null)
const photoIndex = ref(0)
const cache = new Map()
let requestToken = 0

// Provider-synthesized Street View frames ride a SHARED, quota-limited Google
// key, so they fail in bursts — the classic "the photo frequently doesn't
// show". One cache-busted reload usually lands (the key does not enforce its
// URL signature); a photo that fails twice gets the placeholder, never a
// broken frame.
const photoBust = ref(0)
const photoBroken = ref(false)
const photoEl = ref(null)
const heroLoaded = ref(false)
const streetViewUnavailable = ref(false)
const retriedPhotos = new Set()

const photoSrc = computed(() => {
  const url = photos.value[photoIndex.value] || ''
  if (!url || !photoBust.value || !retriedPhotos.has(url)) return url
  return url + (url.includes('?') ? '&' : '?') + 'cb=' + photoBust.value
})

const photoPending = computed(() => {
  if (photoBroken.value) return false
  if (loading.value && !photos.value.length) return true
  return !!(photos.value.length && !heroLoaded.value)
})

function syncHeroFromEl() {
  const el = photoEl.value
  if (el && el.complete && el.naturalWidth > 0) heroLoaded.value = true
}

function onPhotoError() {
  const url = photos.value[photoIndex.value] || ''
  if (!url) return
  // The relay returns 404 when metadata says there is no panorama or any
  // upstream validation fails. That is a clean "no image" answer, not a broken
  // gallery frame and not something to retry/bill in a loop.
  if (isStaticStreetView.value) {
    streetViewUnavailable.value = true
    heroLoaded.value = false
    photoBroken.value = false
    return
  }
  if (url.includes('maps.googleapis.com') && !retriedPhotos.has(url)) {
    retriedPhotos.add(url)
    photoBust.value = Date.now()
    return
  }
  heroLoaded.value = false
  photoBroken.value = true
}

function onPhotoLoad() {
  photoBroken.value = false
  heroLoaded.value = true
}

function retryPhoto() {
  const url = photos.value[photoIndex.value] || ''
  retriedPhotos.add(url)
  photoBust.value = Date.now()
  photoBroken.value = false
}

// Any navigation (arrows, thumbnails, a new comp) gets a clean slate — a broken
// state left behind would show the placeholder over a perfectly good photo.
watch(photoIndex, () => {
  photoBroken.value = false
})

const details = computed(() => response.value?.details || null)
// These are the three real-photo rungs, plus the already-existing cover-photo
// compatibility fallback. Street View MUST NOT enter this list: keeping it
// separate makes the "only after every real photo is empty" contract visible
// and prevents a later refactor from letting it displace listing imagery.
const realPhotos = computed(() => {
  const list = response.value?.photos || []
  if (list.length) return list
  const cover =
    (props.subjectMode && props.subject?.cover_photo) ||
    details.value?.cover_photo ||
    ''
  return cover ? [cover] : []
})
const streetViewPhotoUrl = computed(() => {
  // COMP gallery only. The lead desk's interactive Maps Embed iframe is a
  // different product/key and stays in utils/streetView.js.
  if (props.subjectMode || !response.value || loading.value || realPhotos.value.length) return ''
  if (streetViewUnavailable.value) return ''
  const lat = props.comp?.lat ?? details.value?.lat
  const lng = props.comp?.lng ?? details.value?.lng
  if (lat == null || lng == null || !Number.isFinite(Number(lat)) || !Number.isFinite(Number(lng))) return ''
  const qs = new URLSearchParams({ lat: String(Number(lat)), lng: String(Number(lng)) })
  return `/api/method/crm.api.streetview.comp_streetview?${qs.toString()}`
})
const photos = computed(() => {
  if (realPhotos.value.length) return realPhotos.value
  return streetViewPhotoUrl.value ? [streetViewPhotoUrl.value] : []
})
const isStaticStreetView = computed(
  () => !!streetViewPhotoUrl.value && photos.value[photoIndex.value] === streetViewPhotoUrl.value,
)
const photoDateByUrl = reactive({})
const photoDateState = reactive({})
function canDatePhoto(url) {
  if (!url) return false
  if (url.includes('maps.googleapis.com') || url.includes('imgix.net')) return false
  if (url.includes('crm.api.streetview.comp_streetview')) return false
  return true
}
function photoDateLabel(iso) {
  if (!iso) return ''
  const d = Date.parse(iso)
  if (!Number.isFinite(d)) return ''
  return new Date(d).toLocaleDateString('en-US', { month: 'short', year: 'numeric' })
}
function askPhotoDate(url) {
  if (!canDatePhoto(url) || photoDateState[url]) return
  photoDateState[url] = 'loading'
  call('crm.api.comps.get_photo_date', { url })
    .then((r) => {
      photoDateByUrl[url] = r?.exif || ''
    })
    .catch(() => {
      photoDateByUrl[url] = ''
    })
    .finally(() => {
      photoDateState[url] = 'done'
    })
}
const photoDateText = computed(() =>
  photoDateLabel(photoDateByUrl[photos.value[photoIndex.value]]),
)
const photoDateLoading = computed(() => {
  const url = photos.value[photoIndex.value]
  return canDatePhoto(url) && photoDateState[url] === 'loading'
})
watch(
  () => [photos.value[photoIndex.value], photos.value[photoIndex.value + 1]],
  (urls) => {
    for (const url of urls) askPhotoDate(url)
  },
  { immediate: true },
)

watch(photoSrc, () => {
  heroLoaded.value = false
  nextTick(syncHeroFromEl)
})

const fit = computed(() => compFit(props.comp, props.subject))
const streetPoint = computed(() => {
  if (props.subjectMode) {
    const s = props.subject
    if (s?.lat != null && s?.lng != null) return s
  }
  const c = props.comp
  if (c?.lat != null && c?.lng != null) return c
  return null
})
const hasStreetView = computed(() => !!streetPoint.value)

// Status, in the same four-state grammar as the pills and the tray cards.
//
// Fresh-vs-pin reconciliation: the pin is a SNAPSHOT (the 7-day circle cache,
// or the pooled index's last ask); `details` is a LIVE Zillow read made on this
// click. When the two disagree about whether the house is on the market, the
// live read wins — the 1712 W Seybert pin said "For sale · $379,999" for a
// house Zillow knew had sold for $91,000 weeks earlier (a scrubbed bogus
// listing frozen in our cache), and a rep reads that headline as ARV evidence.
// Only the three definite Zillow statuses carry an opinion; OTHER/null defer
// to the pin.
const FRESH_STATES = {
  RECENTLY_SOLD: 'sold',
  SOLD: 'sold',
  FOR_SALE: 'for_sale',
  PENDING: 'pending',
  FOR_RENT: 'for_rent',
}
const pinState = computed(() => compState(props.comp))
const freshState = computed(() => {
  if (props.subjectMode) return null
  if (details.value?.is_for_auction) return 'auction'
  return FRESH_STATES[String(details.value?.home_status || '').trim().toUpperCase()] || null
})
const state = computed(() => freshState.value || pinState.value)
const statusLabel = computed(() => compStateLabel(state.value))
const palette = computed(() => compColor({ ...(props.comp || {}), listing_state: state.value }))
// The trust-damaging case gets said out loud: the pin claimed a LIVE listing
// (with its price in the headline) and the live read says the house has sold.
const staleListing = computed(
  () => freshState.value === 'sold' && (pinState.value === 'for_sale' || pinState.value === 'pending'),
)
const staleNote = computed(() => {
  if (!staleListing.value) return ''
  const sale = details.value?.last_sale
  const sold = sale?.price
    ? formatCompMoney(sale.price) + (sale.date ? ' · ' + dateOnly(sale.date) : '')
    : ''
  const ask = props.comp?.price ? formatCompMoney(props.comp.price) : ''
  if (sold && ask)
    return __('Zillow now reports this property sold ({0}). The {1} listing on this pin is out of date.', [sold, ask])
  if (sold) return __('Zillow now reports this property sold ({0}); this pin is out of date.', [sold])
  return __('Zillow now reports this property sold; the listing on this pin is out of date.')
})
const compLocation = computed(() =>
  [props.comp?.city, props.comp?.state, props.comp?.zip].filter(Boolean).join(', '),
)
const displayPrice = computed(() => {
  // A reconciled sale outranks everything: once Zillow says the house SOLD,
  // neither the pin's stale ask nor a leftover asking_price is the number.
  if (freshState.value === 'sold' && details.value?.last_sale?.price)
    return details.value.last_sale.price
  // Auctions often have asking_price 0. Do not fall through to a public-record
  // transfer sitting on the pin (1623 Nevada: $190,400 "last known list price").
  if (state.value === 'auction') {
    const ask = Number(details.value?.asking_price)
    return Number.isFinite(ask) && ask > 0 ? ask : null
  }
  // `/property` asking_price is a SALE ask. A rental pin's price is monthly rent.
  if (state.value === 'for_rent') return props.comp?.price
  if (props.comp?.price_basis === 'adc_sale' && !freshState.value) return props.comp.sale_price
  return details.value?.asking_price || props.comp?.price || details.value?.zestimate
})
const displayPriceLabel = computed(() => {
  // Name the number honestly: an agreed contract price, a live ask, a verified
  // sale, an estimate and a stale last-ask are five different claims, and only
  // some of them are prices anybody ever committed to.
  if (freshState.value === 'sold' && details.value?.last_sale?.price) {
    const when = details.value?.last_sale?.date
    return when ? __('Sold · {0}', [dateOnly(when)]) : __('Sold')
  }
  if (state.value === 'for_rent') return __('Monthly rent')
  if (state.value === 'auction') {
    return Number(displayPrice.value) > 0 ? __('Auction') : __('Auction — no asking price')
  }
  if (props.comp?.price_basis === 'adc_sale' && !freshState.value) return __('ADC reported sale')
  if (details.value?.asking_price) return __('Current Zillow ask')
  if (state.value === 'pending') return __('Agreed price · under contract')
  // The subject is not on the market, so its headline number is whatever it last
  // SOLD for -- which is a recorded transaction and must not inherit the comps'
  // "last known list price", the exact confusion the pin popup exists to avoid.
  if (props.subjectMode && props.comp?.price) {
    const when = details.value?.last_sale?.date
    return when ? __('Last sold · {0}', [dateOnly(when)]) : __('Last sold')
  }
  if (props.comp?.price_basis === 'adc_sale') return __('ADC reported sale')
  if (props.comp?.price) return __('Last known list price')
  return details.value?.zestimate ? __('Zestimate — no listing price on record') : ''
})
const propertyLinks = computed(() => {
  const address = propertySearchAddress(props.comp)
  const subject = props.subjectMode ? props.subject : null
  const zillow = details.value?.zillow_url || zillowUrl(props.comp?.address || '')
  return [
    zillow ? { label: 'Zillow', href: zillow } : null,
    providerLink('Redfin', address, response.value?.redfin_url || subject?.redfin_url),
    providerLink('Realtor', address, subject?.realtor_estimate?.href),
  ].filter(Boolean)
})
const facts = computed(() => {
  const d = details.value || {}
  const c = props.comp || {}
  return [
    { label: __('Beds'), value: decimal(d.beds || c.bedrooms) },
    { label: __('Baths'), value: decimal(d.baths || c.bathrooms) },
    {
      label: __('Living area'),
      value: area(subjectSqft.value || d.sqft || c.square_footage),
      editable: props.subjectMode && props.canEditSqft,
    },
    {
      label: __('Lot size'),
      value:
        d.lot_size ||
        c.lot_size ||
        (props.subjectMode && props.subject?.lot_size) ||
        '—',
    },
    { label: __('Year built'), value: whole(d.year_built || c.year_built) },
    { label: __('Property type'), value: d.property_type || c.property_type || '—' },
    // "0.0 mi from itself" is not a fact about the subject.
    ...(props.subjectMode
      ? []
      : [
          {
            label: __('Distance'),
            value: c.distance_mi ? `${Number(c.distance_mi).toFixed(1)} mi` : '—',
          },
        ]),
    { label: __('Zestimate'), value: formatCompMoney(d.zestimate) },
  ]
})
const timeline = computed(() => {
  const rows = []
  if (details.value?.last_sale?.price) {
    rows.push({
      label: __('Last sold'),
      value: `${formatCompMoney(details.value.last_sale.price)} · ${dateOnly(details.value.last_sale.date)}`,
    })
  }
  if (props.comp?.listed_date) {
    rows.push({ label: __('Listed'), value: dateOnly(props.comp.listed_date) })
  }
  if (props.comp?.removed_date) {
    rows.push({ label: __('Went off market'), value: dateOnly(props.comp.removed_date) })
  } else if (props.comp?.days_on_market) {
    rows.push({ label: __('Time on market'), value: __('{0} days', [props.comp.days_on_market]) })
  }
  // Time to sell is a FINISHED measurement off the listing chain, so it is named
  // differently from "time on market" above, which on a live listing is a clock
  // still running. Price cuts ride with it because "sold in 169 days after 3 cuts"
  // and "sold in 169 days at the asking price" are opposite stories about a price.
  const sh = props.comp?.sale_history
  const took = daysToSell(props.comp)
  if (took != null) {
    const cuts = Number(sh?.price_cuts) || 0
    rows.push({
      label: __('Took to sell'),
      value:
        __('{0} days', [Math.round(took)]) +
        (cuts > 0 ? ` · ${cuts === 1 ? __('1 price cut') : __('{0} price cuts', [cuts])}` : ''),
    })
  }
  // The purchase that makes this a possible flip. Shown as its own row rather than
  // folded into "Last sold", because the point is the PAIR of transactions.
  const f = sh?.flip
  if (f) {
    rows.push({
      label: f.kind === 'relist' ? __('Bought before relisting') : __('Bought before resale'),
      value: __('{0} on {1} · held {2} · {3}% more', [
        formatCompMoney(f.bought_price),
        dateOnly(f.bought_date),
        __('{0} days', [Math.round(f.hold_days)]),
        Math.round((f.pct || 0) * 100),
      ]),
    })
  }
  return rows
})

watch(
  () => [show.value, props.comp?.name],
  ([open]) => {
    if (!open || !props.comp?.name) return
    photoIndex.value = 0
    load()
  },
  // CompsView mounts this with v-if on the chosen comp, which is set in the same
  // tick as `show` — so the component's first render already has show=true. A
  // non-immediate watcher would never see a transition and the gallery would
  // sit forever on its empty fallback despite the endpoint returning photos.
  { immediate: true },
)

async function load(force = false) {
  if (!props.comp?.name) return
  const name = props.comp.name
  streetViewUnavailable.value = false
  if (!force && cache.has(name)) {
    response.value = cache.get(name)
    return
  }
  const token = ++requestToken
  loading.value = true
  error.value = ''
  response.value = null
  try {
    const result = props.subjectMode
      ? await call('crm.api.comps.get_subject_details', { lead: props.lead })
      : await call('crm.api.comps.get_comp_details', {
          lead: props.lead,
          comp: name,
          // Realtor/Redfin pins are not CRM Comp rows — without this address the
          // server used to throw "does not exist" before the photo ladder ran.
          // City/state/zip qualify a street-only line; lat/lng feed the Redfin sweep.
          address: props.comp.address || '',
          lat: props.comp.lat ?? null,
          lng: props.comp.lng ?? null,
          city: props.comp.city || '',
          state: props.comp.state || '',
          zip: props.comp.zip || '',
        })
    if (token !== requestToken) return
    response.value = result
    cache.set(name, result)
  } catch (e) {
    if (token !== requestToken) return
    error.value = e?.messages?.[0] || e?.message || __('Could not load this property.')
  } finally {
    if (token === requestToken) loading.value = false
  }
}

// Both wrap: past the last photo lands on the first and vice versa.
function previousPhoto() {
  photoIndex.value = (photoIndex.value - 1 + photos.value.length) % photos.value.length
}

function nextPhoto() {
  photoIndex.value = (photoIndex.value + 1) % photos.value.length
}

// ← / → page the gallery while the panel is open. A plain window listener,
// not useKeyboardShortcuts: this IS a Dialog, so that helper's
// skipWhenDialogOpen default would silently never fire (the CompsView trap),
// and CompsView's own U/H shortcuts already stand down while this is up.
// Skipped when the focus is in a field (the sqft input, the condition select)
// so the caret keeps its arrows.
function onGalleryKey(e) {
  if (!show.value) return
  if (e.altKey || e.ctrlKey || e.metaKey) return
  const t = e.target
  if (t && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName))) return
  if (e.key === 'l' || e.key === 'L') {
    if (!shareUrl.value) return
    e.preventDefault()
    copyDetailLink()
    return
  }
  if (photos.value.length < 2) return
  if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return
  e.preventDefault()
  e.key === 'ArrowLeft' ? previousPhoto() : nextPhoto()
}

watch(
  show,
  (v) => {
    if (v) window.addEventListener('keydown', onGalleryKey)
    else window.removeEventListener('keydown', onGalleryKey)
  },
  { immediate: true },
)
onBeforeUnmount(() => window.removeEventListener('keydown', onGalleryKey))

function decimal(value) {
  const n = Number(value)
  if (!Number.isFinite(n) || n <= 0) return '—'
  return Number.isInteger(n) ? String(n) : n.toFixed(1).replace(/\.0$/, '')
}

function whole(value) {
  const n = Number(value)
  // NO thousand separator: the only thing rendered through this is the year
  // built, and `toLocaleString` was printing a house built in 1910 as "1,910".
  return Number.isFinite(n) && n > 0 ? String(Math.round(n)) : '—'
}

function area(value) {
  const n = Number(value)
  return Number.isFinite(n) && n > 0 ? `${Math.round(n).toLocaleString()} sf` : '—'
}

function dateOnly(value) {
  if (!value) return '—'
  const [y, m, d] = String(value).slice(0, 10).split('-').map(Number)
  if (!y || !m || !d) return String(value)
  return new Date(y, m - 1, d).toLocaleDateString()
}
</script>

<!-- Unscoped: Dialog teleports to <body>. On a phone this is a full-screen
     gallery, not a 4xl card with desktop padding. -->
<style>
.dialog-overlay:has(#comp-detail-modal) > div {
  padding: 0.25rem !important;
}
.dialog-content:has(#comp-detail-modal) {
  max-width: calc(100vw - 0.5rem) !important;
  width: calc(100vw - 0.5rem);
  margin-top: 0 !important;
  margin-bottom: 0 !important;
}
@media (min-width: 640px) {
  .dialog-overlay:has(#comp-detail-modal) > div {
    padding: 2rem !important;
  }
  .dialog-content:has(#comp-detail-modal) {
    max-width: 56rem !important;
    width: auto;
  }
}
</style>
