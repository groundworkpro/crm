<template>
  <!-- `fill` makes this size to its container instead of to its content: the map
       and the property list share whatever height there is, each scrolling
       internally. The comps PAGE keeps its natural, scroll-the-page layout. -->
  <!-- NOTE this component has TWO root nodes (this and the detail modal), so it
       is a fragment and Vue does NOT inherit a `class` from its host. The height
       has to be decided here, from the props, or the map/tray split has no bound
       to scroll inside and grows to ~8,000px. -->
  <!-- overflow-y-auto in BOTH layouts. Compact used to be overflow-hidden so
       the map filled the phone; but the calc opens tall (the auction form is
       ~600px at 390px wide) and the Tax / liens card with Records unfolded is
       taller still, and under overflow-hidden everything past the first
       screen -- the rest of the card, the map -- was simply unreachable
       (Lance, 2026-09-10: "lien table won't go down"). The map keeps its
       16rem floor below, so the folded-calc case paints exactly as before;
       an overflowing header stack now scrolls instead of clipping. -->
  <div
    ref="rootEl"
    class="flex min-w-0 flex-col gap-2 overflow-y-auto"
    :class="fillHeight || !wide ? 'min-h-0 flex-1' : ''"
  >
  <!-- The calculator folds away, and that is a HEIGHT decision. Measured on the
       comps page at a 919px window: the calc is 358px and the map+tray got 342px
       -- the tool was smaller than the form sitting on top of it. Collapsing it
       hands those 358px straight to the map.

       Open by default, because it is the thing you do WITH the comps and hiding
       it would be deciding for the rep. The choice is remembered per user, like
       Details and Parcels, so folding it once is enough. -->
  <template v-if="pageMode && !compsFocusMap">
    <div v-show="!calcOpen" class="flex items-center justify-between gap-2">
      <button
        class="flex items-center gap-1.5 rounded-md border border-outline-gray-2 bg-surface-gray-1 px-2.5 py-1 text-xs font-medium text-ink-gray-7 hover:bg-surface-gray-2"
        :title="__('Show the offer calculator') + ' (C)'"
        @click="calcOpen = true"
      >
        <FeatherIcon name="chevron-right" class="size-3.5" />
        {{ __('Offer') }}
        <!-- The count is what makes this safe to collapse: the rep can see the
             calculator still has their picks without opening it. -->
        <span v-if="selectedComps.length" class="text-ink-gray-5">
          · {{ __('{0} picked', [selectedComps.length]) }}
        </span>
      </button>
    </div>
    <div v-show="calcOpen" class="min-w-0">
      <CompOfferCalc
        ref="calcRef"
        :lead="lead"
        :subject="data?.subject || null"
        :address="data?.address || address"
        :comps="selectedComps"
        :compact="!wide"
        :practice-attempt="practiceAttempt"
        :practice-property="practiceProperty"
        :seller-note="sellerNote"
        :seed="practiceSeed"
        :prefer-kind="preferKind"
        @remove="setCompState($event, 'none')"
        @open="openCompDetail"
        @kind="onCalcKind"
      >
        <template #head-end>
          <button
            type="button"
            class="flex items-center gap-1 rounded px-1.5 py-0.5 text-xs text-ink-gray-5 hover:bg-surface-gray-2 hover:text-ink-gray-8"
            :title="__('Hide the calculator and give the map its height') + ' (C)'"
            @click="calcOpen = false"
          >
            <FeatherIcon name="chevron-up" class="size-3.5" />
            {{ __('Hide') }}
          </button>
        </template>
      </CompOfferCalc>
    </div>
  </template>
  <!-- Address and counts share ONE line with the controls. They used to be
       stacked, which cost a whole row of height at the top of a page whose
       entire job is to show a map. -->
  <div class="flex min-w-0 flex-wrap items-center justify-between gap-x-3 gap-y-1">
    <div class="flex min-w-0 flex-1 flex-wrap items-baseline gap-x-2 gap-y-0.5">
      <span
        class="min-w-0 text-sm font-medium text-ink-gray-8"
        :class="wide ? 'truncate' : ''"
      >
        {{ data?.address || address || __('This property') }}
      </span>
      <span class="min-w-0 text-xs text-ink-gray-5" :class="wide ? 'truncate' : ''">
        <template v-if="loading">{{ __('Finding comps…') }}</template>
        <template v-else-if="comps.length">
          {{ __('{0} comps', [data?.total_matched ?? comps.length]) }}
          <template v-if="presetLabel"> · {{ presetLabel }}</template>
          <span class="text-ink-gray-4">
            ·
            {{
              __('of {0} within {1} mi', [
                data?.total_in_radius ?? comps.length,
                data?.radius_mi,
              ])
            }}
          </span>
          <template v-if="(data?.total_matched ?? 0) > comps.length">
            · {{ __('showing the {0} nearest', [comps.length]) }}
          </template>
          <template v-if="zillowLine"> · {{ zillowLine }}</template>
              </template>
              <template v-else>
                <span :title="emptyMessage">{{ emptyMessage }}</span>
              </template>
            </span>
          </div>
          <!-- Wraps: at 390px this row holds the underwrite button, two
               checkboxes, Filters, radius and refresh, and without wrapping the
               Filters toggle simply sat off the right edge of the screen -- on
               the one width where it is the only way to reach the filters. -->
          <div class="flex flex-wrap items-center gap-1.5">
            <!-- Underwriting from the comps you picked. Enabled only once something is
     selected, and the label carries the count so "up to 4" is visible
     before the click rather than as an error after it. -->
<Button
  v-if="pageMode && !practiceAttempt && !isRentals && !isScratch"
  :label="underwritingLabel"
  :variant="selectedNames.length ? 'solid' : 'subtle'"
  :disabled="!selectedNames.length || creatingSheet"
  :loading="creatingSheet"
  :title="underwritingTitle"
  iconLeft="grid"
  @click="createUnderwriting"
/>

<!-- "Got a live one" -- the comps screen is where a rep is when the seller
     turns out to be real, so the ping to the closer lives here as well as
     on the lead header. Off in practice: nobody needs a drill flagged. -->
<Button
  v-if="!isPractice && !isScratch"
  :label="__('Live one')"
  variant="subtle"
  :title="__('Got a live one? Send the closer a Mattermost message with a link to these comps')"
  iconLeft="zap"
  @click="showLiveOne = true"
/>
<div v-if="pageMode && !isPractice" class="flex min-w-[12rem] flex-1 items-center gap-1 sm:min-w-[16rem]">
  <input
    v-model="addCompText"
    class="min-w-0 flex-1 rounded-md border border-outline-gray-2 bg-surface-white px-2 py-1 text-xs text-ink-gray-8 outline-none focus:border-outline-gray-4"
    :placeholder="__('Add a property — address or listing URL')"
    :disabled="addingComp"
    @keydown.enter.prevent="addCompFromText"
  />
  <Button
    :label="__('Add as comp')"
    variant="subtle"
    :loading="addingComp"
    :disabled="!addCompText.trim()"
    @click="addCompFromText"
  />
</div>
<Button
  v-if="pageMode && !isPractice"
  :label="__('Tax / liens')"
  :variant="showTaxPanel ? 'subtle' : 'ghost'"
  :title="__('Show tax, deeds, mortgages and liens for this property')"
  iconLeft="dollar-sign"
  @click="showTaxPanel = !showTaxPanel"
/>

<!-- Details toggle: the pills carry beds/baths/sqft/year, but on a
                 dense board the overview is sometimes worth more than the facts.
                 A checkbox rather than a button because it reports its own state
                 — a button reading "Details off" is ambiguous about whether that
                 is the current state or what clicking will do. -->
            <div class="flex items-center rounded-md border border-outline-gray-2 bg-surface-gray-1 p-px text-xs" role="group" :aria-label="__('Map listings')">
              <button
                type="button"
                class="rounded px-2 py-0.5 font-medium"
                :class="isRentals ? 'text-ink-gray-6' : 'bg-surface-white text-ink-gray-9 shadow-sm'"
                @click="setInventory('sale')"
              >
                {{ __('For sale') }}
              </button>
              <button
                type="button"
                class="rounded px-2 py-0.5 font-medium"
                :class="isRentals ? 'bg-surface-white text-ink-gray-9 shadow-sm' : 'text-ink-gray-6'"
                @click="setInventory('rentals')"
              >
                {{ __('Rentals') }}
              </button>
            </div>
            <label
              class="flex cursor-pointer select-none items-center gap-1.5 whitespace-nowrap text-xs text-ink-gray-7"
              :title="__('Show beds/baths/sqft/year on pills') + ' (D)'"
            >
              <FormControl type="checkbox" size="sm" v-model="showDetail" />
              {{ __('Details') }}
            </label>

            <!-- Lot lines are their own layer, not a side-effect of Nearby.
                 Nearby is every home around this one; parcels are where each
                 lot ends. A rep zoomed in to judge a comp should not also have
                 to turn on 1,800 context dots. Same checkbox idiom as Details. -->
            <label
              class="flex cursor-pointer select-none items-center gap-1.5 whitespace-nowrap text-xs text-ink-gray-7"
              :title="__('Show lot lines when zoomed in') + ' (P)'"
            >
              <FormControl type="checkbox" size="sm" v-model="showParcels" />
              {{ __('Parcels') }}
            </label>

            <!-- Street View overlays the map (subject, or the last pin clicked).
                 Same checkbox idiom as Details / Parcels. Off by default — the
                 map is the working surface; this is a look. -->
            <label
              class="flex cursor-pointer select-none items-center gap-1.5 whitespace-nowrap text-xs text-ink-gray-7"
              :title="streetViewTitle"
            >
              <FormControl type="checkbox" size="sm" v-model="showStreet" />
              {{ __('Street') }}
            </label>

            <!-- Hide the tray + calculator so the map takes the page. The
                 two are one toggle on purpose: collapsing only the list still
                 leaves the calc sitting on 358px of map. -->
            <Button
              v-if="pageMode || wide"
              :label="compsFocusMap ? __('Show list') : __('Full map')"
              :variant="compsFocusMap ? 'subtle' : 'ghost'"
              :title="
                (compsFocusMap
                  ? __('Show the property list and offer calculator')
                  : __('Hide the list and calculator so the map fills the page')) +
                ' (F)'
              "
              @click="toggleCompsFocusMap"
            />

            <!-- In `fill` mode ONLY, the filter card folds away behind this.
                 Filters are deliberately always visible on the comps page (they
                 are the point of the tool, and a rep should not have to find a
                 button to widen a beds range) -- but the desk gives this whole
                 component ~726px on the laptop it is designed for, and the card
                 is ~190px of it. Spending a quarter of the working surface on
                 controls the preset ladder has already set is the worse trade
                 there. The count keeps the state visible while it is folded. -->
            <Button
              v-if="filtersCollapsible"
              :label="activeFilterCount ? __('Filters ({0})', [activeFilterCount]) : __('Filters')"
              :variant="filtersOpen ? 'subtle' : 'ghost'"
              iconLeft="filter"
              @click="filtersOpen = !filtersOpen"
            />

            <!-- The neighbourhood: every home around the subject, most of which
                 we know nothing about. OFF by default and loaded only on the
                 first click -- it is context, the comps are the answer, and a
                 dense area is ~1,800 records nobody should pay for on open. -->
            <Button
              v-if="neighborhood"
              :label="hoodLabel"
              :variant="hoodOn ? 'subtle' : 'ghost'"
              :loading="hoodLoading"
              iconLeft="map-pin"
              :title="__('Every home around this one, priced or not (N)')"
              @click="toggleHood()"
            />

            <!-- Radius stays its own control: a rural lead needs a wider net than
                 an infill lot, and the right answer is obvious once you see the
                 map. Loosening the preset ladder never touches it. -->
            <FormControl
              type="select"
              size="sm"
              :options="radiusOptions"
              v-model="radius"
            />
            <Button
              variant="ghost"
              icon="refresh-cw"
              :loading="loading"
              @click="() => load()"
            />
          </div>
        </div>
        <TaxInfoCard
          v-if="pageMode && !isPractice && showTaxPanel"
          embedded
          :lead="lead"
          @fetch="showFetchTax = true"
        />

        <!-- Filters are VISIBLE, not behind a popover: they are the whole point
             of the tool, and a rep should be able to widen a beds range without
             first discovering a button. Wraps to as many rows as it needs, which
             is what keeps it usable at 390px. -->
        <!-- Labels sit INLINE, not stacked above each control. Stacked labels
             plus a caveat sentence made this card ~300px tall, which on a laptop
             left the map 439px — the filters were bigger than the thing they
             filter. Inline, the same eight controls wrap into ~2 short rows and
             the map gets the height back. -->
        <div
          v-show="!filtersCollapsible || filtersOpen"
          class="flex flex-wrap items-center gap-x-2.5 gap-y-1 rounded-lg border border-outline-gray-2 bg-surface-gray-1 px-2 py-1"
        >
          <div v-if="!isRentals" class="flex min-w-0 items-center gap-1">
            <span class="shrink-0 text-xs font-medium text-ink-gray-5">{{ __('Status') }}</span>
            <FormControl
              type="select"
              size="sm"
              :options="statusOptions"
              v-model="draft.status"
            />
          </div>
          <div
            v-if="!isRentals"
            class="flex min-w-0 items-center gap-1"
            :title="
              __(
                '“Sold within” applies to off-market comps only — an active listing stays on the map however long it has been listed.',
              )
            "
          >
            <span class="shrink-0 text-xs font-medium text-ink-gray-5">{{ __('Sold') }}</span>
            <FormControl
              type="select"
              size="sm"
              :options="withinOptions"
              v-model="draft.within_days"
            />
          </div>

          <div v-for="r in rangeRows" :key="r.key" class="flex shrink-0 items-center gap-1">
            <span class="shrink-0 text-xs font-medium text-ink-gray-5">{{ r.label }}</span>
            <input
              class="comps-filter-num"
              :style="{ width: r.px + 'px' }"
              inputmode="numeric"
              :placeholder="__('min')"
              :value="fmtInt(draft[r.key + '_min'])"
              @input="typeFilter(r.key + '_min', $event)"
            />
            <span class="text-ink-gray-4">–</span>
            <input
              class="comps-filter-num"
              :style="{ width: r.px + 'px' }"
              inputmode="numeric"
              :placeholder="__('max')"
              :value="fmtInt(draft[r.key + '_max'])"
              @input="typeFilter(r.key + '_max', $event)"
            />
          </div>

          <div class="flex min-w-0 items-center gap-1">
            <span class="shrink-0 text-xs font-medium text-ink-gray-5">{{ __('Type') }}</span>
            <FormControl
              type="select"
              size="sm"
              :options="typeOptions"
              v-model="draft.property_types"
            />
          </div>

          <div class="flex items-center gap-1">
            <Button
              v-if="activeFilterCount"
              :label="__('Reset to suggested')"
              variant="ghost"
              @click="resetToSuggested"
            />
            <Button :label="__('Clear all')" variant="ghost" @click="clearAll" />
          </div>
        </div>

        <ZillowAddressMatch
          v-if="!hideAddressMatch && lead && (zillowMatch || addressResolution?.suggestion_state === 'pending')"
          :lead="lead"
          :address="data?.address || address"
          :match="zillowMatch"
          :address-resolution="addressResolution"
          @saved="onAddressSaved"
          @reran="onAddressReran"
          @address-resolved="loadAddressStatus"
        />
        <!-- The preset had to loosen, or nothing matched at all. Either way the
             user is told outright rather than left to wonder why a "similar"
             map is full of houses that are nothing like the subject. -->
        <div
          v-if="notice"
          class="flex flex-wrap items-center gap-x-2 gap-y-1 rounded-md border px-3 py-2 text-xs"
          :class="
            notice.tone === 'warning'
              ? 'border-outline-amber-2 bg-surface-amber-1 text-ink-amber-3'
              : 'border-outline-gray-2 bg-surface-gray-1 text-ink-gray-6'
          "
        >
          <span>{{ notice.text }}</span>
          <button
            v-if="notice.action"
            class="font-medium underline underline-offset-2"
            @click="notice.action.run"
          >
            {{ notice.action.label }}
          </button>
        </div>

        <!-- Map left, property tray right: the Zillow arrangement, and it is the
             right one here for the same reason it is there. A map answers
             "where" and a list answers "which", and the two questions are asked
             in the same breath — stacking them meant scrolling away from the map
             to read the list of what is on it. Below `lg` it stacks, because a
             390px phone has no second column to give.

             The tray is the list now; the old table is gone. A table row cannot
             carry a photo, and a photo is the fastest way to know a comp is not
             comparable — square footage says nothing about a gutted shell beside
             a renovated flip. -->
        <!-- MIN-HEIGHT, not just flex-1. `flex-1` alone means "whatever is left",
             and what was left measured 342px on a 919px window because the
             calculator above had taken 358px of it. A map that small is a
             thumbnail. The floor means the map gets a usable height first and the
             page scrolls if the rest genuinely does not fit -- the same trade the
             lead desk already makes, where a scrollbar beats clipping. -->
        <div
          id="comps-map"
          class="flex min-h-0 gap-3"
          :class="[
            wide ? 'flex-row' : 'flex-col',
            wide
              ? fillHeight
                ? 'min-h-[32rem] flex-1'
                : 'h-[34rem]'
              : 'min-h-[16rem] flex-1',
          ]"
        >
          <!-- GOTCHA — the sizing classes live on this WRAPPER, never on the
               element Leaflet initialises. Vue patches `class` by writing the
               whole attribute from its static + bound parts, which silently
               discards the classes Leaflet adds imperatively
               (`leaflet-container`, `leaflet-touch`, ...). While these classes
               only changed with a prop that never moved at runtime that was
               harmless; now that `wide` flips on resize, re-patching the map
               element destroys the map. -->
          <div
            class="relative overflow-hidden rounded-lg border border-outline-gray-2 bg-surface-gray-1"
            :data-compact="wide ? null : '1'"
            :class="wide ? 'h-full min-h-0 flex-1' : 'min-h-0 flex-1'"
          >
            <div ref="mapEl" class="size-full" />
            <!-- Street View chrome lives ABOVE the iframe, never over it.
                 Google's embed already has an address chip, fullscreen,
                 zoom, compass and legal bar — our Close / label used to
                 paint on top of those. A slim bar takes ~28px from the
                 panorama and leaves every native control clickable. -->
            <div
              v-if="showStreet"
              class="absolute inset-0 z-[1100] flex flex-col bg-surface-gray-1"
            >
              <div
                class="flex shrink-0 items-center gap-2 border-b border-outline-gray-2 bg-surface-white px-2 py-1"
              >
                <span class="min-w-0 flex-1 truncate text-xs text-ink-gray-7">
                  {{ streetViewPoint?.label || __('Street View') }}
                </span>
                <span
                  v-if="streetViewMsg"
                  class="shrink-0 text-xs text-ink-gray-5"
                >
                  {{ streetViewMsg }}
                </span>
                <button
                  type="button"
                  class="shrink-0 rounded bg-surface-gray-2 px-2 py-0.5 text-xs font-medium text-ink-gray-8 hover:bg-surface-gray-3"
                  @click="showStreet = false"
                >
                  {{ __('Close Street') }}
                </button>
              </div>
              <iframe
                v-if="streetViewSrc"
                :src="streetViewSrc"
                class="min-h-0 w-full flex-1 border-0"
                referrerpolicy="origin"
                allowfullscreen
                loading="eager"
                title="Street View"
                @load="onStreetViewLoad"
              />
              <div
                v-else-if="streetViewMsg"
                class="flex min-h-0 flex-1 items-center justify-center px-4 text-center text-sm text-ink-gray-6"
              >
                {{ streetViewMsg }}
              </div>
            </div>
          </div>

          <!-- Sized in px, not rem: this app's root font-size is 20px, so a
               `21rem` rail reads as 420px and takes more of the split than the
               map it is meant to accompany.

               Its HEIGHT mirrors the map's exactly. In a flex-row both panes
               stretch to the taller one, so an `h-auto` tray beside a fixed-height
               map would grow the row to the full comp list -- which is what the
               Today modal (which passes neither `fill` nor `page-mode`) would
               otherwise do. -->
          <!-- No tray on a phone: a 28rem list under the map scrolls the map
               away, and tapping a pin already opens the photo/detail modal —
               that is the inspect surface. Desktop keeps the rail. -->
          <aside
            v-if="wide && !compsFocusMap && (comps.length || discarded.length)"
            class="flex h-full min-h-0 w-[330px] shrink-0 flex-col overflow-hidden rounded-lg border border-outline-gray-2"
          >
            <div
              class="flex shrink-0 items-center justify-between gap-2 border-b border-outline-gray-2 bg-surface-gray-1 px-3 py-2"
            >
              <span class="text-sm font-medium text-ink-gray-8">
                {{ __('{0} properties', [comps.length]) }}
              </span>
              <div class="flex items-center gap-1">
                <span class="text-xs text-ink-gray-5">{{ __('Nearest first') }}</span>
                <button
                  type="button"
                  class="rounded p-0.5 text-ink-gray-5 hover:bg-surface-gray-2 hover:text-ink-gray-8"
                  :title="__('Hide the list and calculator so the map fills the page') + ' (F)'"
                  @click="toggleCompsFocusMap"
                >
                  <FeatherIcon name="chevron-right" class="size-4" />
                </button>
              </div>
            </div>

            <!-- Marked so each card can find the box it scrolls inside and use
                 it as its IntersectionObserver root. Rooting on the viewport
                 instead technically works but cannot see past this element's own
                 clip, so a card is only fetched as it appears rather than just
                 before -- and a fast scroll shows grey boxes catching up. -->
            <div data-comp-tray class="min-h-0 flex-1 overflow-auto">
              <CompSubjectCard
                v-if="data?.subject"
                ref="subjectCardEl"
                :subject="data.subject"
                :address="data?.address || address"
                :can-edit-sqft="!isPractice"
                @open="openSubjectDetail"
                @street="openStreetView(null)"
                @save-sqft="saveSubjectSqft"
              />

              <CompTrayCard
                v-for="c in comps"
                :key="c.name"
                :comp="c"
                :lead="lead"
                :subject="data?.subject || null"
                :active="hoveredComp === c.name"
                :can-tag="canTagTypes"
                :ref="(el) => setCardRef(c.name, el)"
                @hover="hoverFromCard"
                @open="openCompDetail"
                @use="toggleUse"
                @discard="setCompState($event, 'hidden')"
                @street="openStreetView"
                @set-type="setCompType"
              />

              <!-- Discards live at the BOTTOM of the same tray, not behind a
                   toggle elsewhere: they are still part of what you looked at,
                   and the undo has to be where the eye already is. -->
              <template v-if="discarded.length">
                <button
                  class="sticky bottom-0 flex w-full items-center justify-between border-y border-outline-gray-2 bg-surface-gray-2 px-3 py-1.5 text-xs font-medium text-ink-gray-7"
                  @click="showDiscarded = !showDiscarded"
                >
                  <span>{{ __('{0} discarded', [discarded.length]) }}</span>
                  <FeatherIcon
                    :name="showDiscarded ? 'chevron-down' : 'chevron-up'"
                    class="size-4"
                  />
                </button>
                <CompTrayCard
                  v-for="c in showDiscarded ? discarded : []"
                  :key="'d-' + c.name"
                  :comp="c"
                  discarded
                  @undiscard="setCompState($event, 'none')"
                />
              </template>
            </div>
          </aside>
        </div>


<!-- Legend: the map is unreadable without saying what the fade means. -->
        <div class="flex flex-wrap items-center gap-3 text-xs text-ink-gray-6">
          <span class="flex items-center gap-1.5">
            <span class="size-2.5 rounded-full" :style="{ background: SUBJECT }" />
            {{ __('This property') }}
          </span>
          <template v-if="isRentals">
            <span class="flex items-center gap-1.5">
              <span class="size-2.5 rounded-full" :style="{ background: COMP_COLORS.rent.bg }" />
              {{ __('For rent') }}
            </span>
          </template>
          <template v-else>
          <span class="flex items-center gap-1.5">
            <span
              class="size-2.5 rounded-full ring-1 ring-inset"
              :style="{ background: OFF_MARKET, '--tw-ring-color': COMP_COLORS.sold.border }"
            />
            {{ __('Sold') }}
          </span>
          <span class="flex items-center gap-1.5">
            <span
              class="size-2.5 rounded-full ring-1 ring-inset"
              :style="{ background: COMP_COLORS.off_market.bg, '--tw-ring-color': COMP_COLORS.off_market.border }"
            />
            {{ __('Listing removed / off-market') }}
          </span>
          <span class="flex items-center gap-1.5">
            <span class="size-2.5 rounded-full" :style="{ background: ACTIVE }" />
            {{ __('Still listed') }}
          </span>
          <!-- Only when the board actually holds one. A legend entry for a state
               nothing on the map is in is one more thing to read for nothing. -->
          <span v-if="pendingCount" class="flex items-center gap-1.5">
            <span class="size-2.5 rounded-full" :style="{ background: PENDING }" />
            {{ __('Pending ({0})', [pendingCount]) }}
          </span>
          <span v-if="auctionCount" class="flex items-center gap-1.5">
            <span class="size-2.5 rounded-full" :style="{ background: COMP_COLORS.auction.bg }" />
            {{ __('Auction ({0})', [auctionCount]) }}
          </span>
          <span class="text-ink-gray-5">{{ __('Fainter = older sale / removal') }}</span>
          </template>
          <span
            v-if="hasFlipOnBoard"
            class="flex items-center gap-1.5"
            :title="__('Possible flip — look before you price off it')"
          >
            <span
              class="inline-block size-2.5 rounded-[3px]"
              :style="{
                border: '2px dashed ' + COMP_COLORS.sold.ink,
                background: OFF_MARKET,
              }"
            />
            {{ __('Dashed = possible flip') }}
          </span>
          <span
            v-for="t in typeKindsOnBoard"
            :key="t.kind"
            class="flex items-center gap-1"
          >
            <span
              class="inline-flex size-3 text-ink-gray-7"
              aria-hidden="true"
              v-html="t.svg"
            />
            {{ t.label }}
          </span>
          <CompHelpKey />
          <span v-if="data?.selected_count" class="flex items-center gap-1.5">
            <span
              class="size-2.5 rounded-full ring-2 ring-offset-1"
              :style="{ background: OFF_MARKET, '--tw-ring-color': SUBJECT }"
            />
            {{ __('{0} used as comps', [data.selected_count]) }}
          </span>
          <span class="text-ink-gray-4">
            {{
              wide
                ? __('Click a pin to use or hide it')
                : __('Tap a pin for photos and details')
            }}
            <template v-if="wide">
              ·
              <b>D</b> {{ __('details') }} · <b>P</b> {{ __('parcels') }} ·
              <b>S</b> {{ __('street') }} · <b>F</b> {{ __('full map') }} ·
              <b>U</b> {{ __('use') }} · <b>H</b> {{ __('hide') }}
            </template>
          </span>
        </div>
  </div>

  <!-- Photos + Zillow facts for one comp. Mounted here rather than in each host
       so the map, the list and the Today modal all reach the same gallery. -->
  <LiveOneModal
    v-if="!isPractice && !isScratch"
    v-model="showLiveOne"
    :lead="lead"
    :leadName="address || data?.subject?.address || lead"
  />
  <FetchTaxInfoModal
    v-if="!isPractice"
    v-model="showFetchTax"
    :reference-doc="taxReferenceDoc"
  />
  <CompDetailModal
    v-if="detailComp"
    v-model="showCompDetail"
    :lead="lead"
    :comp="detailComp"
    :subject="data?.subject || null"
    :subject-mode="subjectDetail"
    :can-tag="canTagTypes"
    :can-edit-sqft="!isPractice"
    :can-share="!isPractice"
    @use="toggleUse"
    @street="openStreetView(subjectDetail ? null : detailComp?.name)"
    @set-type="setCompType"
    @save-sqft="saveSubjectSqft"
  />

  <!-- Asked on every pick; stacks above the gallery when the pick came from
       there, so the rep answers without losing the photos. -->
  <CompConditionModal
    v-model="showConditionModal"
    :comp="conditionComp"
    @choose="setCompType"
  />
</template>

<script setup>
/**
 * A lead's comparable sales on a map.
 *
 * Ported from the LeadMarket comps view, with the one change that matters: that
 * app can only draw an ESTIMATED subject location (iSpeedToLead hides the address
 * until you buy the lead), so it plots the centroid of the comp cloud. We own
 * these leads, so this centers on the REAL geocoded parcel and the comps arrange
 * themselves around it.
 *
 * The fade is the point. A sale from last month tells you far more about today's
 * value than one from last year, so opacity carries recency and the eye lands on
 * the comps that actually count without reading a single date.
 *
 * Filters arrive PRE-SET around this property (recent + similar), because an
 * unfiltered two-mile dump is not a comp set. The server picks the tightest tier
 * that still yields a usable number and tells us whether it had to loosen; when it
 * did, we say so instead of quietly showing houses that are nothing like this one.
 * Touching any control switches to explicit mode — from then on the server runs
 * exactly what is on screen, even if that matches nothing.
 */
import { Button, FeatherIcon, FormControl, call, toast } from 'frappe-ui'
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import L from 'leaflet'
import 'leaflet/dist/leaflet.css'
import { zillowUrl } from '@/utils/propertyLinks'
import {
  COMP_COLORS,
  COMP_QUERY_SUBJECT,
  PROPERTY_TYPE_KIND_ORDER,
  PROPERTY_TYPE_KINDS,
  compColor,
  compsDetailUrl,
  compState,
  daysToSell,
  finiteDays,
  isAuction,
  isPending,
  isRentalComp,
  isScratchSubject,
  propertyTypeGlyphHtml,
  propertyTypeGlyphSvg,
  propertyTypeKind,
} from '@/utils/comps'
import { copyToClipboard } from '@/utils'
import CompDetailModal from '@/components/CompDetailModal.vue'
import CompConditionModal from '@/components/Modals/CompConditionModal.vue'
import LiveOneModal from '@/components/Modals/LiveOneModal.vue'
import FetchTaxInfoModal from '@/components/Modals/FetchTaxInfoModal.vue'
import TaxInfoCard from '@/components/TaxInfoCard.vue'
import CompTrayCard from '@/components/CompTrayCard.vue'
import CompSubjectCard from '@/components/CompSubjectCard.vue'
import CompHelpKey from '@/components/CompHelpKey.vue'
import CompOfferCalc from '@/components/CompOfferCalc.vue'
import ZillowAddressMatch from '@/components/ZillowAddressMatch.vue'
import FilterIcon from '@/components/Icons/FilterIcon.vue'
import { useKeyboardShortcuts } from '@/composables/useKeyboardShortcuts'
import {
  compsFocusMap,
  compsViewCount,
  toggleCompsFocusMap,
} from '@/composables/compsLayout'
import { bearingDeg, streetViewEmbedUrl } from '@/utils/streetView'

const props = defineProps({
  lead: { type: String, required: true },
  address: { type: String, default: '' },
  // Only the full page offers underwriting; it needs the room, and the action
  // belongs where the comps are actually chosen.
  pageMode: { type: Boolean, default: false },
  // Size to the container rather than to the content (the lead desk), folding
  // the filter card behind a toggle and letting map + list share the height.
  fill: { type: Boolean, default: undefined },
  // Offer the neighbourhood layer (groundwork-geo). Opt-in for the same reason
  // `fill` is: the comps page is a comps page.
  neighborhood: { type: Boolean, default: false },
  // Today already mounts the same banner above the panes, so the map does not
  // repeat it. The comps page leaves this off.
  hideAddressMatch: { type: Boolean, default: false },
  // Practice run: same map, but hides/picks/offers write to the attempt, not
  // the real lead. Underwriting is off — that creates a real Drive sheet.
  practiceAttempt: { type: String, default: '' },
  practiceProperty: { type: String, default: '' },
  sellerNote: { type: String, default: '' },
  preferKind: { type: String, default: '' },
})
// This used to be a modal driven by `defineModel()`. It is now a full page, so
// "open" is simply always true -- which keeps every existing `show.value` guard,
// watcher and keyboard-shortcut gate working exactly as before.
const emit = defineEmits(['subject', 'picked', 'zillowMatch'])
const route = useRoute()
const router = useRouter()
const show = ref(true)
const showLiveOne = ref(false)
const showFetchTax = ref(false)
const showTaxPanel = ref(false)
const taxReferenceDoc = computed(() => ({
  name: props.lead,
  property_address: props.address || data.value?.address || '',
}))

// Only consulted in `fill` mode. Starts closed: the preset ladder has already
// chosen a sensible filter set by the time anyone looks, and the desk exists to
// remove decisions from a live call rather than present them.
const filtersOpen = ref(false)

// --- neighbourhood layer -------------------------------------------------
// Drawn on a CANVAS renderer, not as markers. A warmed two-mile radius is
// ~1,800 points here and 17,287 in Indianapolis; one DOM node each is the same
// mistake the kanban made with per-field components, and it would land on the
// map a rep is dragging mid-call.
const hoodOn = ref(false)
const hoodLoading = ref(false)
const hood = ref(null)
let hoodLayer = null
let hoodRenderer = null
let hoodZoomHandler = null

// --- lot lines ------------------------------------------------------------
// Own toggle, not tied to Nearby. Nearby is "every home around this one";
// lot lines are "where does this lot end". They answer different questions,
// and a second switch is the point — Lance asked for one.
//
// PARCEL_ZOOM is where a city lot stops being a smudge. At z15 a 12m frontage
// is ~5px; at 16 it is ~10px and the shape starts to mean something. Below it
// nothing is fetched at all -- a request whose result cannot be read is just
// latency and load on a service that is scraping for it.
//
// OFF by default and loaded only when flipped on -- same rule as Nearby. A
// dense viewport is hundreds of polygons nobody should pay for on open.
// Persisted so turning it on once is enough for the next lead.
const PARCEL_ZOOM = 16
const showParcels = ref(localStorage.getItem('compsShowParcels') === '1')
watch(showParcels, (v) => {
  localStorage.setItem('compsShowParcels', v ? '1' : '0')
  if (v) bindParcels()
  else unbindParcels()
})

// Street View is a look at one house, not the working surface. Off by default
// and remembered like Parcels. The iframe is created only while this is on —
// a cold rural panorama can take 20–30s, so the overlay says Loading, never
// "could not load", until the load event or a 30s timer.
const showStreet = ref(localStorage.getItem('compsShowStreet') === '1')
watch(showStreet, (v) => localStorage.setItem('compsShowStreet', v ? '1' : '0'))
const streetViewSrc = ref('')
const streetViewMsg = ref('')
const subjectStreetView = ref(null)
const subjectStreetViewLoading = ref(false)
const subjectStreetViewAttempted = ref(false)
let streetViewTimer = null
let parcelLayer = null
let parcelMoveHandler = null
let parcelTimer = null
let parcelKey = ''

const hoodLabel = computed(() => {
  if (!hoodOn.value) return __('Nearby')
  const d = hood.value
  if (!d?.features) return __('Nearby')
  // Counted from the points we can actually DRAW, not from the payload length.
  // Same rule as `truncated` below: the label describes what is on screen, and a
  // number that survives a shape change the renderer cannot handle is how an
  // empty map ends up claiming 5,000 homes.
  const drawn = hoodPoints(d).length
  return d.truncated && d.in_view
    ? __('Nearby ({0} of {1})', [drawn, d.in_view])
    : __('Nearby ({0})', [drawn])
})

function hoodMoney(n) {
  const v = Number(n) || 0
  return v ? `$${Math.round(v).toLocaleString('en-US')}` : ''
}

/**
 * Normalise a neighbourhood payload into points, WHICHEVER SHAPE IT ARRIVES IN.
 *
 * `get_neighborhood` used to pass the geo service's raw GeoJSON straight through
 * (`{geometry:{coordinates:[lng,lat]}, properties:{...}}`) and now returns flat
 * trimmed rows (`{lat, lng, price, ...}`). Both exist in the wild at once: the
 * frontend and the backend deploy separately, so for the length of any deploy
 * window the browser is talking to the OTHER version.
 *
 * That is not hypothetical. Verified against production mid-work: 5,000 GeoJSON
 * features arrived, every one was skipped for having no `lat`, and the layer
 * drew an EMPTY CANVAS while the button cheerfully read "Nearby (5000)" -- the
 * worst kind of failure, one that reports success.
 */
function hoodPoints(payload) {
  const out = []
  for (const f of payload?.features || []) {
    if (f == null) continue
    if (f.lat != null && f.lng != null) {
      out.push(f)
      continue
    }
    const coords = f.geometry?.coordinates
    if (Array.isArray(coords) && coords.length >= 2) {
      out.push({ ...(f.properties || {}), lng: coords[0], lat: coords[1] })
    }
  }
  return out
}

/**
 * Draw (or redraw) the neighbourhood.
 *
 * Two states, and the difference is the whole point: a home we have a price for
 * is filled, one we do not is a hollow ring. Most of a neighbourhood is the
 * second kind -- 41% priced in the Indianapolis measurement -- and that IS the
 * off-market universe, so it must not be drawn as if it were missing data.
 *
 * These are deliberately NOT pills. Pill grammar means "comp" everywhere else on
 * this map, and a rep glancing down must never price off a dot that is only
 * context.
 */
/**
 * Dot size follows the zoom, and that is not cosmetic.
 *
 * The desk opens at whatever zoom fits the COMPS -- zoom 12 on the Chicago test
 * lead -- and at 28m/px the warmed neighbourhood (1.3km x 1.6km) lands in a
 * 50x50px area. Verified: all 1,500 markers were drawn correctly and the result
 * was an indistinct smudge. Fixed dots answer "is there anything here"; dots
 * that grow as you zoom answer "what is on this street", which is the question
 * a rep on a call actually has.
 */
function hoodRadius() {
  const z = map?.getZoom() ?? 14
  if (z <= 12) return 1.5
  if (z <= 14) return 3
  if (z <= 16) return 5
  return 7
}

function paintHood() {
  if (!map) return
  if (hoodLayer) {
    map.removeLayer(hoodLayer)
    hoodLayer = null
  }
  if (hoodRenderer) {
    map.removeLayer(hoodRenderer)
    hoodRenderer = null
  }
  if (hoodZoomHandler) {
    map.off('zoomend', hoodZoomHandler)
    hoodZoomHandler = null
  }
  if (!hoodOn.value || !hoodPoints(hood.value).length) return

  // ORDER MATTERS. The renderer joins the map FIRST and the group is on the map
  // BEFORE any circle goes into it: a circleMarker added to a detached group
  // never gets a live renderer, so it is only drawn if something later happens
  // to redraw it. Measured with the group added last: 2,416 painted pixels --
  // about 85 of 1,500 dots -- on a map that looked plausibly empty rather than
  // obviously broken.
  const renderer = L.canvas({ padding: 0.3 }).addTo(map)
  const layer = L.layerGroup().addTo(map)
  for (const p of hoodPoints(hood.value)) {
    if (p.lat == null || p.lng == null) continue
    const priced = !!Number(p.price)
    L.circleMarker([p.lat, p.lng], {
      renderer,
      radius: hoodRadius(),
      weight: 1,
      color: '#64748b',
      opacity: priced ? 0.85 : 0.55,
      fillColor: '#94a3b8',
      fillOpacity: priced ? 0.85 : 0,
    })
      .bindPopup(
        `<div style="font-size:12px">` +
          `<div style="font-weight:600">${escapeHtml(p.address || __('Nearby home'))}</div>` +
          (priced ? `<div>${hoodMoney(p.price)}</div>` : `<div style="color:#64748b">${__('no price on record')}</div>`) +
          `<div style="color:#64748b">${[p.beds && `${p.beds} bd`, p.baths && `${p.baths} ba`, p.sqft && `${Number(p.sqft).toLocaleString('en-US')} sf`, p.year_built]
            .filter(Boolean)
            .join(' · ')}</div>` +
          `<div style="color:#94a3b8;margin-top:2px">${__('Context, not a comp')}</div>` +
          `</div>`,
        { maxWidth: 220 },
      )
      .addTo(layer)
  }
  // The comp pills are markers (pane z-600) and this is an overlay (z-400), so
  // the answer already sits above the context without any per-layer reordering.
  hoodLayer = layer
  hoodRenderer = renderer

  // Resize the dots as the rep zooms. Registered once per painted layer and
  // removed with it, so toggling the layer off leaves no handler behind.
  const onZoom = () => layer.eachLayer((l) => l.setRadius?.(hoodRadius()))
  map.on('zoomend', onZoom)
  hoodZoomHandler = onZoom
}

function unbindParcels() {
  if (parcelTimer) {
    clearTimeout(parcelTimer)
    parcelTimer = null
  }
  if (parcelMoveHandler && map) {
    map.off('moveend zoomend', parcelMoveHandler)
  }
  parcelMoveHandler = null
  if (parcelLayer && map) map.removeLayer(parcelLayer)
  parcelLayer = null
  parcelKey = ''
}

function bindParcels() {
  if (!map || !showParcels.value) return unbindParcels()
  if (!parcelMoveHandler) {
    parcelMoveHandler = () => scheduleParcels()
    map.on('moveend zoomend', parcelMoveHandler)
  }
  scheduleParcels()
}

/**
 * Fetch lot lines for what is on screen, once the rep is zoomed in enough.
 *
 * Debounced, because `moveend` fires on every pan and each call reaches a
 * PostGIS query behind an HTTP hop. Keyed on the rounded viewport so panning a
 * few pixels and coming back does not re-fetch what is already drawn -- the
 * cheapest request is the one not made.
 */
function scheduleParcels() {
  if (!map || !showParcels.value) return unbindParcels()
  if (map.getZoom() < PARCEL_ZOOM) {
    // Deliberately silent: at this zoom a lot line is a smudge, and drawing one
    // would suggest a precision the rep cannot see.
    if (parcelLayer) {
      map.removeLayer(parcelLayer)
      parcelLayer = null
      parcelKey = ''
    }
    return
  }
  if (parcelTimer) clearTimeout(parcelTimer)
  parcelTimer = setTimeout(loadParcels, 400)
}

async function loadParcels() {
  if (!map || !showParcels.value || map.getZoom() < PARCEL_ZOOM) return
  const b = map.getBounds()
  const bbox = [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()]
    .map((v) => v.toFixed(4))
    .join(',')
  if (bbox === parcelKey) return
  parcelKey = bbox
  try {
    const res = await call('crm.api.geo.get_parcels', { lead: props.lead, bbox })
    // The toggle or a map rebuild can land while this is in flight.
    if (!map || !showParcels.value) return
    if (!res?.ok || !res.features?.length) {
      // An empty answer means "not enriched here yet", not an error -- the
      // service says so explicitly and the map simply shows no lot lines.
      if (parcelLayer) map.removeLayer(parcelLayer)
      parcelLayer = null
      return
    }
    if (parcelLayer) map.removeLayer(parcelLayer)
    parcelLayer = L.geoJSON(
      { type: 'FeatureCollection', features: res.features },
      {
        // Thin, unfilled, and grey: a lot line is a boundary, not an object.
        // Filling it would compete with the comp pills for the eye on the one
        // screen where the pills are the answer.
        style: { color: '#475569', weight: 1, opacity: 0.55, fill: false },
        onEachFeature: (f, layer) => {
          const p = f.properties || {}
          layer.bindPopup(
            `<div style="font-size:12px">` +
              `<div style="font-weight:600">${escapeHtml(p.address || __('Parcel'))}</div>` +
              (p.apn ? `<div style="color:#64748b">APN ${escapeHtml(p.apn)}</div>` : '') +
              `<div style="color:#94a3b8;margin-top:2px">${__('Lot line · context, not a comp')}</div>` +
              `</div>`,
            { maxWidth: 220 },
          )
        },
      },
    ).addTo(map)
    parcelLayer.bringToBack()
  } catch (e) {
    // Never toast: lot lines are the least important thing on this screen and a
    // rep mid-call does not need to be told a background layer is unavailable.
    console.error(e)
  }
}

async function loadHood() {
  if (!props.lead) return
  hoodLoading.value = true
  try {
    const bounds = map?.getBounds()
    const res = await call('crm.api.geo.get_neighborhood', {
      lead: props.lead,
      ...(bounds && {
        bbox: [
          bounds.getWest(), bounds.getSouth(), bounds.getEast(), bounds.getNorth(),
        ].join(','),
      }),
    })
    hood.value = res || null
    if (res && !res.ok) {
      // Say why rather than showing an empty map: "not configured" and "this
      // lead has no coordinates" are different problems with different fixes.
      toast.error(res.reason || __('Nearby homes are unavailable'))
      hoodOn.value = false
    }
    paintHood()
  } catch (e) {
    console.error(e)
    toast.error(__('Nearby homes are unavailable'))
    hoodOn.value = false
  } finally {
    hoodLoading.value = false
  }
}

function toggleHood(force) {
  hoodOn.value = typeof force === 'boolean' ? force : !hoodOn.value
  if (!hoodOn.value) return paintHood()
  if (hoodPoints(hood.value).length) return paintHood()
  loadHood()
}

// Canvas/marker colours live in JS because Leaflet can't read Tailwind tokens.
// For sale RED, sold YELLOW, off-market SLATE, subject BLUE. The
// palette lives in utils/comps.js because the pills (hand-built HTML), the tray
// cards (Tailwind) and the legend all have to agree.
const ACTIVE = COMP_COLORS.active.bg // still listed = an ASK, not a sale
const OFF_MARKET = COMP_COLORS.sold.bg // example sold fill for flip/selection legend
const SUBJECT = COMP_COLORS.subject.bg
const PENDING = COMP_COLORS.pending.bg // spoken for = an AGREED price, still live

const mapEl = ref(null)
const data = ref(null)
const loading = ref(false)
const inventory = ref('sale')
const isRentals = computed(() => inventory.value === 'rentals')
// Half a mile first: that is the market that actually prices this house.
// load() walks 0.5 → 1 → 2 → 5 if the tight circle is empty, so a rural
// lead still gets a set without opening at a two-mile dump of irrelevants.
const radius = ref(0.5)
const RADIUS_STEPS = [0.5, 1, 2, 5]
const MIN_FOR_RADIUS = 5
let wideningRadius = false
let map = null
let lastFitKey = ''
let sizeObserver = null

const radiusOptions = [
  { label: '½ mile', value: 0.5 },
  { label: '1 mile', value: 1 },
  { label: '2 miles', value: 2 },
  { label: '5 miles', value: 5 },
]

const statusOptions = [
  { label: __('Sold & listed'), value: 'all' },
  { label: __('Sold / off-market'), value: 'sold' },
  { label: __('Still listed'), value: 'active' },
]

// GOTCHA — the "any" options MUST NOT use an empty-string value. frappe-ui's
// Select wraps reka-ui, which reserves '' for the placeholder and silently drops
// any item declared with it: the "Any time" row simply never rendered, leaving no
// way to lift the recency filter from the dropdown at all. A sentinel string is
// the fix; `currentFilters` maps it back to "unconstrained".
const ANY = 'any'

// Labelled "Sold within" because it no longer means the same thing for both kinds
// of pin: an active listing is exempt, however long it has been sitting there.
const withinOptions = [
  { label: __('Any time'), value: ANY },
  { label: __('Last 90 days'), value: 90 },
  { label: __('Last 6 months'), value: 180 },
  { label: __('Last 12 months'), value: 365 },
  { label: __('Last 2 years'), value: 730 },
]

// Every property_type present in the inventory (measured across all 49,769 rows).
const typeOptions = [
  { label: __('Any type'), value: ANY },
  { label: 'Single Family', value: 'Single Family' },
  { label: 'Townhouse', value: 'Townhouse' },
  { label: 'Condo', value: 'Condo' },
  { label: 'Multi-Family', value: 'Multi-Family' },
  { label: 'Manufactured', value: 'Manufactured' },
  { label: 'Land', value: 'Land' },
  { label: 'Apartment', value: 'Apartment' },
]

const rangeRows = [
  { key: 'beds', label: __('Beds'), step: 1, width: 'w-[3.6rem]', px: 58 },
  { key: 'baths', label: __('Baths'), step: 0.5, width: 'w-[3.6rem]', px: 58 },
  { key: 'sqft', label: __('Sq ft'), step: 50, width: 'w-[4.6rem]', px: 74 },
  { key: 'year', label: __('Year'), step: 1, width: 'w-[4.6rem]', px: 74 },
  { key: 'price', label: __('Price'), step: 1000, width: 'w-[5.6rem]', px: 90 },
]

const RANGE_KEYS = rangeRows.flatMap((r) => [`${r.key}_min`, `${r.key}_max`])

/** A control carries a real constraint (blank and the ANY sentinel do not). */
function isSet(v) {
  return v !== '' && v != null && v !== ANY
}

/** Mirrors the server's filter shape 1:1, so what is on screen is what ran. */
const draft = reactive({ status: 'all', within_days: ANY, property_types: ANY })
for (const k of RANGE_KEYS) draft[k] = ''

// `userTouched` is the whole difference between "suggest something sensible" and
// "do exactly what I said". Once the user drives a control we stop re-deriving
// presets, including on a radius change — silently rewriting someone's deliberate
// filter is how a tool stops being trusted.
const userTouched = ref(false)
let syncing = false
let applyTimer = null

// Calculator open-state is TWO prefs: desktop defaults open, a phone defaults
// closed so the map is the page. Sharing one key meant folding it on a phone
// closed it on the laptop, and opening it on the laptop made the phone land
// on a form with a thumbnail map — which is what "the comps page is nothing
// like Today" was.
const calcOpenDesktop = ref(localStorage.getItem('compsCalcOpen') !== '0')
const calcOpenNarrow = ref(localStorage.getItem('compsCalcOpenNarrow') === '1')
const calcOpen = computed({
  get: () => (wide.value ? calcOpenDesktop.value : calcOpenNarrow.value),
  set: (v) => {
    if (wide.value) {
      calcOpenDesktop.value = v
      localStorage.setItem('compsCalcOpen', v ? '1' : '0')
    } else {
      calcOpenNarrow.value = v
      localStorage.setItem('compsCalcOpenNarrow', v ? '1' : '0')
    }
    nextTick(() => map && map.invalidateSize())
  },
})

watch(compsFocusMap, () => nextTick(() => map && map.invalidateSize()))

// Whether pills carry beds/baths/sqft/year, or collapse to the bare price.
// Persisted per user like dispoView / activityScope — it is a view preference,
// and having to re-set it on every lead would make the shortcut pointless.
const showDetail = ref(localStorage.getItem('compsPillDetail') !== '0')
watch(showDetail, (v) => {
  localStorage.setItem('compsPillDetail', v ? '1' : '0')
  render()
})

// Which comp's popup is open — the target for the h / u shortcuts.
const focusedComp = ref(null)

// The comp whose photos/facts are open, if any.
const detailComp = ref(null)
const showCompDetail = ref(false)
// True when that "comp" is actually the subject, which reads its photos from a
// different endpoint (it is not a CRM Comp row and has no comp name to look up).
const subjectDetail = ref(false)

// The row/pin the pointer is over, in EITHER direction. Kept as a plain name so
// the map and the table are pointing at one shared idea of "this one".
const hoveredComp = ref(null)
const markersByName = new Map()

/**
 * Emphasise one pin without re-rendering the map.
 *
 * Deliberately a class toggle on the existing element rather than swapping the
 * icon: rebuilding a divIcon on every mouseenter would thrash 200 markers and
 * drop the popup that may be open.
 */
/**
 * Where a hovered pill sits in the stack, and where it stays afterwards.
 *
 * This is a Leaflet z-index OFFSET, not an inline `style.zIndex`. Leaflet
 * computes each marker's real z from its latitude PLUS its offset and rewrites
 * the inline style on every pan and zoom -- so the old `el.style.zIndex = 900`
 * was both too low to clear the subject and erased the moment the map moved.
 * Going through `setZIndexOffset` is the only version Leaflet respects.
 *
 * Hover clears the subject. After the pointer leaves, the pill stays above its
 * neighbouring COMPS (so a dense cluster is not whack-a-mole) but drops back
 * UNDER the subject -- overlapping the blue pill and leaving it buried was the
 * remaining complaint.
 */
// The bands are far apart on purpose: Leaflet ADDS the marker's pixel y to the
// offset, and the map can be ~1,000px tall, so bands any closer together would
// let a pin near the bottom of the map outrank a raised pin near the top.
// Normal pins land under ~1,700. Raised max offset 8000 + ~1,000px y stays under
// the subject; hover clears it.
const HOVER_Z = 12000
const SUBJECT_Z = 10000
// One band below the subject. A pill you have looked at STAYS on top of the
// comps it overlaps after the pointer leaves, because the reason you hovered it
// was to bring it out from under them -- dropping it straight back under is the
// behaviour that makes a dense cluster feel like whack-a-mole. It yields to the
// subject, and to whatever is hovered next -- see `markRaised`.
const RAISED_Z = 5000
//: How much room the raised band has to order itself in. RAISED_Z + rank + the
//: marker's pixel y (~1,000 max) has to stay clear of HOVER_Z, so 3,000 leaves
//: headroom on both sides.
const RAISED_SPAN = 3000
//: name -> how recently it was hovered. A SET was not enough: every raised pill
//: shared one flat RAISED_Z, so as soon as two overlapping pills had both been
//: hovered they tied, and the tie was settled by latitude -- whichever sat lower
//: on the map won, regardless of which one you had just looked at. That is the
//: whack-a-mole the raised band exists to prevent, reappearing the moment you
//: use it on more than one pin. Insertion order is kept equal to recency (the
//: hover handler deletes before re-setting) so the ranks can be renumbered.
const raisedComps = new Map()
let raiseSeq = 0

/** Rank the most recently hovered pill highest, without letting z run away. */
function markRaised(name) {
  // delete-then-set so Map iteration order stays oldest -> newest.
  raisedComps.delete(name)
  raisedComps.set(name, ++raiseSeq)
  if (raiseSeq < RAISED_SPAN) return
  // Renumber 1..N in place rather than clamping: clamping would silently stop
  // ordering after 3,000 hovers, and a flat tie is the exact bug this replaced.
  raiseSeq = 0
  for (const key of raisedComps.keys()) raisedComps.set(key, ++raiseSeq)
}

function restZ(name) {
  // Its natural layer, unless it has been hovered at least once this session --
  // and among those, the one looked at most recently sits highest.
  const rank = raisedComps.get(name)
  if (rank !== undefined) return RAISED_Z + rank
  const c = comps.value.find((x) => x.name === name)
  return c ? pinZ(c) : 0
}

watch(hoveredComp, (name, prev) => {
  const off = markersByName.get(prev)
  if (off) {
    off.getElement()?.classList.remove('comps-pill-hot')
    off.setZIndexOffset(restZ(prev))
  }
  const on = markersByName.get(name)
  if (on) {
    on.getElement()?.classList.add('comps-pill-hot')
    markRaised(name)
    on.setZIndexOffset(HOVER_Z)
  }
  // Bring the matching card into the tray's viewport. Only when the map is what
  // moved -- scrolling the list under the pointer while someone is reading it
  // would fight the user for control of their own scroll position.
  if (name && hoverSource === 'map') scrollCardIntoView(name)
})

// Which surface the current hover came from. The pin and the card both write
// `hoveredComp`, and only one of the two directions should scroll.
let hoverSource = 'card'
function hoverFromMap(name) {
  hoverSource = 'map'
  hoveredComp.value = name
}
function hoverFromCard(name) {
  hoverSource = 'card'
  hoveredComp.value = name
}

/**
 * A component ref -> its first real ELEMENT.
 *
 * GOTCHA -- `$el` is not always an element. A template that opens with a COMMENT
 * compiles to a FRAGMENT, and `$el` is then the leading comment node, which has
 * no `scrollIntoView`. Vue strips template comments in production builds but
 * KEEPS them in dev, so a component like `CompSubjectCard` (whose template opens
 * with an explanatory comment) works in prod and silently does nothing on the dev
 * server -- which reads as "this used to work and now it doesn't". Verified:
 * the prod chunk has 0 comment vnodes, the dev module has 10.
 *
 * Walking to the first element node makes both behave the same, and makes it
 * safe for any component here to grow a leading comment.
 */
function cardElementOf(refValue) {
  let el = refValue?.$el ?? refValue
  while (el && el.nodeType !== 1) el = el.nextSibling
  return el && el.nodeType === 1 ? el : null
}

function scrollCardIntoView(name) {
  const el = cardElementOf(cardRefs.get(name))
  if (el) el.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
}

/**
 * Bring the subject's own card (and its photo) into the tray's viewport.
 *
 * Found by ref rather than through `cardRefs`, which is keyed by comp name and
 * the subject has none. `block: 'start'` rather than `'nearest'` because the
 * subject sits at the very top of the tray -- "nearest" from below would leave
 * it flush against the top edge with its photo still cut off.
 */
const subjectCardEl = ref(null)
function scrollSubjectIntoView() {
  const el = cardElementOf(subjectCardEl.value)
  if (el) el.scrollIntoView({ block: 'start', behavior: 'smooth' })
}

// Prod-backed `yarn dev` (and a frontend-before-backend deploy window) talks to
// a get_lead_comps that ignores `inventory`. Painting those sale pins under a
// For-rent legend is worse than empty. Require the server to echo the inventory.
const rentalReady = computed(
  () => !isRentals.value || data.value?.inventory === 'rentals',
)
const comps = computed(() => (rentalReady.value ? data.value?.comps || [] : []))

const compStreetView = ref(null)
const compStreetViewLoading = ref(false)
const compStreetViewAttempted = ref(false)

watch(focusedComp, () => {
  compStreetView.value = null
  compStreetViewAttempted.value = false
})

const streetViewPoint = computed(() => {
  const name = focusedComp.value
  if (name) {
    const c = comps.value.find((x) => x.name === name)
    if (c?.lat != null && c?.lng != null) {
      const resolved = compStreetView.value
      if (
        resolved?.ok && resolved?.available &&
        c.lat === resolved.lat && c.lng === resolved.lng
      ) {
        return {
          lat: c.lat,
          lng: c.lng,
          heading: Number.isFinite(Number(resolved.heading)) ? Number(resolved.heading) : 0,
          pano_id: resolved.pano_id || '',
          label: c.address || '',
        }
      }
      return { lat: c.lat, lng: c.lng, heading: 0, pano_id: '', label: c.address || '' }
    }
  }
  if (subjectStreetViewLoading.value) return null
  const resolved = subjectStreetView.value
  if (
    resolved?.ok && resolved?.available && resolved?.exact &&
    resolved.lat != null && resolved.lng != null
  ) {
    return {
      lat: resolved.lat,
      lng: resolved.lng,
      heading: Number.isFinite(Number(resolved.heading)) ? Number(resolved.heading) : 0,
      pano_id: resolved.pano_id || '',
      label: resolved.address || data.value?.address || props.address || '',
    }
  }
  // Once the exact-point endpoint answered, do not quietly fall back to a
  // Census/interpolated point. That is how Street View showed the neighbour's
  // house before parcel resolution existed.
  if (subjectStreetViewAttempted.value) return null
  const s = data.value?.subject
  if (s?.lat == null || s?.lng == null) return null
  const zlat = s.zillow_lat
  const zlng = s.zillow_lng
  let lat = s.lat
  let lng = s.lng
  let heading = 0
  if (
    zlat != null &&
    zlng != null &&
    (Math.abs(zlat - s.lat) > 0.00005 || Math.abs(zlng - s.lng) > 0.00005)
  ) {
    // Census point is on the street; Zillow is the rooftop. Stand on the
    // street and look at the house, or Street View faces north from the lot.
    heading = bearingDeg(s.lat, s.lng, zlat, zlng)
  } else if (zlat != null && zlng != null) {
    lat = zlat
    lng = zlng
  }
  return {
    lat,
    lng,
    heading,
    label: data.value?.address || props.address || '',
  }
})

const streetViewTitle = computed(() => {
  if (!streetViewPoint.value) return __('Street View needs a mapped address')
  return __('Street View of the subject, or the last pin you clicked') + ' (S)'
})

async function loadSubjectStreetView() {
  if (!props.lead || focusedComp.value || subjectStreetViewLoading.value) return
  if (subjectStreetViewAttempted.value && !subjectStreetView.value?.retryable) return
  subjectStreetViewLoading.value = true
  streetViewMsg.value = __('Locating the exact parcel…')
  try {
    subjectStreetView.value = await call('crm.api.address_resolve.get_street_view', {
      subject: props.lead,
    })
  } catch (_) {
    subjectStreetView.value = { ok: false, available: false, retryable: true }
  } finally {
    subjectStreetViewAttempted.value = true
    subjectStreetViewLoading.value = false
  }
}

watch(
  () => props.lead,
  () => {
    subjectStreetView.value = null
    subjectStreetViewAttempted.value = false
  },
)

async function loadCompStreetView() {
  if (!focusedComp.value) return
  const c = comps.value.find((x) => x.name === focusedComp.value)
  if (!c || c.lat == null || c.lng == null) return
  if (compStreetViewAttempted.value && !compStreetView.value?.retryable) return
  compStreetViewLoading.value = true
  try {
    compStreetView.value = await call('crm.api.streetview.metadata', {
      lat: c.lat,
      lng: c.lng,
    })
  } catch (_) {
    compStreetView.value = { ok: false, available: false, retryable: true }
  } finally {
    compStreetViewAttempted.value = true
    compStreetViewLoading.value = false
  }
}

watch([showStreet, focusedComp], ([open, focused]) => {
  if (!open) return
  if (focused) loadCompStreetView()
  else loadSubjectStreetView()
})

function onStreetViewLoad() {
  if (streetViewTimer) {
    clearTimeout(streetViewTimer)
    streetViewTimer = null
  }
  streetViewMsg.value = ''
}

function syncStreetView() {
  if (streetViewTimer) {
    clearTimeout(streetViewTimer)
    streetViewTimer = null
  }
  if (!showStreet.value) {
    streetViewSrc.value = ''
    streetViewMsg.value = ''
    return
  }
  if ((subjectStreetViewLoading.value && !focusedComp.value) || (compStreetViewLoading.value && focusedComp.value)) {
    streetViewSrc.value = ''
    streetViewMsg.value = __('Locating the exact parcel…')
    return
  }
  const pt = streetViewPoint.value
  const src = pt ? streetViewEmbedUrl(pt.lat, pt.lng, pt.heading, pt.pano_id) : ''
  if (!src) {
    streetViewSrc.value = ''
    const attempted = focusedComp.value ? compStreetViewAttempted.value : subjectStreetViewAttempted.value
    streetViewMsg.value = attempted
      ? __('No Street View is available at this parcel.')
      : __('No coordinates for Street View yet.')
    return
  }
  if (streetViewSrc.value === src) return
  streetViewSrc.value = src
  streetViewMsg.value = __('Loading…')
  streetViewTimer = setTimeout(() => {
    if (streetViewMsg.value === __('Loading…')) {
      streetViewMsg.value = __('Still loading — or no Street View at this address.')
    }
  }, 30000)
}

watch([showStreet, streetViewPoint], syncStreetView)

// Counted off what is ON THE MAP, not off the server's tally of what it found:
// the legend describes the pins in front of the rep, and a number that survives
// filtering is the same mistake the Nearby label documents.
const pendingCount = computed(() => comps.value.filter((c) => isPending(c)).length)
const auctionCount = computed(() => comps.value.filter((c) => isAuction(c)).length)

// Type glyphs and the dashed-flip note only when the board actually holds one.
// Same rule as pending: a legend entry for a state nothing on the map is in is
// one more thing to read for nothing.
const typeKindsOnBoard = computed(() => {
  const seen = new Set()
  for (const c of comps.value) {
    const k = propertyTypeKind(c.property_type)
    if (k) seen.add(k)
  }
  const sk = propertyTypeKind(data.value?.subject?.property_type)
  if (sk) seen.add(sk)
  return PROPERTY_TYPE_KIND_ORDER.filter((k) => seen.has(k)).map((kind) => ({
    kind,
    label: PROPERTY_TYPE_KINDS[kind].label,
    svg: propertyTypeGlyphSvg(kind, 12),
  }))
})
const hasFlipOnBoard = computed(() => comps.value.some((c) => c?.sale_history?.flip))

// Comps a person threw out. They arrive in their own list precisely so they can
// be shown without ever re-entering the pool: a discarded comp must not keep a
// preset tier "usable" and suppress the widening the rep actually needs.
const discarded = computed(() =>
  rentalReady.value ? data.value?.discarded || [] : [],
)
const showDiscarded = ref(false)

// `fill` sizes the map to the container. `pageMode` is the calculator. The
// comps PAGE passes pageMode and wants fillHeight. Today used to pass
// `:fill="false"` because a stacked tray under the map crushed it; the tray
// is gone on narrow, so both hosts fill and the map takes the leftover.
const fillHeight = computed(
  () => props.fill === true || (props.pageMode && props.fill !== false),
)

/**
 * Is there room beside the map for the tray?
 *
 * Measured on OUR OWN width, not the viewport's. The three hosts get wildly
 * different widths at the same viewport -- the comps page ~800px, the Today
 * modal's right pane ~620px, a phone ~260px -- so a `lg:` viewport breakpoint
 * put a 330px rail next to a 266px map inside the modal and called it a split.
 * A ResizeObserver on the root is the only thing that knows what this instance
 * actually got.
 *
 * SPLIT_MIN_WIDTH is the point below which the tray would take more from the map
 * than it gives back; under it the two stack and the map spans the full width.
 */
const SPLIT_MIN_WIDTH = 700
const rootEl = ref(null)
const calcRef = ref(null)
// Guess from the viewport so a phone does not paint one frame of desktop
// layout (tray + open calc) while ResizeObserver catches up — and so a
// background-tab open, where RO does not fire, is still compact.
const wide = ref(
  typeof window === 'undefined' ? true : window.innerWidth >= SPLIT_MIN_WIDTH,
)
let rootObserver = null

// Narrow means stacked, and a stacked filter card is a full screen of controls
// standing between the rep and the map -- eight rows before the first pin on a
// phone. Collapsed there behind the same toggle the desk uses; still always-open
// when wide, where the filters ARE the tool and hiding them behind a button is
// exactly what this layout refuses to do.
const filtersCollapsible = computed(() => props.fill || !wide.value)

// Hovering a pin should bring its card into view, not just tint a row that may
// be 200 cards down a scroller.
const cardRefs = new Map()
function setCardRef(name, el) {
  if (el) cardRefs.set(name, el)
  else cardRefs.delete(name)
}
const emptyMessage = computed(() => {
  if (data.value?.message) return data.value.message
  if (isRentals.value && !rentalReady.value) {
    return __('Rental listings need a server update — this site is still serving sale comps.')
  }
  if (isRentals.value) return __('No rental listings nearby.')
  return __('No comps found nearby.')
})
const zillowMatch = computed(() => data.value?.zillow_match || null)
const addressResolution = ref(null)
let addressStatusToken = 0

async function loadAddressStatus() {
  if (!props.lead || (props.practiceAttempt && props.practiceProperty)) return
  const token = ++addressStatusToken
  try {
    const next = await call('crm.api.address_resolve.get_address_status', {
      subject: props.lead,
    })
    if (token === addressStatusToken) addressResolution.value = next || null
  } catch (_) {
    // Schema ships before app code, but mixed deploy windows are expected. An
    // absent optional notice must never take the comp board down.
    if (token === addressStatusToken) addressResolution.value = null
  }
}

watch(() => props.lead, loadAddressStatus, { immediate: true })

function onAddressSaved(address) {
  if (data.value) data.value.address = address
  // Same record, different house string: the prior parcel/panorama belongs to
  // the old address and must not survive merely because the route id did.
  subjectStreetView.value = null
  subjectStreetViewAttempted.value = false
}

async function onAddressReran() {
  await load({ explicit: userTouched.value })
}
const presetLabel = computed(() =>
  userTouched.value ? '' : data.value?.preset?.label || '',
)

/** Counts CONSTRAINED FIELDS, not bounds, so a min+max pair reads as one filter. */
const activeFilterCount = computed(() => {
  let n = 0
  if (draft.status && draft.status !== 'all') n++
  if (isSet(draft.within_days)) n++
  if (isSet(draft.property_types)) n++
  for (const r of rangeRows) {
    if (draft[`${r.key}_min`] !== '' && draft[`${r.key}_min`] != null) n++
    else if (draft[`${r.key}_max`] !== '' && draft[`${r.key}_max`] != null) n++
  }
  return n
})

/**
 * What to tell the user about the fit of what they are looking at.
 *
 * The important case is the one Lance asked for: nothing recent and similar
 * exists, so the map is showing a wider net. That must be stated, not implied.
 */
const notice = computed(() => {
  const d = data.value
  if (!d || loading.value || !d.available || !d.subject) return null

  // We hold nothing at all here, so these were bought from BatchData. Said first
  // and unconditionally, because it changes what the numbers MEAN: recorded sale
  // prices rather than our pooled listing index, and ranked by the provider's own
  // similarity rather than by distance. Letting them render as ordinary pins would
  // quietly present two different kinds of evidence as one.
  if (d.fallback?.used && (d.fallback.count ?? 0) > 0) {
    return {
      tone: 'info',
      text: d.fallback.had_pins
        ? __('Added {0} recorded sales from BatchData ({1}).', [
            d.fallback.count,
            d.fallback.basis || __('last 2 years'),
          ])
        : __(
            'We hold no comps here — showing {0} recorded sales from BatchData ({1}).',
            [d.fallback.count, d.fallback.basis || __('last 2 years')],
          ),
    }
  }
  if (d.fallback?.used && (d.fallback.count ?? 0) === 0 && !d.fallback.had_pins) {
    return {
      tone: 'warning',
      text: __('No comps here, and no recorded sales nearby either.'),
    }
  }

  if ((d.total_matched ?? 0) === 0 && (d.total_in_radius ?? 0) > 0) {
    return {
      tone: 'warning',
      text: userTouched.value
        ? __('No comps match these filters. {0} properties are within {1} mi.', [
            d.total_in_radius,
            d.radius_mi,
          ])
        : __('Nothing nearby resembles this property.'),
      action: userTouched.value
        ? { label: __('Reset to suggested'), run: resetToSuggested }
        : { label: __('Show everything nearby'), run: clearAll },
    }
  }
  if (userTouched.value || !d.relaxed) return null

  // Fell all the way through the ladder: these are simply the nearest properties,
  // and calling them comparable would be a lie.
  if (d.fell_through) {
    return {
      tone: 'warning',
      text: __(
        'No recent, similar comps nearby — showing all {0} properties within {1} mi. These may not be comparable.',
        [d.total_matched, d.radius_mi],
      ),
    }
  }
  return {
    tone: 'info',
    text: __('No recent, similar comps nearby — widened to “{0}” to find {1}.', [
      d.preset?.label || '',
      d.total_matched,
    ]),
  }
})

/** Exact match — /active/i would wrongly match "Inactive". */
function isActive(status) {
  return /^active$/i.test(String(status || '').trim())
}

/**
 * Days used to fade a pill: an active listing ages by time on market, an
 * off-market one by how long ago it left. Falls back through days_old and DOM
 * when a removal date is missing, so a comp never silently reads as brand new.
 */
function stalenessDays(c) {
  if (isActive(c.status)) {
    const dom = Number(c.days_on_market)
    return Number.isFinite(dom) && dom >= 0 ? dom : 0
  }
  if (c.removed_date) {
    const ms = Date.parse(c.removed_date)
    if (Number.isFinite(ms)) return Math.max(0, (Date.now() - ms) / 86400000)
  }
  const old = Number(c.days_old)
  if (Number.isFinite(old) && old >= 0) return old
  const dom = Number(c.days_on_market)
  return Number.isFinite(dom) && dom >= 0 ? dom : 0
}

/** 0d -> 1.0 (solid); 360d+ -> ~0.32. Smoothstep keeps the first month strong. */
/**
 * Recency fade. 0d → 1.0, 360d+ → 0.32.
 *
 * Applied to the FILL only, and only to off-market pins — see `pillIcon`. Fading
 * the whole pill (text included) is what made the new palette unreadable: a
 * faded red pill kept white text that washed into the basemap at 2.5:1, and a
 * faded yellow one vanished entirely at 1.9:1. With the text held solid the fade
 * can stay deep, so the signal is stronger than the stopgap that shallowed it.
 */
function pillOpacity(days) {
  const t = Math.max(0, Math.min(1, days / 360))
  const eased = t * t * (3 - 2 * t)
  return 1 - eased * 0.68
}

/** `#rrggbb` + alpha -> `rgba(...)`, so a fill can fade without its text fading. */
function withAlpha(hex, alpha) {
  const h = String(hex).replace('#', '')
  const n = parseInt(
    h.length === 3 ? h.split('').map((c) => c + c).join('') : h,
    16,
  )
  return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${alpha.toFixed(3)})`
}

function priceShort(p, rental = false) {
  const n = Number(p)
  if (!Number.isFinite(n) || n <= 0) return '—'
  // Rent is monthly. $1,450 must not collapse to "$1k".
  if (rental) return '$' + Math.round(n).toLocaleString()
  // `>= 999500`, not `>= 1000000`: rounding happens BEFORE the unit is chosen, so
  // the old threshold let $999,500 round up to "$1000k" — four digits, and the
  // widest thing on any pill, for the one price that should have read "$1.0m".
  if (n >= 999500) return '$' + (n / 1000000).toFixed(n >= 10000000 ? 0 : 1) + 'm'
  if (n >= 1000) return '$' + Math.round(n / 1000) + 'k'
  return '$' + Math.round(n)
}

/**
 * How long it took to sell, for the pill's bottom-right slot. '' when unknown.
 *
 * A duration, against the calendar date on the top-right of a sold pin — two
 * different kinds of number, so they don't need labels. `Number(null) === 0`
 * used to paint "0d" here whenever the listing chain was missing.
 */
function soldInShort(c) {
  const n = daysToSell(c)
  return n == null ? '' : `${Math.round(n)}d`
}

/** `Jul 17` — date-only, local, no year. The year lives in the tooltip. */
function soldOnShort(v) {
  if (!v) return ''
  const m = String(v).match(/^(\d{4})-(\d{2})-(\d{2})/)
  if (!m) return ''
  const d = new Date(+m[1], +m[2] - 1, +m[3])
  if (!Number.isFinite(d.getTime())) return ''
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
}

function listingDays(c) {
  return (
    finiteDays(c?.recency_days) ??
    finiteDays(c?.days_on_market) ??
    finiteDays(c?.sale_history?.days_on_market)
  )
}

/**
 * True when this comp was bought and resold (or relisted) fast enough, and for
 * enough more, that it should not be priced off without a second look.
 *
 * The threshold lives on the SERVER (18 months, +30%) so the map, the tray and
 * the detail panel cannot disagree about what counts. This only asks whether the
 * server said so.
 */
function isFlip(c) {
  return !!c?.sale_history?.flip
}

function fmtDate(v) {
  if (!v) return '—'
  const s = String(v)
  // GOTCHA — a bare "YYYY-MM-DD" is parsed by Date.parse as UTC MIDNIGHT, which
  // then renders as the PREVIOUS DAY everywhere west of Greenwich. Every comp
  // date in this modal read a day early in Chicago (a sale on Oct 9 showed as
  // Oct 8). Date-only values are calendar dates with no timezone, so build them
  // as local; anything with a time component still goes through Date.parse.
  const ymd = /^(\d{4})-(\d{2})-(\d{2})$/.exec(s.trim())
  const d = ymd
    ? new Date(Number(ymd[1]), Number(ymd[2]) - 1, Number(ymd[3]))
    : new Date(Date.parse(s))
  if (!Number.isFinite(d.getTime())) return s.slice(0, 10)
  return d.toLocaleDateString('en-US', {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
  })
}

function escapeHtml(s) {
  return String(s ?? '').replace(
    /[&<>"']/g,
    (m) =>
      ({
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
        "'": '&#39;',
      })[m],
  )
}

/**
 * The pieces of a detailed pill, split across two lines.
 *
 * Year rides on the TOP line next to the price rather than in the facts line,
 * and that is a width decision, not a cosmetic one: the pill is as wide as its
 * widest line, and "3/2 · 1,395sf · 1910" was the widest thing on it. Moving the
 * four year digits up beside the short price shortens the line that was setting
 * the width, and lengthens the one that wasn't.
 */
function pillBits(c) {
  const bb = c.bedrooms || c.bathrooms ? `${c.bedrooms || '?'}/${c.bathrooms || '?'}` : ''
  const sf = c.square_footage ? `${Number(c.square_footage).toLocaleString()}sf` : ''
  return {
    year: Number(c.year_built) > 0 ? String(Math.round(Number(c.year_built))) : '',
    line2: [bb, sf].filter(Boolean).join(' · '),
  }
}

/**
 * Everything a detailed pill says, for the title tooltip / measurement.
 *
 * Tooltip restates the two times in words, because the pill itself cannot
 * afford the labels.
 */
function pillFacts(c) {
  const { year, line2 } = pillBits(c)
  const took = daysToSell(c)
  const f = c?.sale_history?.flip
  const soldOn = soldOnShort(c.removed_date)
  const listed = listingDays(c)
  const typeKind = propertyTypeKind(c.property_type)
  return [
    typeKind ? PROPERTY_TYPE_KINDS[typeKind].label : '',
    line2,
    year,
    soldOn ? __('sold {0}', [soldOn]) : '',
    took != null ? __('took {0}d to sell', [Math.round(took)]) : '',
    isActive(c.status) && listed != null ? __('listed {0}d', [Math.round(listed)]) : '',
    f
      ? f.kind === 'relist'
        ? __('possible flip — bought {0} ago for {1}, now asking {2}% more', [
            agoShort(f.hold_days),
            priceShort(f.bought_price),
            Math.round((f.pct || 0) * 100),
          ])
        : __('possible flip — bought {0} earlier for {1}, resold {2}% higher', [
            agoShort(f.hold_days),
            priceShort(f.bought_price),
            Math.round((f.pct || 0) * 100),
          ])
      : '',
  ]
    .filter(Boolean)
    .join(' · ')
}

/**
 * Design B: price bold, facts beneath in small type.
 *
 * Measured against the one-line alternative on a real 418-comp board: 115px wide
 * vs 186px, which is the difference between readable and a wall of overlapping
 * pills in a tight cluster. `showDetail` collapses it back to the bare price pill
 * (the `d` shortcut), because on a dense urban board the overview is sometimes
 * worth more than the facts.
 *
 * A SELECTED comp gets a white ring and always shows its facts — it is the one
 * someone is actually pricing off, so it should never be the pin you lose.
 */
/**
 * The hover-only ✕ that drops a comp off the map.
 *
 * Rendered into every pill but hidden until the pill is hovered (CSS at the
 * bottom of this file), so removing an obviously-wrong comp is one click on the
 * thing itself rather than click → read popup → find button. Same delegated
 * handler as the popup's Hide button, so both paths write the same state.
 */
function hideBadge(c) {
  // Selected: − takes it out of the calc table, pin stays. Unselected: ✕ hides
  // it from the map. Same hover reveal either way.
  if (c.selected) {
    return `<span class="comps-pill-x" data-comp-unuse="${escapeHtml(c.name)}"
        title="${__('Remove from table — stays on the map')}"
        style="position:absolute;top:-6px;right:-6px;width:15px;height:15px;
        border-radius:50%;background:#fff;color:#44423d;border:1px solid #cfccc5;
        box-shadow:0 1px 2px rgba(0,0,0,.3);font:700 10px/13px ui-sans-serif,system-ui;
        text-align:center;cursor:pointer">−</span>`
  }
  return `<span class="comps-pill-x" data-comp-use="${escapeHtml(c.name)}"
      title="${__('Add to table')}"
      style="position:absolute;top:-6px;right:-6px;width:15px;height:15px;
      border-radius:50%;background:#fff;color:#2563c9;border:1px solid #93c5fd;
      box-shadow:0 1px 2px rgba(0,0,0,.3);font:700 11px/13px ui-sans-serif,system-ui;
      text-align:center;cursor:pointer">+</span>`
}

/**
 * The subject as a pill, in the same two-line shape as the comps.
 *
 * It used to be an 18px dot, which marked the spot but said nothing — you had to
 * click it to find out what you were comparing against. Rendering it like a comp
 * (bd/ba · sqft, year on the top line) means the subject's own numbers sit in the
 * same visual grammar as the numbers you are judging it by, so "is this comp
 * bigger or smaller than mine" is a glance rather than a memory test.
 *
 * Blue with a heavier white ring so it never reads as one of the comps, and it
 * keeps the centre anchor so the pill's middle still marks the real parcel.
 */
function subjectIcon(s) {
  const year = s.year_built_label || ''
  const bb =
    s.beds_label || s.baths_label ? `${s.beds_label || '?'}/${s.baths_label || '?'}` : ''
  const sf = s.sqft_label ? `${s.sqft_label}sf` : ''
  const line2 = [bb, sf].filter(Boolean).join(' · ')
  const label = __('Subject')
  // Same type mark the comps wear, so a manufactured subject is not the one
  // unmarked pill on a board of manufactured comps.
  const glyph = propertyTypeGlyphHtml(s.property_type)
  const glyphW = glyph ? 16 : 0

  // With details off (the D toggle) the comps collapse to a bare price, so the
  // subject collapses to a bare label rather than staying loud on its own.
  if (!showDetail.value || (!year && !line2)) {
    const w = Math.max(52, Math.ceil(18 + glyphW + label.length * 6.6))
    return L.divIcon({
      className: 'comps-price-pill',
      html: `<div class="comps-pill-body" style="display:flex;align-items:center;
          justify-content:center;box-sizing:border-box;width:${w}px;height:24px;
          background:${SUBJECT};color:#fff;font:700 11px/1 ui-sans-serif,system-ui,sans-serif;
          border-radius:999px;border:2px solid #fff;
          box-shadow:0 1px 6px rgba(0,0,0,.5);white-space:nowrap;overflow:visible">${glyph}${label}</div>`,
      iconSize: [w, 24],
      iconAnchor: [w / 2, 12],
      popupAnchor: [0, -14],
    })
  }

  const top = glyphW + label.length * 6.6 + (year ? 3 + year.length * 5.0 : 0)
  const w = Math.max(58, Math.ceil(14 + Math.max(top, line2.length * 5.05)))
  const yearHtml = year
    ? `<span style="font:500 9px/1 ui-sans-serif,system-ui,sans-serif;opacity:.8;
         margin-left:3px">${escapeHtml(year)}</span>`
    : ''
  const line2Html = line2
    ? `<div style="font:500 9px/1 ui-sans-serif,system-ui,sans-serif;opacity:.95;
         margin-top:2px">${escapeHtml(line2)}</div>`
    : ''
  return L.divIcon({
    className: 'comps-price-pill',
    html: `<div class="comps-pill-body" style="display:flex;flex-direction:column;
        align-items:center;justify-content:center;box-sizing:border-box;width:${w}px;
        height:34px;background:${SUBJECT};color:#fff;border-radius:9px;
        border:2px solid #fff;box-shadow:0 1px 6px rgba(0,0,0,.55);
        white-space:nowrap;line-height:1;overflow:visible">
        <div style="display:flex;align-items:center;justify-content:center">
          ${glyph}<span style="font:700 11px/1 ui-sans-serif,system-ui,sans-serif">${label}</span>${yearHtml}
        </div>
        ${line2Html}
      </div>`,
    iconSize: [w, 34],
    iconAnchor: [w / 2, 17],
    popupAnchor: [0, -19],
  })
}

function pillIcon(c) {
  const active = isActive(c.status)
  const opacity = pillOpacity(stalenessDays(c))
  const pal = compColor(c)
  // The fade means ONE thing: how old the SALE is. An active listing is current
  // by definition -- how long it has sat is already printed on it as DOM -- so
  // it never fades, which also keeps its white-on-red legible. A selected pill is
  // never faded either: an explicit human pick outranks the recency signal.
  const alpha = active || c.selected ? 1 : opacity
  const bg = withAlpha(pal.bg, alpha)
  // Text and border are held SOLID while the fill fades. That is what lets the
  // fade stay deep without the price becoming unreadable, and it is why the ink
  // can differ per colour: white on the red, near-black on the yellow.
  const ink = pal.ink
  const price =
    compState(c) === 'auction' && !(Number(c.price) > 0)
      ? 'Auction'
      : priceShort(c.price, isRentalComp(c))
  const { year, line2 } = pillBits(c)
  // Selected does NOT switch to the tall pill — that resized the icon under the
  // pointer and read as the map jumping. The ring is the selected signal.
  const detailed = showDetail.value && (year || line2)
  const ring = c.selected
    ? `box-shadow:0 0 0 2px #fff,0 0 0 4px ${COMP_COLORS.subject.bg},0 1px 3px rgba(0,0,0,.4);`
    : 'box-shadow:0 1px 3px rgba(0,0,0,.35);'
  // Flip used to be a star on the price line, which collides with the type glyph.
  // A dashed outline leaves the interior alone: a condo flip is a dashed condo.
  // Ink, not the faded fill, so it survives recency the way the old star did.
  const flip = isFlip(c)
  const border = flip
    ? `2px dashed ${ink}`
    : `1px solid ${withAlpha(pal.border, Math.max(alpha, 0.55))}`
  const borderSolid = flip ? ink : pal.border
  const op = '1'
  const glyph = propertyTypeGlyphHtml(c.property_type)
  const glyphW = glyph ? 16 : 0

  if (!detailed) {
    // Glyph and dashed outline both survive the `D` collapse. Details are hidden
    // on a dense board -- exactly where a bad (or wrong-type) comp is easiest to
    // price off by mistake -- so neither mark is part of what gets hidden.
    const w = Math.max(40, Math.ceil(18 + glyphW + (flip ? 4 : 0) + price.length * 7.4))
    return L.divIcon({
      className: 'comps-price-pill',
      html: `<div class="comps-pill-body" title="${escapeHtml(pillFacts(c))}"
          style="position:relative;display:flex;
          align-items:center;justify-content:center;
          box-sizing:border-box;width:${w}px;height:24px;background:${bg};color:${ink};
          --pill-bg-solid:${pal.bg};--pill-border-solid:${borderSolid};
          font:700 11px/1 ui-sans-serif,system-ui,sans-serif;border-radius:999px;
          border:${border};${ring}white-space:nowrap;overflow:visible;
          opacity:${op}">${glyph}${price}${hideBadge(c)}</div>`,
      iconSize: [w, 24],
      iconAnchor: [w / 2, 12],
      popupAnchor: [0, -14],
    })
  }
  // Line 1 = bold price + small dim year + age; line 2 = beds/baths · sqft. The
  // pill is sized to whichever line is actually wider, which is why age rides up
  // here: line 1 had slack and line 2 was the binding constraint. Measured over
  // 200 real comps, this costs 82 -> 90px average, where putting it on line 2
  // would have cost 113px and undone the whole point of the two-line layout.
  //
  // Sold: the calendar date it closed (`Jul 17`), so it cannot be read as the
  // duration on the line below. Listed AND pending: days on market. Pending used
  // to spend this slot on the word; the violet fill already says that, and the
  // missing number was the complaint.
  const age = isActive(c.status)
    ? agoShort(listingDays(c))
    : soldOnShort(c.removed_date) || agoShort(c.recency_days)
  // The OTHER time number. `age` says how long since the news; this says how long
  // the house took to sell, from the first listing of the run that ended in the
  // sale (price cuts included -- a seller who cut twice sat there the whole time).
  // A live listing has not sold, so it never has one and the slot stays empty.
  const soldIn = soldInShort(c)
  const top =
    glyphW +
    price.length * 7.0 +
    (year ? 3 + year.length * 5.0 : 0) +
    (age ? 3 + age.length * 5.0 : 0)
  const line2w = line2.length * 5.05 + (soldIn ? 6 + soldIn.length * 5.05 : 0)
  const w = Math.max(52, Math.ceil(12 + (flip ? 4 : 0) + Math.max(top, line2w)))
  const yearHtml = year
    ? `<span style="font:500 9px/1 ui-sans-serif,system-ui,sans-serif;opacity:.72;
         margin-left:3px">${year}</span>`
    : ''
  const ageHtml = age
    ? `<span style="font:500 9px/1 ui-sans-serif,system-ui,sans-serif;opacity:.72;
         margin-left:3px">${age}</span>`
    : ''
  // Stretched to the pill's width so the two halves can sit at opposite ends --
  // facts left, time-to-sell right. Without a soldIn it stays centred exactly as
  // before, so a pill that has nothing new to say is byte-identical to the old one.
  const line2Html = line2 || soldIn
    ? `<div style="display:flex;align-items:baseline;width:100%;padding:0 1px;
         box-sizing:border-box;gap:6px;justify-content:${
           soldIn && line2 ? 'space-between' : 'center'
         };font:500 9px/1 ui-sans-serif,system-ui,sans-serif;opacity:.9;
         margin-top:2px"><span>${escapeHtml(line2)}</span>${
           soldIn
             ? `<span style="font-variant-numeric:tabular-nums">${soldIn}</span>`
             : ''
         }</div>`
    : ''
  return L.divIcon({
    className: 'comps-price-pill',
    html: `<div class="comps-pill-body" title="${escapeHtml(pillFacts(c))}"
        style="position:relative;display:flex;flex-direction:column;align-items:center;
        justify-content:center;box-sizing:border-box;width:${w}px;height:34px;
        background:${bg};color:${ink};border-radius:9px;border:${border};${ring}
        --pill-bg-solid:${pal.bg};--pill-border-solid:${borderSolid};
        white-space:nowrap;line-height:1;opacity:${op};overflow:visible">
        <div style="display:flex;align-items:center;justify-content:center">
          ${glyph}<span style="font:700 11.5px/1 ui-sans-serif,system-ui,sans-serif">${price}</span>${yearHtml}${ageHtml}
        </div>
        ${line2Html}
        ${hideBadge(c)}
      </div>`,
    iconSize: [w, 34],
    iconAnchor: [w / 2, 17],
    popupAnchor: [0, -19],
  })
}

function fmtMoney(v) {
  const n = Number(v)
  if (!Number.isFinite(n) || n <= 0) return null
  return '$' + Math.round(n).toLocaleString()
}

/** Plain number for the discrepancy rows: baths keep their half, the rest are whole. */
function fmtNum(v) {
  const n = Number(v)
  if (!Number.isFinite(n)) return escapeHtml(String(v ?? ''))
  return Number.isInteger(n) ? n.toLocaleString() : String(n)
}

/**
 * The subject's own card. This is the property everything else is compared to, so
 * it earns the same facts a comp shows plus what it last listed for.
 *
 * Two honesty rules, both load-bearing:
 *  - facts are labelled by SOURCE. "3 bd" off a listing record and "3 bd" typed
 *    into a web form by a motivated seller are not the same claim, and a band
 *    ("1000-2000 sqft") is shown as the band it is rather than a fake midpoint.
 *  - the price is called a LIST price, never a sale. This inventory carries the
 *    last ask, and going off-market is not a confirmed close.
 */
function subjectPopupHtml(s) {
  const addr = escapeHtml(data.value?.address || props.address || '')
  const facts = [
    s.beds_label ? `${s.beds_label} bd` : '',
    s.baths_label ? `${s.baths_label} ba` : '',
    s.sqft_label ? `${s.sqft_label} sqft` : '',
    s.year_built_label ? `built ${s.year_built_label}` : '',
  ]
    .filter(Boolean)
    .join(' · ')

  const rows = []
  if (facts) {
    rows.push(
      `<div style="margin-top:3px;color:#161614;font-weight:600">${escapeHtml(facts)}</div>`,
    )
  }
  if (s.property_type) {
    rows.push(`<div style="color:#5c5a55">${escapeHtml(s.property_type)}</div>`)
  }
  if (s.condition) {
    rows.push(`<div style="color:#5c5a55">${escapeHtml(s.condition)}</div>`)
  }
  if (!facts && !s.property_type && !s.condition) {
    rows.push(
      `<div style="margin-top:3px;color:#8a877e">${__('No property details on this lead yet.')}</div>`,
    )
  }

  // Zillow vs Redfin disagreeing about the house the rep is pricing. Amber, not
  // red -- a "verify before pricing" nudge. Null in the common case (check did
  // not run / no Redfin row / the sources agree), so nothing renders.
  const rc = s.redfin_check
  if (rc?.fields?.length) {
    const lines = rc.fields
      .map(
        (f) =>
          `${__('Zillow')} ${fmtNum(f.zillow)} · ${__('Redfin')} ${fmtNum(f.redfin)} ${escapeHtml(f.label || '')}`,
      )
      .join('<br>')
    rows.push(
      `<div style="margin-top:6px;padding:6px 8px;border-radius:6px;background:#fef3c7;
        border:1px solid #fcd34d;color:#92400e">
        <div style="font-weight:700">⚠ ${__('Zillow ≠ Redfin')}</div>
        <div>${lines}</div>
        <div style="margin-top:2px;font-size:10px">${__('The sources disagree — verify before pricing.')}</div>
      </div>`,
    )
  }

  // What it ACTUALLY SOLD for. Zillow's priceHistory carries Public Record `Sold`
  // rows, which is a real transaction with a date — a different and much stronger
  // claim than the comp inventory's last ask below, so it is shown separately and
  // first, and is the one thing here allowed to use the word "sold".
  const sale = s.last_sale
  if (sale && (sale.price || sale.date)) {
    const sp = fmtMoney(sale.price)
    rows.push(
      `<div style="margin-top:6px;padding-top:6px;border-top:1px solid #e5e3de">
        <div style="font-size:10px;font-weight:700;text-transform:uppercase;
          letter-spacing:.04em;color:#8a877e">${__('Last sold')}</div>
        ${sp ? `<div style="font-size:15px;font-weight:700;color:#161614">${sp}</div>` : ''}
        <div style="color:#5c5a55">${fmtDate(sale.date)}</div>
        ${
          sale.source
            ? `<div style="color:#8a877e;font-size:10px">${escapeHtml(sale.source)}</div>`
            : ''
        }
      </div>`,
    )
  }

  // Last time this house itself was on the market, when we happen to hold it.
  const ll = s.last_listing
  if (ll && (ll.price || ll.listed_date)) {
    const price = fmtMoney(ll.price)
    const live = isActive(ll.status)
    const when = live
      ? __('Listed {0} · still on the market', [fmtDate(ll.listed_date)])
      : `${__('Listed {0}', [fmtDate(ll.listed_date)])} → ${__('off-market {0}', [fmtDate(ll.removed_date)])}`
    const dom = ll.days_on_market
      ? `<div style="color:#8a877e">${Math.round(ll.days_on_market)}d ${__('on market')}</div>`
      : ''
    rows.push(
      `<div style="margin-top:6px;padding-top:6px;border-top:1px solid #e5e3de">
        <div style="font-size:10px;font-weight:700;text-transform:uppercase;
          letter-spacing:.04em;color:#8a877e">${live ? __('Currently listed') : __('Last listed')}</div>
        ${price ? `<div style="font-size:15px;font-weight:700;color:#161614">${price}</div>` : ''}
        <div style="color:#5c5a55">${when}</div>
        ${dom}
        <div style="color:#8a877e;margin-top:2px;font-style:italic">${__('Last list price (an ask) — not a verified sale.')}</div>
      </div>`,
    )
  }

  const extras = [
    fmtMoney(s.zestimate) ? `${__('Zestimate')} ${fmtMoney(s.zestimate)}` : '',
    fmtMoney(s.redfin_estimate) ? `${__('Redfin')} ${fmtMoney(s.redfin_estimate)}` : '',
    fmtMoney(s.realtor_estimate?.value)
      ? `${__('Realtor')} ${fmtMoney(s.realtor_estimate.value)}`
      : '',
    fmtMoney(s.assessed_value) ? `${__('Assessed')} ${fmtMoney(s.assessed_value)}` : '',
    fmtMoney(s.annual_tax) ? `${__('Tax')} ${fmtMoney(s.annual_tax)}/yr` : '',
    fmtMoney(s.asking_price) ? `${__('Asking')} ${fmtMoney(s.asking_price)}` : '',
    s.lot_size ? `${__('Lot')} ${s.lot_size}` : '',
  ].filter(Boolean)
  if (extras.length) {
    rows.push(
      `<div style="margin-top:4px;color:#5c5a55">${escapeHtml(extras.join(' · '))}</div>`,
    )
  }

  // The subject gets the same gallery every comp has. It was the one house on
  // the board you could not look at -- which is backwards, because it is the
  // house being priced. Same delegated popup-click listener as the comp buttons.
  rows.push(
    `<button data-subject-details="1" style="width:100%;cursor:pointer;margin-top:8px;
      font:600 11px/1 ui-sans-serif,system-ui;padding:7px 8px;border-radius:6px;
      border:1px solid #e5e3de;background:#fff;color:${SUBJECT}">${__('Photos & details')}</button>`,
  )
  if (s.lat != null && s.lng != null) {
    rows.push(
      `<button data-subject-street="1" style="width:100%;cursor:pointer;margin-top:6px;
        font:600 11px/1 ui-sans-serif,system-ui;padding:7px 8px;border-radius:6px;
        border:1px solid #e5e3de;background:#fff;color:#161614">${__('Street View')}</button>`,
    )
  }

  const sources = Object.values(s.source || {})
  if (sources.length) {
    // Name the strongest source present. A rep reading "1,438 sqft" deserves to
    // know whether that came from Zillow, from a listing record, or from whatever
    // a motivated seller typed into a web form.
    const label = sources.includes('zillow')
      ? __('Details from Zillow')
      : sources.includes('listing')
        ? __('Details from this property’s own listing record')
        : sources.includes('manual')
          ? __('Details set manually')
          : __('Details as reported by the seller')
    // The one fact a rep corrected by hand outranks the scraped label above it,
    // and saying so is the point of labelling sources at all.
    const manual = s.source?.sqft === 'manual' ? ` · ${__('sqft set manually')}` : ''
    rows.push(
      `<div style="margin-top:6px;font-size:10px;color:#8a877e">${label}${manual}</div>`,
    )
  }

  return `<div style="min-width:200px;max-width:260px;font:12px/1.45 system-ui,sans-serif;color:#161614">
      <div style="font-weight:700;color:${SUBJECT}">${__('This property')}</div>
      <div style="color:#5c5a55">${addr}</div>
      ${rows.join('')}
    </div>`
}

/**
 * The same age with no words, for the pill — "9d", "4mo", "2y".
 *
 * The pill is sized by its widest line, so " ago" is not free: it measured 103px
 * per pill against 90px without it. The popup and the list keep the wordy form,
 * where there is room and the sentence reads better.
 */
function agoShort(days) {
  const d = finiteDays(days)
  if (d == null) return ''
  if (d < 31) return `${Math.round(d)}d`
  if (d < 365) return `${Math.round(d / 30.44)}mo`
  const y = d / 365.25
  return `${y < 2 ? y.toFixed(1) : Math.round(y)}y`
}

// NOTE neither pin has a Leaflet popup. Comp pins open CompDetailModal on click
// (since 698523dd). The subject pin used to bind `subjectPopupHtml`; that extra
// tap sat in front of the same gallery on a phone, so it now opens the modal
// too. `subjectPopupHtml` is still called from the popup-click delegate in case
// a leftover popup is open, but nothing binds one.

function render() {
  if (!mapEl.value) return
  const s = data.value?.subject
  // Keep the view the rep is looking at. Filter / discard / use used to destroy
  // the map and fitBounds again, which yanked them from a street they had zoomed
  // into out to the whole circle. Only refit when the SUBJECT or the RADIUS
  // changed — those are the two things that actually change what "the area" is.
  const fitKey =
    s?.lat != null ? `${s.lat.toFixed(5)},${s.lng.toFixed(5)},${Number(radius.value)}` : ''
  const prev =
    map && lastFitKey === fitKey
      ? { center: map.getCenter(), zoom: map.getZoom() }
      : null

  if (map) {
    unbindParcels()
    map.remove()
    map = null
  }
  // Markers are rebuilt below; stale entries would otherwise leak and the hover
  // watcher would try to light up an element no longer on the map.
  markersByName.clear()
  if (!s?.lat) return

  // scrollWheelZoom OFF deliberately. The map sits above the property list on a
  // full page, so a wheel over the map used to be swallowed by Leaflet and the
  // list below was unreachable on a laptop-height window (caught in review).
  // Zoom is still available via the +/- control and pinch on a trackpad.
  map = L.map(mapEl.value, {
    center: prev ? prev.center : [s.lat, s.lng],
    zoom: prev ? prev.zoom : 14,
    scrollWheelZoom: false,
  })
  L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
    maxZoom: 19,
    attribution: '© OpenStreetMap',
  }).addTo(map)

  const bounds = [[s.lat, s.lng]]

  // Distance rings give instant scale. L.circle is metres on the ground, so the
  // rings stay true at any latitude (L.circleMarker would be pixels and lie).
  for (const [mi, label] of [
    [0.5, '½ mi'],
    [1, '1 mi'],
    [2, '2 mi'],
  ]) {
    if (mi > (data.value?.radius_mi || 2)) continue
    L.circle([s.lat, s.lng], {
      radius: mi * 1609.344,
      color: '#161614',
      weight: 1,
      opacity: 0.3,
      dashArray: '4 4',
      fill: false,
      interactive: false,
    }).addTo(map)
    L.marker([s.lat + mi / 69, s.lng], {
      icon: L.divIcon({
        className: '',
        // display:inline-block is load-bearing: iconSize [0,0] gives the icon
        // container zero width, and translate(-50%) of a BLOCK child inside it
        // resolves to 0px — so the label was never actually centred, it just
        // started at the ring point and ran right. Shrink-to-fit gives the
        // transform a real width to halve.
        html: `<div style="display:inline-block;transform:translate(-50%,-50%);
            background:rgba(255,255,255,.85);padding:0 4px;border-radius:4px;
            font:600 9px/14px system-ui,sans-serif;color:#44423d;white-space:nowrap">${label}</div>`,
        iconSize: [0, 0],
      }),
      interactive: false,
    }).addTo(map)
  }

  // Repaint the neighbourhood whenever the map is rebuilt (a reload, a radius
  // change), or the layer would silently vanish while its button still reads on.
  if (hoodOn.value) nextTick(paintHood)
  if (showParcels.value) nextTick(bindParcels)

  for (const c of comps.value) {
    if (c.lat == null || c.lng == null) continue
    placePin(c)
    bounds.push([c.lat, c.lng])
  }

  // The subject goes on LAST so it is never buried under a comp pill.
  const subjectMarker = L.marker([s.lat, s.lng], {
    zIndexOffset: SUBJECT_Z,
    // A real iconSize + centre anchor, NOT the 0x0-plus-transform trick the rings
    // use. With a zero-width container the horizontal translate(-50%) collapsed to
    // 0, so the marker for "the real parcel" was drawn ~9px to the RIGHT of the
    // coordinate it claims to mark — and its hit area sat off it too, on the one
    // pin that is expected to be clicked for the subject's details.
    icon: subjectIcon(s),
  }).addTo(map)
  // Same as a comp pin: tap opens the photo/detail modal. The old Leaflet popup
  // sat in front of that gallery and on a phone was an extra tap for no gain.
  subjectMarker.on('click', () => openSubjectDetail())

  // Hovering the subject pin scrolls its card -- and so its photo -- into view in
  // the tray, exactly as hovering a comp pin already did. The subject was the one
  // pin on the map that did nothing on hover, which made it feel like it was not
  // part of the same board.
  subjectMarker.on('mouseover', () => {
    hoverSource = 'map'
    scrollSubjectIntoView()
  })

  if (!prev && bounds.length > 1) {
    try {
      map.fitBounds(bounds, { padding: [30, 30], maxZoom: 16 })
      lastFitKey = fitKey
    } catch {
      /* single point / degenerate bounds — keep the default view */
    }
  } else {
    lastFitKey = fitKey
  }
  // Popup HTML is injected, so its buttons cannot carry Vue handlers. One
  // delegated listener on the map container covers every popup instead.
  map.getContainer().addEventListener('click', onPopupClick)

  // Leaflet mis-measures a container that was display:none when it mounted,
  // which is exactly what a modal is until the moment it opens.
  setTimeout(() => map && map.invalidateSize(), 120)
}

function onPopupClick(e) {
  const subject = e.target?.closest?.('[data-subject-details]')
  if (subject) {
    e.preventDefault()
    openSubjectDetail()
    return
  }
  const subjectStreet = e.target?.closest?.('[data-subject-street]')
  if (subjectStreet) {
    e.preventDefault()
    openStreetView(null)
    return
  }
  const use = e.target?.closest?.('[data-comp-use]')
  if (use) {
    e.preventDefault()
    e.stopPropagation()
    const name = use.getAttribute('data-comp-use')
    // After the click, not during: replacing the icon mid-click lets Leaflet
    // treat the mouseup as a map drag, which is the jump.
    setTimeout(() => toggleUse(name), 0)
    return
  }
  const detail = e.target?.closest?.('[data-comp-details]')
  if (detail) {
    e.preventDefault()
    openCompDetail(detail.getAttribute('data-comp-details'))
    return
  }
  const hide = e.target?.closest?.('[data-comp-hide]')
  if (hide) {
    e.preventDefault()
    e.stopPropagation()
    const name = hide.getAttribute('data-comp-hide')
    setTimeout(() => setCompState(name, 'hidden'), 0)
    return
  }
  const unuse = e.target?.closest?.('[data-comp-unuse]')
  if (unuse) {
    e.preventDefault()
    e.stopPropagation()
    const name = unuse.getAttribute('data-comp-unuse')
    setTimeout(() => setCompState(name, 'none'), 0)
  }
}

/**
 * Open one comp's photo gallery + Zillow facts.
 *
 * Resolved from `comps` by name rather than passed by reference so the popup
 * (whose HTML is injected and therefore holds only strings) and the list row
 * reach the identical object -- and so a reload cannot leave the modal showing a
 * comp that is no longer on the map.
 */
function findCompByName(name) {
  return (
    comps.value.find((c) => c.name === name) ||
    discarded.value.find((c) => c.name === name) ||
    null
  )
}

function openCompDetail(name) {
  const comp = findCompByName(name)
  if (!comp) return false
  focusedComp.value = name
  subjectDetail.value = false
  detailComp.value = comp
  showCompDetail.value = true
  return true
}

function copyCompLink(name, { subject = false } = {}) {
  if (isPractice.value) return
  const url = compsDetailUrl(props.lead, { compName: name, subject })
  if (url) copyToClipboard(url)
}

function currentCompQuery() {
  if (!showCompDetail.value) return ''
  if (subjectDetail.value) return COMP_QUERY_SUBJECT
  return detailComp.value?.name || ''
}

function writeCompQuery() {
  if (!props.pageMode) return
  const want = currentCompQuery()
  const have = typeof route.query.comp === 'string' ? route.query.comp : ''
  if (want === have) return
  const q = { ...route.query }
  if (want) q.comp = want
  else delete q.comp
  router.replace({ query: q })
}

function applyCompQuery() {
  if (!props.pageMode) return
  const raw = typeof route.query.comp === 'string' ? route.query.comp : ''
  if (raw === currentCompQuery()) {
    if (raw && raw !== COMP_QUERY_SUBJECT) {
      const next = findCompByName(raw)
      if (next) detailComp.value = next
    }
    return
  }
  if (!raw) {
    showCompDetail.value = false
    return
  }
  if (raw === COMP_QUERY_SUBJECT) {
    openSubjectDetail()
    return
  }
  if (openCompDetail(raw)) return
  focusedComp.value = raw
  subjectDetail.value = false
  detailComp.value = { name: raw }
  showCompDetail.value = true
}

watch(
  () => [showCompDetail.value, subjectDetail.value, detailComp.value?.name],
  writeCompQuery,
)
watch(() => [route.query.comp, data.value], applyCompQuery)

/** Point Street View at this house and show it. null = the subject. */
function openStreetView(name) {
  focusedComp.value = name || null
  showCompDetail.value = false
  showStreet.value = true
}

/**
 * Open the SUBJECT in the same gallery the comps use.
 *
 * Shaped into a comp-like row so `CompDetailModal` needs no second layout: the
 * modal switches only which endpoint it calls. Facts come from the subject's
 * `*_exact` numbers where we have them and are left blank where we do not --
 * feeding it a band midpoint would put invented precision on the one property
 * everything else is measured against.
 */
function openSubjectDetail() {
  const s = data.value?.subject
  if (!s) return
  focusedComp.value = null
  subjectDetail.value = true
  detailComp.value = {
    name: `subject::${props.lead}`,
    address: data.value?.address || props.address || '',
    lat: s.lat,
    lng: s.lng,
    bedrooms: s.beds_exact ? s.beds : null,
    bathrooms: s.baths_exact ? s.baths : null,
    square_footage: s.sqft_exact ? s.sqft : null,
    lot_size: s.lot_size || null,
    year_built: s.year_built,
    property_type: s.property_type,
    price: s.last_sale?.price || null,
    status: '',
    listing_state: '',
    distance_mi: 0,
    is_subject: true,
  }
  showCompDetail.value = true
}

function fmtInt(v) {
  if (v === '' || v == null) return ''
  const n = Math.round(Number(String(v).replace(/[^0-9-]/g, '')))
  return Number.isFinite(n) ? String(n) : ''
}

function typeFilter(key, e) {
  const raw = String(e.target.value).replace(/\D/g, '')
  draft[key] = raw === '' ? '' : parseInt(raw, 10)
  nextTick(() => {
    e.target.value = fmtInt(draft[key])
  })
}

/** Draft -> the server's filter shape. Blank means "unconstrained", not zero. */
function currentFilters() {
  const f = { status: draft.status || 'all', radius_mi: radius.value }
  if (isSet(draft.within_days)) f.within_days = Number(draft.within_days)
  if (isSet(draft.property_types)) f.property_types = [draft.property_types]
  for (const k of RANGE_KEYS) {
    const v = draft[k]
    if (v !== '' && v != null && Number.isFinite(Number(v))) f[k] = Math.round(Number(v))
  }
  return f
}

/** Server -> draft, so the controls always show what actually ran. */
function syncDraft(f) {
  syncing = true
  draft.status = f?.status || 'all'
  draft.within_days = f?.within_days ?? ANY
  const types = f?.property_types
  const type = Array.isArray(types) ? types[0] : types
  draft.property_types = type || ANY
  for (const k of RANGE_KEYS) {
    const v = f?.[k]
    draft[k] = v == null || v === '' ? '' : Math.round(Number(v))
  }
  // Watchers flush before nextTick callbacks, so this releases only after the
  // deep watcher has seen (and ignored) our own programmatic write.
  nextTick(() => {
    syncing = false
  })
}

function pinZ(c) {
  const fresh = Math.round(pillOpacity(stalenessDays(c)) * 100)
  return (c.selected ? 600 : isActive(c.status) ? 200 : 100) + fresh
}

/** Put or restyle one pin. Does NOT touch the map view. */
function placePin(c) {
  if (!map || c.lat == null || c.lng == null) return
  let marker = markersByName.get(c.name)
  if (!marker) {
    marker = L.marker([c.lat, c.lng]).addTo(map)
    marker.on('click', (ev) => {
      const t = ev.originalEvent?.target
      if (t?.closest?.('[data-comp-hide],[data-comp-unuse],[data-comp-use]')) return
      focusedComp.value = c.name
      openCompDetail(c.name)
    })
    marker.on('mouseover', () => hoverFromMap(c.name))
    marker.on('mouseout', () => {
      if (hoveredComp.value === c.name) hoveredComp.value = null
    })
    markersByName.set(c.name, marker)
  }
  marker.setIcon(pillIcon(c))
  // restZ, not pinZ: a restyle (use / discard / filter change) must not drop a
  // pill the rep has already pulled to the front back under its neighbours.
  marker.setZIndexOffset(hoveredComp.value === c.name ? HOVER_Z : restZ(c.name))
  // `setIcon` REPLACES the element, so the hover class goes with it. The z-index
  // was already being restored on the line above; the highlight was not, so a
  // pill under the pointer went flat the moment anything restyled the board.
  if (hoveredComp.value === c.name) {
    marker.getElement()?.classList.add('comps-pill-hot')
  }
  const badge = marker.getElement()?.querySelector('.comps-pill-x')
  if (badge) {
    L.DomEvent.disableClickPropagation(badge)
    L.DomEvent.on(badge, 'mousedown', L.DomEvent.stop)
    L.DomEvent.on(badge, 'touchstart', L.DomEvent.stop)
  }
}

function dropPin(name) {
  const marker = markersByName.get(name)
  if (!marker || !map) return
  map.removeLayer(marker)
  markersByName.delete(name)
}

/**
 * Flip selected / hidden on the in-memory board and restyle that one pin.
 * Replacing the arrays (not mutating a field) is what makes `selectedComps`
 * recompute; Vue will not notice `row.selected = true` through a computed that
 * only depends on the array identity.
 */
function applyCompState(name, state) {
  if (!data.value) return
  const list = data.value.comps || []
  const disc = data.value.discarded || []
  const row = list.find((c) => c.name === name) || disc.find((c) => c.name === name)
  if (!row) return
  const next = {
    ...row,
    selected: state === 'selected',
    hidden: state === 'hidden',
  }
  if (state === 'hidden') {
    data.value.comps = list.filter((c) => c.name !== name)
    data.value.discarded = disc.some((c) => c.name === name)
      ? disc.map((c) => (c.name === name ? next : c))
      : [...disc, next]
    dropPin(name)
  } else {
    data.value.comps = list.some((c) => c.name === name)
      ? list.map((c) => (c.name === name ? next : c))
      : [...list, next]
    data.value.discarded = disc.filter((c) => c.name !== name)
    placePin(next)
  }
  data.value.selected_count = data.value.comps.filter((c) => c.selected).length
  if (detailComp.value?.name === name) detailComp.value = next
}

/**
 * Mark a comp as one we are pricing off, or hide it. Team-wide by design.
 *
 * The map is NOT rebuilt. Adding a comp used to `load()` → `render()`, which
 * tore Leaflet down and put it back — that's the jump. The server write is
 * fire-and-forget against local state; a failure reloads to undo.
 */
const addCompText = ref('')
const addingComp = ref(false)
async function addCompFromText() {
  const text = addCompText.value.trim()
  if (!text || addingComp.value || !props.lead) return
  addingComp.value = true
  try {
    const res = await call('crm.api.comps.add_comp', { lead: props.lead, text })
    if (res?.ok === false) {
      toast.error(__('Comp selection is not set up on this site yet.'))
      return
    }
    addCompText.value = ''
    toast.success(res?.created === false ? __('Already in the pool — added to the table') : __('Added as a comp'))
    await load()
  } catch (e) {
    toast.error(e.messages?.[0] || __('Could not add that property as a comp.'))
  } finally {
    addingComp.value = false
  }
}

const practiceSeed = computed(() => data.value?.offer || null)
const isPractice = computed(() => Boolean(props.practiceAttempt && props.practiceProperty))
// A scratch CRM Property: same map, same picks and calc, but nothing that only
// makes sense on a LEAD — no underwriting sheet, no live-one ping to the closer.
const isScratch = computed(() => isScratchSubject(props.lead))

async function setCompState(comp, state) {
  if (!props.lead || !comp) return
  if (data.value?.practice_locked) {
    toast.error(__('This practice run is finished.'))
    return
  }
  applyCompState(comp, state)
  try {
    const res = isPractice.value
      ? await call('crm.api.practice.set_comp_state', {
          attempt: props.practiceAttempt,
          property: props.practiceProperty,
          comp,
          state,
        })
      : await call('crm.api.comps.set_comp_state', {
          lead: props.lead,
          comp,
          state,
        })
    if (res?.ok === false) {
      toast.error(__('Comp selection is not set up on this site yet.'))
      await load()
      return
    }
    if (state === 'hidden') {
      toast.success(__('Comp discarded'))
      showDiscarded.value = true
    }
  } catch (e) {
    toast.error(e.messages?.[0] || __('Could not update that comp.'))
    await load()
  }
}

function toggleUse(name) {
  const c = comps.value.find((x) => x.name === name) || discarded.value.find((x) => x.name === name)
  const picking = !c?.selected
  setCompState(name, picking ? 'selected' : 'none')
  // Ask what KIND of comp this is at the moment it is added (Lance's ask) --
  // the rep has just looked at the photos, which is when they know. Only
  // when the site can store it and nothing is tagged yet; "Skip for now"
  // keeps the pick and the chip on the card takes the tag later.
  if (picking && canTagTypes.value && !c?.comp_type && !isRentalComp(c)) {
    conditionComp.value = c
    showConditionModal.value = true
  }
}

const showConditionModal = ref(false)
const conditionComp = ref(null)

// Team-wide writes, so off in practice mode; `types_supported` is false on a
// site the ops script has not reached, where a stored tag still renders read-only.
const canTagTypes = computed(
  () => !isPractice.value && data.value?.types_supported !== false,
)

/** Optimistic in-memory tag, same immutable-replace pattern as applyCompState. */
function applyCompType(name, type) {
  if (!data.value) return
  const next = (list) =>
    (list || []).map((c) => (c.name === name ? { ...c, comp_type: type || null } : c))
  data.value.comps = next(data.value.comps)
  data.value.discarded = next(data.value.discarded)
  if (detailComp.value?.name === name) {
    detailComp.value = { ...detailComp.value, comp_type: type || null }
  }
}

async function setCompType(name, type) {
  if (!props.lead || !name || isPractice.value) return
  applyCompType(name, type)
  try {
    const res = await call('crm.api.comps.set_comp_type', {
      lead: props.lead,
      comp: name,
      comp_type: type || '',
    })
    if (res?.ok === false) {
      toast.error(__('Comp condition tags are not set up on this site yet.'))
      await load()
    }
  } catch (e) {
    toast.error(e.messages?.[0] || __('Could not tag that comp.'))
    await load()
  }
}

/**
 * Save (or clear, with null) the subject's manual square footage, then reload:
 * the ladder's tolerances, the ± deltas and the repair totals are all derived
 * server-side from the subject facts, so a full re-derive is the point.
 */
async function saveSubjectSqft(sqft) {
  if (!props.lead || isPractice.value) return
  try {
    const res = await call('crm.api.comps.set_subject_sqft', {
      lead: props.lead,
      sqft: sqft == null ? '' : sqft,
    })
    if (res?.ok === false) {
      toast.error(__('Manual square footage is not set up on this site yet.'))
      return
    }
    toast.success(sqft ? __('Square footage updated') : __('Back to Zillow/listing square footage'))
    await load()
  } catch (e) {
    toast.error(e.messages?.[0] || __('Could not update the square footage.'))
  }
}

let loadGen = 0
async function load({ explicit = userTouched.value } = {}) {
  if (!props.lead) return
  const gen = ++loadGen
  const want = inventory.value
  loading.value = true
  try {
    // Loop, not recurse: a `return load()` still runs this try's `finally`,
    // which would flip `loading` off while the inner call is in flight.
    for (;;) {
      if (gen !== loadGen) return
      const payload = {
        radius_mi: radius.value,
        include_hidden: 1,
        inventory: want,
      }
      if (explicit) {
        payload.filters = JSON.stringify(currentFilters())
        payload.auto = 0
      } else {
        payload.auto = 1
      }
      const nextData = isPractice.value
        ? await call('crm.api.practice.get_comps', {
            ...payload,
            attempt: props.practiceAttempt,
            property: props.practiceProperty,
          })
        : await call('crm.api.comps.get_lead_comps', {
            ...payload,
            lead: props.lead,
          })
      if (gen !== loadGen) return
      data.value = nextData
      emit('subject', data.value?.subject || null)
      emit('zillowMatch', data.value?.zillow_match || null)
      syncDraft(data.value?.filters)
      // Auto-widen only on the suggested path. A rep who picked ½ mile and got
      // three houses meant three houses; walking out from under them is the same
      // silent rewrite as loosening a filter they typed.
      if (explicit || userTouched.value) break
      const n = data.value?.total_matched ?? 0
      const next = RADIUS_STEPS.find((r) => r > Number(radius.value))
      if (n >= MIN_FOR_RADIUS || next == null) break
      wideningRadius = true
      radius.value = next
      wideningRadius = false
    }
    await nextTick()
    render()
    if (detailComp.value) {
      const next = comps.value.find((c) => c.name === detailComp.value.name)
      if (next) detailComp.value = next
    }
  } catch (e) {
    if (gen === loadGen) toast.error(e.messages?.[0] || __('Could not load comps.'))
  } finally {
    if (gen === loadGen) loading.value = false
  }
}

function resetToSuggested() {
  userTouched.value = false
  load({ explicit: false })
}

function setInventory(next) {
  if (inventory.value === next) return
  inventory.value = next
  userTouched.value = false
  load({ explicit: false })
}

function onCalcKind(k) {
  const next = k === 'rental' ? 'rentals' : 'sale'
  setInventory(next)
}

const MAX_SHEET_COMPS = 4
const creatingSheet = ref(false)
const selectedNames = computed(() => comps.value.filter((c) => c.selected).map((c) => c.name))
const selectedComps = computed(() => comps.value.filter((c) => c.selected))

const zillowLine = computed(() => {
  const z = data.value?.zillow
  if (!z || z.reason === 'error' || z.reason === 'not_configured') return ''
  const bits = []
  if (z.added) bits.push(__('+{0} from Zillow', [z.added]))
  if (z.updated) bits.push(__('{0} refreshed', [z.updated]))
  return bits.join(', ')
})

// Hand the chosen comps to whoever is hosting us, so a host rail can price off
// exactly what the rep ticked. Selection already persists team-wide on the lead;
// this just avoids the host re-deriving it and the two disagreeing about which
// comps produced a number somebody said out loud on a call.
watch(
  () => comps.value.filter((c) => c.selected),
  (picked) => emit('picked', picked),
  { deep: true, immediate: true },
)

// Short, because it shares one line with the address and the count summary and
// used to take ~430px of it to say nothing. The disabled state plus the tooltip
// already carry "you have to pick some first"; the count carries the rest.
const underwritingLabel = computed(() => {
  const n = selectedNames.value.length
  if (!n) return __('Underwrite')
  return __('Underwrite {0}/{1}', [Math.min(n, MAX_SHEET_COMPS), MAX_SHEET_COMPS])
})
const underwritingTitle = computed(() =>
  selectedNames.value.length
    ? __('Build an underwriting sheet from the comps you picked (up to {0})', [MAX_SHEET_COMPS])
    : __('Pick up to {0} comps with + first, then build an underwriting sheet', [MAX_SHEET_COMPS]),
)

/**
 * Send the chosen comps to a NEW underwriting sheet.
 *
 * Always a new sheet, never an edit of an existing one: a colleague may already
 * have comps in theirs, and silently overwriting their work is the one outcome
 * worth a few cents of Drive storage to avoid.
 */
async function createUnderwriting() {
  const picked = selectedNames.value
  if (!picked.length) return
  if (picked.length > MAX_SHEET_COMPS) {
    toast.error(
      __('Pick at most {0} comps — you have {1} selected.', [MAX_SHEET_COMPS, picked.length]),
    )
    return
  }
  creatingSheet.value = true
  try {
    const res = await call('crm.api.underwriting.create_underwriting_from_comps', {
      lead: props.lead,
      comps: JSON.stringify(picked),
    })
    // Name the shortfall rather than opening a sheet with quietly missing rows:
    // the sheet's formulas need a Zillow homedetails URL and not every address
    // resolves to one.
    if (res?.unresolved?.length) {
      toast.warning(
        __("{0} comp(s) couldn't be linked to Zillow and were left out", [
          res.unresolved.length,
        ]),
      )
    }
    toast.success(
      res?.sheet_number > 1
        ? __('Created underwriting sheet #{0} with {1} comps', [
            res.sheet_number,
            res.comps_written,
          ])
        : __('Created underwriting sheet with {0} comps', [res?.comps_written ?? 0]),
    )
    if (res?.sheet_url) window.open(res.sheet_url, '_blank', 'noopener')
  } catch (e) {
    toast.error(e.messages?.[0] || __('Could not create the underwriting sheet.'))
  } finally {
    creatingSheet.value = false
  }
}

function clearAll() {
  syncing = true
  draft.status = 'all'
  draft.within_days = ANY
  draft.property_types = ANY
  for (const k of RANGE_KEYS) draft[k] = ''
  nextTick(() => {
    syncing = false
  })
  // Deliberately explicit: "clear" means show everything, not "go back to the
  // suggestion", which is what the neighbouring Reset button is for.
  userTouched.value = true
  load({ explicit: true })
}

// Watch the value rather than binding @change on the control: frappe-ui renders
// `type="select"` as a button-driven dropdown, not a native <select>, so a
// `change` event is not guaranteed to reach us. Watching v-model works whichever
// way the control chooses to emit.
watch(draft, () => {
  if (syncing || !show.value) return
  userTouched.value = true
  clearTimeout(applyTimer)
  // Debounced: typing "1400" into a min box is four keystrokes, not four queries.
  applyTimer = setTimeout(() => load({ explicit: true }), 300)
}, { deep: true })

watch(radius, () => {
  if (wideningRadius || !show.value) return
  userTouched.value = true
  load()
})

watch(
  () => props.lead,
  () => {
    userTouched.value = false
    lastFitKey = ''
    // Which pills were pulled to the front is a fact about the board you were
    // just looking at, not about this one.
    raisedComps.clear()
    wideningRadius = true
    radius.value = 0.5
    wideningRadius = false
  },
)

watch(show, (v) => {
  if (v) {
    // Every open starts from the suggestion again: the filters describe THIS
    // property, and a stale set carried over from the last lead would be wrong.
    userTouched.value = false
    nextTick(() => load({ explicit: false }))
  } else if (map) {
    unbindParcels()
    map.remove()
    map = null
  }
})

// GOTCHA — useKeyboardShortcuts defaults to skipWhenDialogOpen:true, and this IS
// a Dialog, so the shortcuts would silently never fire. It is turned off here and
// the modal's own `show` gates them instead. `ignoreTyping` (on by default) is
// what stops "d" toggling pills while someone types in a filter box.
//
// Because that opt-out is blanket, the photo gallery has to be excluded by hand:
// it is a Dialog stacked on top of this one, and without the guard `h` would hide
// the very comp whose photos the user is looking at.
useKeyboardShortcuts({
  // The condition prompt is a Dialog too: with it open, `U` on the pin popup
  // would un-pick the comp being asked about.
  active: () => !!show.value && !showCompDetail.value && !showConditionModal.value,
  skipWhenDialogOpen: false,
  shortcuts: [
    { keys: ['d', 'D'], action: () => (showDetail.value = !showDetail.value) },
    { keys: ['p', 'P'], action: () => (showParcels.value = !showParcels.value) },
    { keys: ['s', 'S'], action: () => (showStreet.value = !showStreet.value) },
    // Hide tray + calculator together. Browser Find keeps Cmd/Ctrl+F; only a
    // bare F owns this action. `C` still folds only the calc.
    {
      keys: ['f', 'F'],
      guard: (e) => !e.metaKey && !e.ctrlKey && !e.altKey,
      action: () => toggleCompsFocusMap(),
    },
    // Only where the calculator exists, so `c` stays free elsewhere.
    // No-op in full-map: the calc is not on screen, and flipping the pref
    // behind the overlay would surprise on the way back out.
    {
      keys: ['c', 'C'],
      guard: (e) => !e.metaKey && !e.ctrlKey && !e.altKey,
      action: () =>
        props.pageMode &&
        !compsFocusMap.value &&
        (calcOpen.value = !calcOpen.value),
    },
    // Only where the layer is offered, so `n` stays free on the comps page.
    { keys: ['n', 'N'], action: () => props.neighborhood && toggleHood() },
    {
      keys: ['h', 'H'],
      action: () => focusedComp.value && setCompState(focusedComp.value, 'hidden'),
    },
    { keys: ['u', 'U'], action: () => focusedComp.value && toggleUse(focusedComp.value) },
    {
      keys: ['l', 'L'],
      action: () => focusedComp.value && copyCompLink(focusedComp.value),
    },
  ],
})

// If this ever mounts with `show` already true (a v-if host, or a hot reload),
// the watcher above never fires and the map would sit empty claiming "no comps".
onMounted(() => {
  compsViewCount.value += 1
  if (rootEl.value) {
    rootObserver = new ResizeObserver((entries) => {
      const w = entries[0]?.contentRect?.width || 0
      // Defer a frame, or this trips "ResizeObserver loop completed with
      // undelivered notifications" -- changing the layout resizes the root.
      requestAnimationFrame(() => {
        if (w) wide.value = w >= SPLIT_MIN_WIDTH
      })
    })
    rootObserver.observe(rootEl.value)
  }
  if (show.value) nextTick(() => load({ explicit: false }))
  observeMapSize()
  mapEl.value?.addEventListener('wheel', forwardWheel, { passive: false })
})

onBeforeUnmount(() => {
  compsViewCount.value = Math.max(0, compsViewCount.value - 1)
  if (streetViewTimer) {
    clearTimeout(streetViewTimer)
    streetViewTimer = null
  }
  rootObserver?.disconnect()
  sizeObserver?.disconnect()
  sizeObserver = null
  mapEl.value?.removeEventListener('wheel', forwardWheel)
  if (map) {
    unbindParcels()
    map.remove()
    map = null
  }
})

/**
 * Scroll the nearest ancestor when the wheel is used over the map.
 *
 * Leaflet's container is `overflow:hidden` AND genuinely overflows (transformed
 * map pane + pills past the edge), which makes it a scroll CONTAINER. Chrome
 * does not chain wheel scrolling out of one — the same mechanism as
 * `body { overflow: hidden }`. `scrollWheelZoom` is off by design, so the wheel
 * is ours to forward. preventDefault only when we actually moved, so hitting
 * the end of a list still chains outwards.
 */
function forwardWheel(e) {
  if (e.ctrlKey) return
  const host = scrollHost(mapEl.value)
  if (!host) return
  const unit = e.deltaMode === 1 ? 16 : e.deltaMode === 2 ? host.clientHeight : 1
  const before = host.scrollTop
  host.scrollTop = before + e.deltaY * unit
  if (host.scrollTop !== before) e.preventDefault()
}

function scrollHost(el) {
  let node = el?.parentElement
  while (node && node !== document.body) {
    const oy = getComputedStyle(node).overflowY
    if ((oy === 'auto' || oy === 'scroll') && node.scrollHeight > node.clientHeight)
      return node
    node = node.parentElement
  }
  const doc = document.scrollingElement
  return doc && doc.scrollHeight > doc.clientHeight ? doc : null
}

/**
 * Re-measure the map whenever its container changes size.
 *
 * Leaflet measures once, at init, and a container that was display:none measures
 * 0x0 -- so a host that keeps this mounted behind a hidden tab (the Today lead
 * modal) gets tiles crammed into the corner the first time the tab is opened.
 * The one-shot `invalidateSize` after render() only covers the case where the
 * container becomes visible within 120ms of loading, which a tab does not.
 *
 * Observing the element means every show, hide and window resize fixes itself
 * and no host has to remember to tell us it revealed the map.
 */
defineExpose({
  saveCalcs: () => calcRef.value?.save?.() || Promise.resolve(),
})

function observeMapSize() {
  if (!mapEl.value || typeof ResizeObserver === 'undefined') return
  sizeObserver = new ResizeObserver(() => {
    // Deferred a frame: calling back into layout from inside the callback is what
    // produces "ResizeObserver loop completed with undelivered notifications".
    requestAnimationFrame(() => map && map.invalidateSize())
  })
  sizeObserver.observe(mapEl.value)
}
</script>

<style>
/* Kill Leaflet's default white chrome on divIcons so the pills sit clean. */
.comps-price-pill {
  background: transparent;
  border: 0;
}

/* The remove-✕ is hover-only: 200 pins each wearing a permanent ✕ would be
   louder than the prices they exist to show. */
.comps-pill-x {
  display: none;
}
.comps-price-pill:hover .comps-pill-x {
  display: block;
}
/* Touch sticks :hover, so the add/remove chip sat on every pill you had
   tapped. Compact (phone) inspects via the detail modal, not the chip. */
[data-compact='1'] .comps-pill-x {
  display: none !important;
}

/* Number boxes are sized in px (this app's rem is 20px, so w-12 is 60px and
   chops "2620"). Kill the spinner — it ate the last digit — and let the value
   use the whole box. */
/* Colours come from the theme's CSS variables, NOT hex. These inputs sit in the
   app chrome rather than on the map, so in dark mode a hardcoded `#fff` stayed
   glaring white with near-black text -- which is exactly what "dark mode isn't
   working for comps" meant. The variables are redefined under
   `[data-theme=dark]`, so this now follows the theme with no JS and no `dark:`
   variants.

   The map's own pills and popups deliberately keep their hex: they sit on
   OpenStreetMap tiles, which are light in either theme, so darkening them would
   make them harder to read, not easier. */
input.comps-filter-num {
  flex: none;
  box-sizing: border-box;
  height: 26px;
  min-width: 0;
  border: 1px solid var(--outline-gray-2);
  border-radius: 6px;
  background: var(--surface-white);
  padding: 0 6px;
  /* inherit is 20px here (app rem). The selects next door are ~13. */
  font: 13px/1.2 InterVar, Inter, -apple-system, 'Segoe UI', system-ui, sans-serif;
  font-variant-numeric: tabular-nums;
  color: var(--ink-gray-9);
  text-align: right;
}
input.comps-filter-num::placeholder {
  color: var(--ink-gray-4);
}
.comps-filter-num input::-webkit-outer-spin-button,
.comps-filter-num input::-webkit-inner-spin-button {
  -webkit-appearance: none;
  margin: 0;
}

/* Hovering the row (or the pin) outlines the matching pill and brings it to full
   strength; the JS also lifts its z-index so it is never half-buried.

   NOTE: a `transform: scale()` was tried here first and is deliberately gone --
   it silently does nothing on a Leaflet divIcon child. Verified directly: with
   the rule matching (its `outline` applied fine) the computed transform stayed
   `matrix(1,0,0,1,0,0)` at 600ms, and even setting `style.transform` inline on
   the element did not take. Outline + opacity are what actually render, so they
   are what the highlight is built from. */
.comps-price-pill.comps-pill-hot .comps-pill-body {
  /* The fade lives in the FILL's alpha channel, not in `opacity` -- the pill's
     inline opacity is always 1. So `opacity: 1 !important` here was a no-op and
     a hovered old comp stayed washed out. This restores the pill to its solid
     colour instead, which is what "highlight" was always meant to mean.
     `!important` is what lets a stylesheet beat the inline background. */
  background: var(--pill-bg-solid) !important;
  border-color: var(--pill-border-solid) !important;
  outline: 3px solid #161614;
  outline-offset: 2px;
}
.comps-pill-body {
  /* Transition the property that actually changes. Was `opacity`, which no
     longer moves, so the highlight also snapped rather than easing. */
  transition: background-color 90ms ease-out, border-color 90ms ease-out;
  overflow: visible;
}
.comps-type-glyph svg {
  display: block;
  overflow: visible;
}
</style>
