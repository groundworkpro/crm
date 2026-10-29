<template>
  <!-- ONE column by default; "+ Compare" adds the second. Two calculators
       side by side is the exception, not the norm, and the second column was
       costing ~5rem of width on a surface whose whole job is the map.

       The columns stay independent, like the sheet: shared-looking defaults
       (both ARVs empty, both $25k fee) but typing in one never writes the
       other — no merged cells. -->
  <div class="wrap">
  <div
    class="calc"
    :style="{
      width: '100%',
      maxWidth: compact ? 'none' : cols === 2 ? '30rem' : '21rem',
    }"
    @keydown="onKeys"
  >
    <div class="head">
      <div class="seg kind" role="group" :aria-label="__('Offer type')">
        <button
          type="button"
          :class="{ on: kind === 'cash' }"
          :title="__('Exit Price × % − repairs − profit')"
          @click="setKind('cash')"
        >
          {{ __('Cash') }}
        </button>
        <button
          type="button"
          :class="{ on: kind === 'auction' }"
          :title="__('Exit Price × % − repairs − liens − back taxes − profit')"
          @click="setKind('auction')"
        >
          {{ __('Auction') }}
        </button>
        <button
          type="button"
          :class="{ on: kind === 'novation' }"
          :title="__('Current value − 10% − profit')"
          @click="setKind('novation')"
        >
          {{ __('Novation') }}
        </button>
        <button
          type="button"
          :class="{ on: kind === 'wholetail' }"
          :title="__('Novation plus capital and holding costs for the hold')"
          @click="setKind('wholetail')"
        >
          {{ __('Wholetail') }}
        </button>
        <button
          type="button"
          :class="{ on: kind === 'list' }"
          :title="__('As-is − 6% commission − 2% closing − 2% concessions')"
          @click="setKind('list')"
        >
          {{ __('List it') }}
        </button>
        <button
          type="button"
          :class="{ on: kind === 'rental' }"
          :title="__('MIR × 80% − repairs − profit')"
          @click="setKind('rental')"
        >
          {{ __('Rental') }}
        </button>
      </div>
      <span class="head-r">
        <button
          v-if="cols === 1"
          type="button"
          class="link"
          :title="__('Run a second set of numbers beside this one')"
          @click="addCompare"
        >
          {{ __('+ Compare') }}
        </button>
        <button
          type="button"
          class="save"
          :disabled="!canSave || saving"
          @click="save"
        >
          {{ saving ? __('Saving…') : __('Save calcs') }}
        </button>
        <slot name="head-end" />
      </span>
    </div>

    <div
      class="grid"
      :style="{ gridTemplateColumns: cols === 2 ? '6.5rem 1fr 1fr' : '6.5rem 1fr' }"
    >
      <template v-if="cols === 2">
        <span />
        <span class="colh">{{ __('Scenario 1') }}</span>
        <span class="colh">
          {{ __('Scenario 2') }}
          <button
            type="button"
            class="drop"
            :title="__('Drop this scenario')"
            @click="cols = 1"
          >
            ×
          </button>
        </span>
      </template>

      <!-- The formula comes first because it frames every number under it.
           Per column, like everything else here: "+ Compare" then puts the two
           formulas side by side on the same house, which is the comparison a
           wholesaler actually wants. Cash-only: novation has one shape. -->
      <template v-if="kind === 'cash' || kind === 'auction'">
        <span class="lab">{{ __('Formula') }}</span>
        <div v-for="col in visible" :key="'form' + col" class="seg">
          <button
            v-for="f in FORMULAS"
            :key="f.key"
            type="button"
            :class="{ on: multOf(col) === f.mult }"
            :title="f.why"
            @click="setFormula(col, f)"
          >
            {{ f.label }}
          </button>
        </div>
      </template>

      <span class="lab">{{ valueLabel }}</span>
      <input
        v-for="col in visible"
        :key="'arv' + col"
        :ref="(el) => setField(0, col, el)"
        :class="{ empty: !s[col].arv }"
        inputmode="numeric"
        :placeholder="arvHint"
        :value="s[col].arv ? money(s[col].arv) : ''"
        @focus="$event.target.select()"
        @input="typeMoney(col, 'arv', $event)"
      />

      <template v-if="kind === 'list'">
        <template v-for="cut in LIST_CUTS" :key="cut.key">
          <span class="lab" :title="cut.why">{{ cut.label }}</span>
          <div v-for="col in visible" :key="cut.key + col" class="cut">
            <label class="pct">
              <input
                :ref="(el) => setField(cut.row, col, el)"
                inputmode="numeric"
                :value="Math.round((s[col][cut.key] || 0) * 100)"
                @focus="$event.target.select()"
                @change="setRate(col, cut.key, $event)"
              />
              <i>%</i>
            </label>
            <span class="out">
              {{ s[col].arv ? '−' + money(run(col)[cut.result]) : '—' }}
            </span>
          </div>
        </template>
      </template>
      <template v-else>
      <span class="lab">
        {{ pctLabel }}
        <button
          v-if="kind === 'novation' || kind === 'wholetail'"
          type="button"
          class="why"
          tabindex="-1"
          :aria-label="NOVATION_PCT_WHY"
        >
          ?
          <span class="why-tip">
            <b>6%</b> {{ __('Realtors') }}<br />
            <b>2%</b> {{ __('closing costs') }}<br />
            <b>2%</b> {{ __('minor repairs') }}
          </span>
        </button>
      </span>
      <label v-for="col in visible" :key="'pct' + col" class="pct">
        <input
          :ref="(el) => setField(1, col, el)"
          inputmode="numeric"
          :value="Math.round(s[col].pct * 100)"
          @focus="$event.target.select()"
          @change="setPct(col, $event)"
        />
        <i>%</i>
      </label>

      <span class="lab">{{ __('After %') }}</span>
      <input
        v-for="col in visible"
        :key="'after' + col"
        :ref="(el) => setField(2, col, el)"
        inputmode="numeric"
        :value="s[col].arv ? money(run(col).after) : ''"
        @focus="$event.target.select()"
        @input="typeAfter(col, $event)"
      />
      </template>

      <template v-if="kind === 'wholetail'">
      <span class="lab">
        {{ __('Rate / hold') }}
        <button type="button" class="why" tabindex="-1" :aria-label="CAPITAL_WHY">
          ?
          <span class="why-tip">{{ CAPITAL_WHY }}</span>
        </button>
      </span>
      <div v-for="col in visible" :key="'hold' + col" class="hold">
        <label class="pct">
          <input
            :ref="(el) => setField(9, col, el)"
            inputmode="numeric"
            :value="Math.round((s[col].interestPct || 0) * 100)"
            @focus="$event.target.select()"
            @change="setInterest(col, $event)"
          />
          <i>%</i>
        </label>
        <label class="pct">
          <input
            :ref="(el) => setField(10, col, el)"
            inputmode="numeric"
            :value="s[col].holdMonths"
            @focus="$event.target.select()"
            @change="setHoldMonths(col, $event)"
          />
          <i>{{ __('mo') }}</i>
        </label>
      </div>
      <span class="lab">{{ __('Capital') }}</span>
      <span v-for="col in visible" :key="'cap' + col" class="out">
        {{ s[col].arv ? '−' + money(run(col).capital) : '—' }}
      </span>
      <span class="lab">
        {{ __('Holding') }}
        <button type="button" class="why" tabindex="-1" :aria-label="HOLDING_WHY">
          ?
          <span class="why-tip">
            <b>1.5%/yr</b> {{ __('of value (tax + insurance)') }}<br />
            <b>$200/mo</b> {{ __('utilities / vacant') }}
          </span>
        </button>
      </span>
      <span v-for="col in visible" :key="'hld' + col" class="out">
        {{ s[col].arv ? '−' + money(run(col).holding) : '—' }}
      </span>
      </template>

      <!-- Repairs is a NAMED choice, not a number to invent. The rep is on the
           phone with a seller describing a house; "kitchen & baths" is a thing
           they can hear, "$30/sf" is not. Each row carries what that level
           costs on THIS house so the ladder has a felt price, not just a rate.
           "Other…" keeps the raw $/sf field one click away — the preset is a
           shortcut, never a cage. Novation lists the house as-is, so none of
           this applies. -->
      <template v-if="kind === 'cash' || kind === 'rental' || kind === 'auction'">
      <span class="lab">{{ __('Condition') }}</span>
      <div v-for="col in visible" :key="'rep' + col" class="rep">
        <template v-if="isCustom(col)">
          <input
            :ref="(el) => setField(3, col, el)"
            class="rep-psf"
            inputmode="numeric"
            :value="psfText(s[col].rehabPsf)"
            @focus="$event.target.select()"
            @input="typeMoney(col, 'rehabPsf', $event)"
          />
          <button
            type="button"
            class="rep-caret"
            :title="__('Pick a repair level')"
            @click="toggleMenu(col)"
          >
            ▾
          </button>
          <span class="rep-sf">{{ __('/sf') }}</span>
        </template>
        <button
          v-else
          type="button"
          class="rep-btn"
          :class="{ open: menuFor === col }"
          @click="toggleMenu(col)"
        >
          <span class="rep-name">{{ tierFor(col).label }}</span>
          <span class="vals">
            <i>{{ psfText(s[col].rehabPsf) }}/sf</i><b v-if="sqft">
              ({{ k(s[col].rehabPsf * sqft) }})</b>
          </span>
          <span class="caret">▾</span>
        </button>

        <div v-if="menuFor === col" class="menu">
          <button
            v-for="t in TIERS"
            :key="t.key"
            type="button"
            class="mrow"
            :class="{ on: s[col].rehabPsf === t.psf }"
            @click="pickTier(col, t)"
          >
            <span>{{ t.label }}</span>
            <span class="vals">
              <i>{{ money(t.psf) }}/sf</i><b v-if="sqft">
                ({{ k(t.psf * sqft) }})</b>
            </span>
          </button>
          <div class="msep" />
          <button type="button" class="mrow other" @click="pickOther(col)">
            {{ __('Other…') }}
          </button>
        </div>
      </div>

      <!-- The repair BILL, and only ever the bill: what this house costs to fix
           is a fact about the house, so it must read the same whichever formula
           is running. Doubling is something a formula does to it, not something
           that happens to the number. -->
      <span class="lab">{{ __('Repairs') }} <i>{{ fmt(sqft) }} sf</i></span>
      <input
        v-for="col in visible"
        :key="'rehab' + col"
        :ref="(el) => setField(4, col, el)"
        inputmode="numeric"
        :value="money(run(col).repairs)"
        @focus="$event.target.select()"
        @input="typeRehab(col, $event)"
      />

      <!-- … and the doubling lands here, on the line it actually produces. This
           row is derived, so it can carry a derived figure without asking the
           rep to maintain one. Shown ONLY at 2×, i.e. only when the deduction
           differs from the repairs row above it and the column would otherwise
           stop adding up. -->
      <template v-if="kind === 'cash' || kind === 'auction'">
      <span class="lab">{{ __('Wholesale') }}</span>
      <span v-for="col in visible" :key="'ws' + col" class="out annot">
        <i
          v-if="s[col].arv && multOf(col) === 2"
          :title="__('2× repairs.') + ' ' + DOUBLE_WHY"
        >−{{ money(run(col).rehab) }}</i>
        {{ s[col].arv ? money(run(col).wholesale) : '—' }}
      </span>
      </template>
      </template>

      <template v-if="kind !== 'list'">
      <span class="lab" :title="__('Gross profit as a percent of what we pay')">{{ __('Profit') }}</span>
      <div v-for="col in visible" :key="'profit' + col" class="cut">
        <label class="pct">
          <input
            :ref="(el) => setField(5, col, el)"
            inputmode="numeric"
            :value="profitPctDisplay(col)"
            @focus="$event.target.select()"
            @change="setProfit(col, $event)"
          />
          <i>%</i>
        </label>
        <input
          :ref="(el) => setField(11, col, el)"
          inputmode="numeric"
          :value="money(run(col).fee)"
          @focus="$event.target.select()"
          @input="typeProfit(col, $event)"
        />
      </div>
      </template>

      <template v-if="kind === 'auction'">
      <span class="lab">{{ __('Liens') }}</span>
      <input
        v-for="col in visible"
        :key="'liens' + col"
        :ref="(el) => setField(6, col, el)"
        inputmode="numeric"
        :value="money(s[col].liens)"
        @focus="$event.target.select()"
        @input="typeMoney(col, 'liens', $event)"
      />
      <span class="lab">{{ __('Back taxes') }}</span>
      <input
        v-for="col in visible"
        :key="'tax' + col"
        :ref="(el) => setField(7, col, el)"
        inputmode="numeric"
        :value="money(s[col].backTaxes)"
        @focus="$event.target.select()"
        @input="typeMoney(col, 'backTaxes', $event)"
      />
      </template>

      <span class="lab offer">{{ offerLabel }}</span>
      <label
        v-for="col in visible"
        :key="'offer' + col"
        class="offer-in"
        :class="{ win: winner === col }"
      >
        <!-- Which one is bigger, and by how much. The gap is the whole reason
             anyone runs two of these, and eyeballing two five-figure numbers
             for a $800 difference is exactly the arithmetic this tool exists
             to stop doing in your head. -->
        <b v-if="winner === col" class="gap" :title="__('Higher of the two')">
          +{{ money(offerGap) }}
        </b>
        <input
          :ref="(el) => setField(kind === 'auction' ? 8 : 6, col, el)"
          class="offer"
          :class="{ bad: s[col].arv && run(col).offer <= 0 }"
          inputmode="numeric"
          :value="s[col].arv ? money(run(col).offer) : ''"
          @focus="$event.target.select()"
          @input="typeOffer(col, $event)"
        />
      </label>
    </div>

    <!-- With one column the comparison cannot be on screen, so the other
         formula reports itself rather than making the rep toggle, read, and
         toggle back holding a number in their head. Its OWN percentage, since
         that is part of what is being compared. Cash and auction share the
         Classic / 2× toggle, so both get the line. -->
    <div v-if="(kind === 'cash' || kind === 'auction') && altOffer !== null" class="alt">
      {{
        __('{0} would offer {1}', [altFormula.label, money(altOffer)])
      }}<template v-if="altOffer !== run(0).offer">
        —
        <b :class="altOffer > run(0).offer ? 'more' : 'less'">
          {{ money(Math.abs(altOffer - run(0).offer)) }}
          {{ altOffer > run(0).offer ? __('more') : __('less') }}
        </b>
      </template>
    </div>
    <p v-if="sellerNote" class="seller-note">
      <span class="seller-note-lbl">{{ __('Seller said') }}</span>
      {{ sellerNote }}
    </p>
    <textarea
      v-model="notes"
      class="notes"
      rows="3"
      :placeholder="__('Notes — condition, access, what you told the seller…')"
    />
  </div>

    <table v-if="!compact && (subjectRow || rows.length)" class="tbl">
      <thead>
        <tr>
          <th>{{ __('Address') }}</th>
          <th>{{ __('Sale / list') }}</th>
          <th class="n">{{ __('Dist') }}</th>
          <th class="n">{{ __('Sq ft') }}</th>
          <th class="n">{{ __('Price') }}</th>
          <th class="n">{{ __('$/sf') }}</th>
          <th class="n">{{ __('On this') }}</th>
          <th>{{ __('Status') }}</th>
          <th />
        </tr>
      </thead>
      <tbody>
        <tr v-if="subjectRow" class="subj">
          <td class="street" :title="subjectRow.address">{{ subjectRow.street }}</td>
          <td>{{ subjectRow.date }}</td>
          <td class="n">—</td>
          <td class="n">{{ subjectRow.sqft }}</td>
          <td class="n">{{ subjectRow.price }}</td>
          <td class="n">{{ subjectRow.psf }}</td>
          <td class="n">—</td>
          <td>{{ __('Subject') }}</td>
          <td />
        </tr>
        <tr v-for="r in rows" :key="r.name">
          <td class="street" :title="r.address">
            <button type="button" @click="$emit('open', r.name)">{{ r.street }}</button>
          </td>
          <td>{{ r.date }}</td>
          <td class="n">{{ r.mi }}</td>
          <td class="n">{{ r.sqft }}</td>
          <td class="n">{{ r.price }}</td>
          <td class="n">{{ r.psf }}</td>
          <td class="n">{{ r.onThis }}</td>
          <td>{{ r.status }}</td>
          <td class="rm">
            <button
              type="button"
              :title="__('Remove from table — stays on the map')"
              @click="removeComp(r.name)"
            >
              ×
            </button>
          </td>
        </tr>
        <tr class="avg">
          <td colspan="5">{{ __('Average $/sf') }}</td>
          <td class="n">{{ avgPsf ? money(avgPsf) : '—' }}</td>
          <td class="n">{{ suggestedArv ? money(suggestedArv) : '—' }}</td>
          <td />
          <td />
        </tr>
      </tbody>
    </table>
  </div>
</template>

<script setup>
/**
 * Offer calculator. Cash, novation, list-it, or rental — cash by default, one kind at a time.
 *
 * Cash has two formulas, because the desk uses both:
 *   2× repairs  ARV × 88% − 2×repairs − fee   (default; what OfferRail runs)
 *   Classic     ARV × 70% −   repairs − fee   (the 70% rule)
 * i.e. after = ARV × %, deduction = mult × $/sf × sqft, offer = after −
 * deduction − profit. Profit is a dollar takeoff ($25k default) so it still
 * shows when it is bigger than the offer; the % is derived (profit / offer)
 * and blank when the offer is not positive. Picking a formula sets its
 * canonical ARV %, and that % stays editable afterwards — the toggle owns
 * the SHAPE, the rep owns the number.
 *
 * Novation is Current value − 10% − fee ($40k default). No repairs: we list
 * the house as-is. The 10% stays editable the same way cash's % does.
 *
 * List it is as-is − 6% commission − 2% closing − 2% concessions. No fee, no
 * repairs: it is what the seller would take home if they listed themselves.
 * The three rates stay editable; the dollars and the takeaway are derived.
 *
 * Rental is MAO = MIR × 80% − repairs − fee (1× repairs, fee $25k default).
 * Switching to it tells the map to show ForRent listings; the other kinds
 * stay on for-sale/sold. MIR is not filled from rental pin rents.
 *
 * The kinds are separate notebooks: switching does not rewrite the
 * other kind's numbers or its comps. Each kind soft-fills its value from the
 * average of ITS comps table (same suggested-ARV the cash column has always
 * used), and every figure in the column is editable — derived rows back-solve.
 *
 * `mult` is stored, unlike the repair tier, because it genuinely cannot be
 * derived: a 70% column does not imply a single deduction once the rep is free
 * to edit the percentage. Anything saved before this existed has no `mult` and
 * is read as Classic — which is what those numbers meant when they were
 * written.
 *
 * One scenario by default; "+ Compare" opens a second one carrying whatever
 * the rep is already on, and each column keeps its own toggle. Seeding it with
 * the other formula was tried and removed: it decided for them what was being
 * compared, when the far more common comparison is the same formula at two
 * percentages. Each column is its own notebook.
 *
 * Repairs are picked by NAME off a four-rung ladder. There is deliberately no
 * stored `tier` field — the tier is DERIVED from the $/sf, so there is nothing
 * to drift, an old saved calc names itself, and typing 30 into "Other…" is the
 * same thing as picking Kitchen & baths. A $/sf that matches no rung simply
 * renders as the raw input it always was.
 */
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { call, toast } from 'frappe-ui'
import { isRentalComp, streetAddress } from '@/utils/comps'

function isRentalRow(c) {
  return isRentalComp(c)
}

const emit = defineEmits(['remove', 'open', 'saved', 'kind'])

const props = defineProps({
  lead: { type: String, required: true },
  subject: { type: Object, default: null },
  comps: { type: Array, default: () => [] },
  address: { type: String, default: '' },
  // Snapshot from a timeline comment. When present, this is the source of
  // truth for the columns (not localStorage) so "Tweak calcs" opens on
  // the numbers that were actually saved, not a later draft.
  seed: { type: Object, default: null },
  // Phone: no picked-comps spreadsheet. The map is the list; tapping a pin
  // opens the gallery. Same component as desktop so the numbers cannot drift.
  compact: { type: Boolean, default: false },
  // Practice run: save onto the attempt, and keep the draft off the real lead's
  // localStorage key so a practice calc cannot leak onto a live deal.
  practiceAttempt: { type: String, default: '' },
  practiceProperty: { type: String, default: '' },
  sellerNote: { type: String, default: '' },
  // Auction.com properties open on the auction notebook (liens line).
  preferKind: { type: String, default: '' },
})

// Matches OfferRail.vue's rail so the two surfaces cannot name the same
// formula differently. Order is display order: the default leads.
const FORMULAS = [
  {
    key: 'double',
    label: __('2× repairs'),
    pct: 0.88,
    mult: 2,
    why: __('Exit Price × 88% − 2× repairs − profit'),
  },
  {
    key: 'classic',
    label: __('Classic'),
    pct: 0.7,
    mult: 1,
    why: __('Exit Price × 70% − repairs − profit — the 70% rule'),
  },
]
// Same reason the desk rail gives, and for the same doubling.
const DOUBLE_WHY = __(
  'Doubled on purpose: a buffer that overruns is recoverable where an offer ' +
    'that was too high is not.',
)

const TIERS = [
  { key: 'paint', label: __('Paint & carpet'), psf: 10 },
  { key: 'kitchen', label: __('Kitchen & baths'), psf: 30 },
  { key: 'full', label: __('Full rehab'), psf: 50 },
  { key: 'studs', label: __('Down to studs'), psf: 75 },
]

// Kitchen & baths — the middle-low rung, and the one the ladder opens on so
// the control reads as a named condition rather than an unnamed number.
const DEFAULT_PSF = 30
const DEFAULT_FEE = 25000
const DEFAULT_PROFIT_PCT = 0.2
const DEFAULT_RENTAL_REPAIRS = 10000
const DEFAULT_NOVATION_FEE = 40000
const DEFAULT_NOVATION_PCT = 0.1
const DEFAULT_WHOLETAIL_RATE = 0.12
const DEFAULT_WHOLETAIL_MONTHS = 6
const HOLDING_TAX_INS_PCT = 0.015
const HOLDING_UTIL_MO = 200
const CAPITAL_WHY = __('Current value × interest × hold / 12')
const HOLDING_WHY = __('1.5%/yr of value (tax + insurance) + $200/mo utilities / vacant')
const DEFAULT_RENTAL_PCT = 0.8
const DEFAULT_COMMISSION_PCT = 0.06
const DEFAULT_CLOSING_PCT = 0.02
const DEFAULT_CONCESSIONS_PCT = 0.02
const NOVATION_PCT_WHY = [
  __('6% Realtors'),
  __('2% closing costs'),
  __('2% minor repairs'),
].join(' · ')
const LIST_CUTS = [
  {
    key: 'commissionPct',
    result: 'commission',
    row: 1,
    label: __('Commission'),
    why: __('Agent commission'),
  },
  {
    key: 'closingPct',
    result: 'closing',
    row: 2,
    label: __('Closing'),
    why: __('Closing costs'),
  },
  {
    key: 'concessionsPct',
    result: 'concessions',
    row: 3,
    label: __('Concessions'),
    why: __('Concessions and miscellaneous'),
  },
]
function asKind(k) {
  return k === 'novation' || k === 'wholetail' || k === 'list' || k === 'rental' || k === 'auction' ? k : 'cash'
}

function listRates() {
  return {
    commissionPct: DEFAULT_COMMISSION_PCT,
    closingPct: DEFAULT_CLOSING_PCT,
    concessionsPct: DEFAULT_CONCESSIONS_PCT,
  }
}

function fresh(k, f) {
  if (k === 'list') {
    return {
      arv: 0,
      pct: 0.1,
      mult: 1,
      rehabPsf: DEFAULT_PSF,
      fee: 0,
      profitPct: DEFAULT_PROFIT_PCT,
      ...listRates(),
    }
  }
  if (k === 'novation') {
    return {
      arv: 0,
      pct: DEFAULT_NOVATION_PCT,
      mult: 1,
      rehabPsf: DEFAULT_PSF,
      fee: DEFAULT_NOVATION_FEE,
      profitPct: DEFAULT_PROFIT_PCT,
      ...listRates(),
    }
  }
  if (k === 'wholetail') {
    return {
      arv: 0,
      pct: DEFAULT_NOVATION_PCT,
      mult: 1,
      rehabPsf: DEFAULT_PSF,
      fee: DEFAULT_NOVATION_FEE,
      profitPct: DEFAULT_PROFIT_PCT,
      interestPct: DEFAULT_WHOLETAIL_RATE,
      holdMonths: DEFAULT_WHOLETAIL_MONTHS,
      ...listRates(),
    }
  }
  if (k === 'rental') {
    return {
      arv: 0,
      pct: DEFAULT_RENTAL_PCT,
      mult: 1,
      rehabPsf: DEFAULT_PSF,
      fee: DEFAULT_FEE,
      profitPct: DEFAULT_PROFIT_PCT,
      liens: 0,
      backTaxes: 0,
      ...listRates(),
    }
  }
  if (k === 'auction') {
    const g = f || FORMULAS[0]
    return {
      arv: 0,
      pct: g.pct,
      mult: g.mult,
      rehabPsf: DEFAULT_PSF,
      fee: DEFAULT_FEE,
      profitPct: DEFAULT_PROFIT_PCT,
      liens: 0,
      backTaxes: 0,
      ...listRates(),
    }
  }
  const g = f || FORMULAS[0]
  return {
    arv: 0,
    pct: g.pct,
    mult: g.mult,
    rehabPsf: DEFAULT_PSF,
    fee: DEFAULT_FEE,
    profitPct: DEFAULT_PROFIT_PCT,
    liens: 0,
    backTaxes: 0,
    ...listRates(),
  }
}

function emptyBook(k) {
  return {
    s: [fresh(k), fresh(k)],
    cols: 1,
    notes: '',
    // Cash follows the sale map until the rep edits the table. The other
    // kinds start empty — switching must not copy the other notebook's comps.
    comps: k === 'cash' || k === 'auction' ? null : [],
  }
}

const kind = ref('cash')
const books = reactive({
  cash: emptyBook('cash'),
  auction: emptyBook('auction'),
  novation: emptyBook('novation'),
  wholetail: emptyBook('wholetail'),
  list: emptyBook('list'),
  rental: emptyBook('rental'),
})
const valueLabel = computed(() => {
  if (kind.value === 'novation' || kind.value === 'wholetail') return __('Current value')
  if (kind.value === 'list') return __('As-is')
  if (kind.value === 'rental') return __('Move-in ready')
  return __('Exit Price')
})
const pctLabel = computed(() => {
  if (kind.value === 'novation' || kind.value === 'wholetail') return __('− %')
  if (kind.value === 'rental') return __('% of MIR')
  return __('% of Exit Price')
})
const offerLabel = computed(() => {
  if (kind.value === 'list') return __('Takeaway')
  if (kind.value === 'rental') return __('MAO')
  if (kind.value === 'auction') return __('Max bid')
  return __('Offer')
})
// Template reads these; script uses S() so a kind switch cannot point at a
// stale array. Comps live on the book — cash and novation keep their own.
const s = computed(() => books[kind.value].s)
const cols = computed({
  get: () => books[kind.value].cols,
  set: (v) => {
    books[kind.value].cols = v
  },
})
const notes = computed({
  get: () => books[kind.value].notes,
  set: (v) => {
    books[kind.value].notes = v
  },
})
function S() {
  return books[kind.value].s
}
// Only needed to hold the raw input open when the typed number happens to land
// on a rung; every other custom case falls out of `tierFor` returning nothing.
const custom = reactive([false, false])
const menuFor = ref(-1)
const saving = ref(false)
const visible = computed(() => (cols.value === 2 ? [0, 1] : [0]))
const canSave = computed(
  () => visible.value.some((i) => S()[i].arv > 0) && !saving.value,
)
const grid = [
  [null, null],
  [null, null],
  [null, null],
  [null, null],
  [null, null],
  [null, null],
  [null, null],
  [null, null],
  [null, null],
  [null, null],
  [null, null],
  [null, null],
]

function setField(row, col, el) {
  grid[row][col] = el || grid[row][col]
}

function cloneComps(list) {
  return (list || []).map((c) => ({ ...c }))
}

function compKey(c) {
  return c?.name || c?.address || ''
}

function compKeys(list) {
  return (list || []).map(compKey).filter(Boolean).sort().join('\n')
}

function removeComp(name) {
  const b = books[kind.value]
  if (!b.comps) b.comps = cloneComps(props.comps)
  b.comps = b.comps.filter((c) => compKey(c) !== name)
}

const tableComps = computed(() => {
  const own = books[kind.value].comps
  // Cash may still follow the sale map (null). Rentals never ride along.
  if (own == null) {
    if (kind.value !== 'cash' && kind.value !== 'auction') return []
    return (props.comps || []).filter((c) => !isRentalRow(c))
  }
  return own
})

const storageKey = computed(() =>
  props.practiceAttempt && props.practiceProperty
    ? `practiceCalc:${props.practiceAttempt}:${props.practiceProperty}`
    : `compsCalc:${props.lead}`,
)

function readRate(row, camel, snake, fallback) {
  const v = row?.[camel] ?? row?.[snake]
  if (v === undefined || v === null || v === '') return fallback
  const n = Number(v)
  if (!Number.isFinite(n)) return fallback
  return n > 1 ? n / 100 : n
}

function applyScene(k, i, row) {
  const base = fresh(k)
  const blank = !row || !Object.keys(row).length
  const oldRentCap = k === 'rental' && (row.annual != null || Number(row.pct) > 0 && Number(row.pct) < 0.5)
  Object.assign(books[k].s[i], base, {
    arv: oldRentCap ? 0 : Number(row.arv) || 0,
    pct: oldRentCap ? base.pct : Number(row.pct) || base.pct,
    rehabPsf: Number(row.rehabPsf ?? row.rehab_psf) || DEFAULT_PSF,
    fee: Number(row.fee) || base.fee,
    profitPct: readRate(row, 'profitPct', 'profit_pct', base.profitPct ?? DEFAULT_PROFIT_PCT),
    // A snapshot written before the toggle existed carries no `mult`, and its
    // numbers were computed with a single deduction — so it IS the classic
    // one. Only a genuine reset falls back to the column's default.
    // Novation / list-it have no multiplier; leave the cash default unused.
    mult: k === 'cash' || k === 'auction' ? Number(row.mult) || (blank ? base.mult : 1) : 1,
    liens: Number(row.liens) || 0,
    backTaxes: Number(row.backTaxes ?? row.back_taxes) || 0,
    interestPct: readRate(row, 'interestPct', 'interest_pct', base.interestPct ?? 0),
    holdMonths: (() => {
      const v = row.holdMonths ?? row.hold_months
      if (v === undefined || v === null || v === '') return base.holdMonths || 0
      const n = Number(v)
      return Number.isFinite(n) ? n : (base.holdMonths || 0)
    })(),
    commissionPct: readRate(row, 'commissionPct', 'commission_pct', base.commissionPct),
    closingPct: readRate(row, 'closingPct', 'closing_pct', base.closingPct),
    concessionsPct: readRate(
      row,
      'concessionsPct',
      'concessions_pct',
      base.concessionsPct,
    ),
  })
}

function loadBook(k, raw) {
  if (!raw) return
  if (Array.isArray(raw.s) && raw.s.length) {
    applyScene(k, 0, {})
    applyScene(k, 1, {})
    raw.s.forEach((row, i) => {
      if (i < 2) applyScene(k, i, row)
    })
  }
  if (raw.cols === 2) books[k].cols = 2
  if (typeof raw.notes === 'string') books[k].notes = raw.notes
  if (Array.isArray(raw.comps)) books[k].comps = cloneComps(raw.comps)
}

function resetBooks() {
  books.cash = emptyBook('cash')
  books.auction = emptyBook('auction')
  books.novation = emptyBook('novation')
  books.wholetail = emptyBook('wholetail')
  books.list = emptyBook('list')
  books.rental = emptyBook('rental')
  kind.value = 'cash'
  menuFor.value = -1
  custom[0] = false
  custom[1] = false
}

let hydrating = false
const seenMap = new Set()
function rememberMap(list) {
  seenMap.clear()
  for (const c of list || []) {
    const key = compKey(c)
    if (key) seenMap.add(key)
  }
}

function loadSaved() {
  hydrating = true
  try {
    resetBooks()
    let stored = null
    try {
      stored = JSON.parse(localStorage.getItem(storageKey.value) || 'null')
    } catch {
      stored = null
    }
    if (stored?.cash || stored?.novation || stored?.wholetail || stored?.list || stored?.rental || stored?.auction) {
      loadBook('cash', stored.cash)
      loadBook('auction', stored.auction)
      loadBook('novation', stored.novation)
      loadBook('wholetail', stored.wholetail)
      loadBook('list', stored.list)
      loadBook('rental', stored.rental)
      kind.value = asKind(stored.kind)
      // An earlier build copied the cash/map list onto novation at switch.
      // If the two lists are the same names, novation never got its own picks.
      const cashList = stored.cash?.comps ?? props.comps
      if (compKeys(books.novation.comps) && compKeys(books.novation.comps) === compKeys(cashList)) {
        books.novation.comps = []
      }
    } else if (stored && Array.isArray(stored.s)) {
      // Pre-split draft: one notebook. Drop it into whichever kind it was.
      const k = asKind(stored.kind)
      kind.value = k
      loadBook(k, stored)
    }
    const seed = props.seed
    if (seed && Array.isArray(seed.scenarios) && seed.scenarios.length) {
      const k = asKind(seed.kind || seed.scenarios[0]?.kind)
      kind.value = k
      applyScene(k, 0, {})
      applyScene(k, 1, {})
      seed.scenarios.forEach((row, i) => {
        if (i < 2) applyScene(k, i, row)
      })
      books[k].cols = Math.min(2, Math.max(1, seed.scenarios.length))
      if (typeof seed.notes === 'string') books[k].notes = seed.notes
      if (Array.isArray(seed.comps)) books[k].comps = cloneComps(seed.comps)
      else if ((k === 'cash' || k === 'auction') && props.comps?.length) {
        books[k].comps = cloneComps(props.comps)
      }
    } else if (props.preferKind === 'auction' && !(stored && stored.kind)) {
      kind.value = 'auction'
    }
  } finally {
    hydrating = false
    rememberMap(props.comps)
  }
}

function persist() {
  if (hydrating) return
  try {
    localStorage.setItem(
      storageKey.value,
      JSON.stringify({
        kind: kind.value,
        cash: {
          s: books.cash.s,
          cols: books.cash.cols,
          notes: books.cash.notes,
          comps: books.cash.comps,
        },
        novation: {
          s: books.novation.s,
          cols: books.novation.cols,
          notes: books.novation.notes,
          comps: books.novation.comps,
        },
        wholetail: {
          s: books.wholetail.s,
          cols: books.wholetail.cols,
          notes: books.wholetail.notes,
          comps: books.wholetail.comps,
        },
        list: {
          s: books.list.s,
          cols: books.list.cols,
          notes: books.list.notes,
          comps: books.list.comps,
        },
        rental: {
          s: books.rental.s,
          cols: books.rental.cols,
          notes: books.rental.notes,
          comps: books.rental.comps,
        },
        auction: {
          s: books.auction.s,
          cols: books.auction.cols,
          notes: books.auction.notes,
          comps: books.auction.comps,
        },
      }),
    )
  } catch {
    /* quota */
  }
}

watch([storageKey, () => props.seed], loadSaved, { immediate: true })
watch(books, persist, { deep: true })
watch(kind, persist)

watch(
  () => props.comps,
  (list) => {
    const added = (list || []).filter((c) => {
      const key = compKey(c)
      if (!key || seenMap.has(key)) return false
      return kind.value === 'rental' ? isRentalRow(c) : !isRentalRow(c)
    })
    rememberMap(list)
    if (hydrating) return
    // Only pins the rep picks WHILE on this kind join its table. Switching
    // must not dump the other notebook's (or the map's) list in.
    const b = books[kind.value]
    if (!b.comps) {
      if (kind.value !== 'cash' && kind.value !== 'auction') b.comps = []
      else return
    }
    const have = new Set(b.comps.map(compKey))
    for (const c of added) {
      const key = compKey(c)
      if (!have.has(key)) {
        b.comps.push({ ...c })
        have.add(key)
      }
    }
  },
  { deep: true },
)
function setKind(next) {
  if (kind.value === next) return
  kind.value = next
  menuFor.value = -1
  custom[0] = false
  custom[1] = false
  emit('kind', next)
}
watch(kind, (k) => emit('kind', k), { immediate: true })

const sqft = computed(() => Number(props.subject?.sqft) || 0)

function tierFor(col) {
  return TIERS.find((t) => t.psf === Number(S()[col].rehabPsf)) || null
}
function isCustom(col) {
  return custom[col] || !tierFor(col)
}
function toggleMenu(col) {
  menuFor.value = menuFor.value === col ? -1 : col
}
function pickTier(col, t) {
  S()[col].rehabPsf = t.psf
  custom[col] = false
  menuFor.value = -1
}
function pickOther(col) {
  custom[col] = true
  menuFor.value = -1
  nextTick(() => {
    const el = grid[3][col]
    el?.focus()
    el?.select?.()
  })
}
// A click anywhere else closes the menu. Capture phase so it still fires when
// the click lands on something that stops propagation.
function onDocClick(e) {
  if (menuFor.value < 0) return
  if (!e.target?.closest?.('.rep')) menuFor.value = -1
}
onMounted(() => document.addEventListener('click', onDocClick, true))
onBeforeUnmount(() => document.removeEventListener('click', onDocClick, true))

function multOf(col) {
  return Number(S()[col]?.mult) === 2 ? 2 : 1
}
function setFormula(col, f) {
  S()[col].mult = f.mult
  // The percentage is part of the formula's identity, so picking one sets it.
  // It stays editable afterwards; the rep just starts from the canonical number.
  S()[col].pct = f.pct
}

/** A second column starts as a copy of the one the rep is looking at — same
 *  formula, same percentage, same repair level — so the only thing different
 *  about it is what they deliberately change. An untouched column only; a
 *  dropped-then-restored one keeps whatever was in it. */
function addCompare() {
  if (!S()[1].arv) {
    Object.assign(S()[1], {
      pct: S()[0].pct,
      mult: multOf(0),
      rehabPsf: S()[0].rehabPsf,
      fee: S()[0].fee,
      profitPct: S()[0].profitPct,
      liens: S()[0].liens,
      backTaxes: S()[0].backTaxes,
      interestPct: S()[0].interestPct,
      holdMonths: S()[0].holdMonths,
      commissionPct: S()[0].commissionPct,
      closingPct: S()[0].closingPct,
      concessionsPct: S()[0].concessionsPct,
    })
  }
  cols.value = 2
}

function run(col, formula) {
  const x = S()[col]
  const pct = formula ? formula.pct : x.pct
  const mult = formula ? formula.mult : multOf(col)
  if (kind.value === 'list') {
    const commission = Math.round(x.arv * (Number(x.commissionPct) || 0))
    const closing = Math.round(x.arv * (Number(x.closingPct) || 0))
    const concessions = Math.round(x.arv * (Number(x.concessionsPct) || 0))
    const after = Math.round(x.arv - commission - closing - concessions)
    return {
      after,
      cut: commission + closing + concessions,
      commission,
      closing,
      concessions,
      repairs: 0,
      rehab: 0,
      wholesale: after,
      offer: after,
    }
  }
  const fee = Math.round(Number(x.fee) || 0)
  const split = (net) => ({ fee, offer: net - fee })
  if (kind.value === 'novation') {
    const cut = Math.round(x.arv * pct)
    const after = Math.round(x.arv - cut)
    return { after, cut, repairs: 0, rehab: 0, wholesale: after, ...split(after) }
  }
  if (kind.value === 'wholetail') {
    const cut = Math.round(x.arv * pct)
    const after = Math.round(x.arv - cut)
    const months = Math.max(0, Number(x.holdMonths) || 0)
    const rate = Number(x.interestPct) || 0
    const capital = Math.round(x.arv * rate * (months / 12))
    const holding = Math.round(
      x.arv * HOLDING_TAX_INS_PCT * (months / 12) + HOLDING_UTIL_MO * months,
    )
    const net = after - capital - holding
    return {
      after, cut, repairs: 0, rehab: 0, wholesale: after,
      capital, holding,
      ...split(net),
    }
  }
  const after = Math.round(x.arv * pct)
  const repairs = Math.round((Number(x.rehabPsf) || 0) * sqft.value)
  // Rental MAO is 1× repairs. Cash and auction honor the formula toggle.
  const rehab = repairs * (kind.value === 'rental' ? 1 : mult)
  const wholesale = after - rehab
  const liens = kind.value === 'auction' ? Math.round(Number(x.liens) || 0) : 0
  const backTaxes = kind.value === 'auction' ? Math.round(Number(x.backTaxes) || 0) : 0
  const net = wholesale - liens - backTaxes
  return { after, repairs, rehab, wholesale, liens, backTaxes, ...split(net) }
}

// Only ever a comparison between two PRICED columns: an empty scenario reads
// as an offer of minus the fee, and crowning the other one for beating it
// would be noise dressed up as a finding.
const winner = computed(() => {
  if (cols.value !== 2 || !S()[0].arv || !S()[1].arv) return -1
  const a = run(0).offer
  const b = run(1).offer
  if (a === b) return -1
  return a > b ? 0 : 1
})
const offerGap = computed(() =>
  winner.value < 0 ? 0 : Math.abs(run(0).offer - run(1).offer),
)

// What the other formula would pay for the same house, at ITS percentage.
// Same run() path as the column on screen — auction liens/taxes included —
// so the line cannot disagree with toggling the formula.
const altFormula = computed(
  () => FORMULAS.find((f) => f.mult !== multOf(0)) || FORMULAS[0],
)
const altOffer = computed(() => {
  if (cols.value !== 1 || !S()[0].arv) return null
  if (kind.value !== 'cash' && kind.value !== 'auction') return null
  return run(0, altFormula.value).offer
})

const usable = computed(() =>
  tableComps.value.filter((c) => Number(c.price) > 0 && Number(c.square_footage) > 0),
)
const avgPsf = computed(() => {
  if (!usable.value.length) return 0
  const sum = usable.value.reduce((a, c) => a + Number(c.price) / Number(c.square_footage), 0)
  const avg = sum / usable.value.length
  return Math.round(avg)
})
const suggestedArv = computed(() => {
  // Rental pins are monthly rent — never a MIR. Borrow cash ARV if they already
  // priced it; otherwise leave MIR blank.
  if (kind.value === 'rental') return Number(books.cash.s[0]?.arv) || 0
  if (!avgPsf.value || !sqft.value) return 0
  return Math.round((avgPsf.value * sqft.value) / 1000) * 1000
})
const arvHint = computed(() => (suggestedArv.value ? money(suggestedArv.value) : ''))

// Soft-fill: write the comps-table average into an empty (or still-suggested)
// value so Current value / ARV starts from the same number the table is
// pointing at. Per-kind, so editing cash cannot move novation's value.
const lastSoft = { cash: 0, auction: 0, novation: 0, wholetail: 0, list: 0, rental: 0 }
function applySoft(k) {
  const v = suggestedArv.value
  if (!v) return
  for (const i of [0, 1]) {
    const col = books[k].s[i]
    if (!col.arv || col.arv === lastSoft[k]) col.arv = v
  }
  lastSoft[k] = v
}
watch(suggestedArv, () => applySoft(kind.value))
watch(kind, (k) => applySoft(k))

// Rental defaults the repair BILL to $10k, not a $/sf tier. Keep that total
// while sqft is still unknown or still the seeded value; a picked tier or a
// typed bill is a human choice and is left alone.
const lastRentalPsf = { v: 0 }
function applyRentalRepairs() {
  if (!sqft.value) return
  const psf = DEFAULT_RENTAL_REPAIRS / sqft.value
  for (const i of [0, 1]) {
    const col = books.rental.s[i]
    if (
      !col.rehabPsf ||
      col.rehabPsf === lastRentalPsf.v ||
      (lastRentalPsf.v === 0 && col.rehabPsf === DEFAULT_PSF)
    ) {
      col.rehabPsf = psf
    }
  }
  lastRentalPsf.v = psf
}
watch(sqft, applyRentalRepairs)
watch(kind, (k) => {
  if (k === 'rental') applyRentalRepairs()
})

const subjectRow = computed(() => {
  const s = props.subject
  if (!s && !props.address) return null
  const sf = Number(s?.sqft) || 0
  const sale = s?.last_sale || {}
  const price = Number(sale.price) || 0
  const psf = price && sf ? Math.round(price / sf) : 0
  return {
    address: props.address || '',
    street: streetAddress(props.address) || props.address || __('This property'),
    date: sale.date ? fmtDate(sale.date) : '—',
    sqft: sf ? sf.toLocaleString() : '—',
    price: price ? money(price) : '—',
    psf: psf ? money(psf) : '—',
  }
})

const rows = computed(() =>
  tableComps.value.map((c) => {
    const price = Number(c.price) || 0
    const sf = Number(c.square_footage) || 0
    const psf = price && sf ? Math.round(price / sf) : 0
    const active = String(c.status || '').toLowerCase().startsWith('activ')
    return {
      name: c.name,
      address: c.address || '',
      street: streetAddress(c.address) || c.address || '—',
      date: fmtDate(c.removed_date || c.listed_date),
      mi: c.distance_mi != null ? Number(c.distance_mi).toFixed(2) : '—',
      sqft: sf ? sf.toLocaleString() : '—',
      price: price
        ? kind.value === 'rental' || isRentalRow(c)
          ? money(price) + '/mo'
          : money(price)
        : '—',
      psf: psf ? money(psf) : '—',
      onThis: psf && sqft.value ? money(Math.round(psf * sqft.value)) : '—',
      // The rep's condition tag rides with the status — "Sold · Full gut" is
      // exactly the context that explains an outlier $/sf in this table.
      status:
        (kind.value === 'rental' || isRentalRow(c)
          ? __('For rent')
          : c.listing_state === 'auction'
            ? __('Auction')
            : c.listing_state === 'pending'
              ? __('Pending')
              : active
                ? __('Listed')
                : __('Sold')) + (c.comp_type ? ` · ${c.comp_type}` : ''),
    }
  }),
)

function parseMoney(v) {
  const n = Number(String(v).replace(/[^0-9.]/g, ''))
  return Number.isFinite(n) ? n : 0
}
function money(n) {
  return '$' + Math.round(Number(n) || 0).toLocaleString('en-US')
}
/** Rounded to whole thousands: this is a feel for the size, not a quote. */
function k(n) {
  return '$' + Math.round((Number(n) || 0) / 1000).toLocaleString('en-US') + 'k'
}
function fmt(n) {
  return (Number(n) || 0).toLocaleString('en-US')
}
function fmtDate(v) {
  if (!v) return '—'
  const m = String(v).match(/^(\d{4})-(\d{2})-(\d{2})/)
  const d = m ? new Date(+m[1], +m[2] - 1, +m[3]) : new Date(v)
  if (isNaN(d)) return '—'
  return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
}
/** The rate fields DISPLAY whole percents (`Math.round(pct*100)`), so what is
 *  typed is a percent too: 1 means 1%, never 100%. The old `n > 1 ? n/100 : n`
 *  guess treated anything ≤1 as an already-converted fraction, so typing 1
 *  into concessions read as 100% and wiped the takeaway. Storage stays
 *  fraction-denominated — only the keyboard side changes. */
function setPct(col, e) {
  const n = parseMoney(e.target.value)
  const fallback =
    kind.value === 'novation' || kind.value === 'wholetail'
      ? DEFAULT_NOVATION_PCT
      : kind.value === 'rental'
        ? DEFAULT_RENTAL_PCT
        : col
          ? 0.65
          : 0.7
  S()[col].pct = n / 100 || fallback
}
function profitPctDisplay(col) {
  const r = run(col)
  if (!S()[col].arv || r.offer <= 0) return ''
  return Math.round((r.fee / r.offer) * 100)
}
function setProfit(col, e) {
  const n = parseMoney(e.target.value)
  const r = run(col)
  const net = r.offer + r.fee
  if (!Number.isFinite(n) || n <= 0) {
    S()[col].fee = 0
    S()[col].profitPct = 0
    return
  }
  const m = n / 100
  if (m <= -1) return
  S()[col].profitPct = m
  S()[col].fee = Math.round((net * m) / (1 + m))
}
/** Dollar profit is the takeoff (default $25k). It is allowed to exceed the
 *  offer — that is a negative offer, not a reason to hide the number. */
function typeProfit(col, e) {
  const el = e.target
  const digitsBefore = (el.value.slice(0, el.selectionStart).match(/\d/g) || []).length
  const n = parseMoney(el.value)
  const r = run(col)
  const net = r.offer + r.fee
  S()[col].fee = n
  const offer = net - n
  S()[col].profitPct = offer > 0 ? n / offer : 0
  nextTick(() => putCaret(el, digitsBefore, n ? money(n) : ''))
}
function setInterest(col, e) {
  const n = parseMoney(e.target.value)
  S()[col].interestPct = n / 100 || DEFAULT_WHOLETAIL_RATE
}
function setHoldMonths(col, e) {
  const n = Math.round(parseMoney(e.target.value))
  S()[col].holdMonths = Number.isFinite(n) && n >= 0 ? n : DEFAULT_WHOLETAIL_MONTHS
}
function setRate(col, key, e) {
  const n = parseMoney(e.target.value)
  S()[col][key] = n / 100
}
function listCutPct(col) {
  const x = S()[col]
  return (
    (Number(x.commissionPct) || 0) +
    (Number(x.closingPct) || 0) +
    (Number(x.concessionsPct) || 0)
  )
}

/** Write the formatted value (Vue skips the DOM patch when `:value` didn't
 *  change) and put the caret after the same digit it was on. */
function putCaret(el, digitsBefore, formatted) {
  if (typeof formatted === 'string' && el.value !== formatted) el.value = formatted
  let seen = 0
  let pos = formatted.length
  for (let i = 0; i < formatted.length; i++) {
    if (/\d/.test(formatted[i])) {
      seen++
      if (seen >= digitsBefore) {
        pos = i + 1
        break
      }
    }
  }
  el.setSelectionRange(pos, pos)
}

function typeMoney(col, key, e) {
  const el = e.target
  const digitsBefore = (el.value.slice(0, el.selectionStart).match(/\d/g) || []).length
  const n = parseMoney(el.value)
  S()[col][key] = n
  const formatted = n ? money(n) : ''
  nextTick(() => putCaret(el, digitsBefore, formatted))
}

/** After % back-solves the value so the column still adds up. Novation is
 *  value × (1 − %), cash is value × %. Full precision, same reason as the
 *  repair bill: rounding here would make the field twitch while typing. */
function typeAfter(col, e) {
  const el = e.target
  const digitsBefore = (el.value.slice(0, el.selectionStart).match(/\d/g) || []).length
  const n = parseMoney(el.value)
  const pct = Number(S()[col].pct) || 0
  if (kind.value === 'novation' || kind.value === 'wholetail') {
    if (pct < 1) S()[col].arv = n / (1 - pct)
  } else if (pct) {
    S()[col].arv = n / pct
  }
  nextTick(() => putCaret(el, digitsBefore, n ? money(n) : ''))
}

/** Dollar profit stays put. Offer edits back-solve Exit Price / as-is; the
 *  percent is derived from fee / offer. Only `setProfit` rewrites the dollars. */
function backsolveArv(col, net) {
  const x = S()[col]
  const pct = Number(x.pct) || 0
  const k = kind.value
  if (k === 'novation') {
    if (pct < 1) x.arv = net / (1 - pct)
    return
  }
  if (k === 'wholetail') {
    const months = Math.max(0, Number(x.holdMonths) || 0)
    const rate = Number(x.interestPct) || 0
    const coef =
      1 - pct - rate * (months / 12) - HOLDING_TAX_INS_PCT * (months / 12)
    const intercept = HOLDING_UTIL_MO * months
    if (coef) x.arv = (net + intercept) / coef
    return
  }
  const repairs = Math.round((Number(x.rehabPsf) || 0) * sqft.value)
  const rehab = repairs * (k === 'rental' ? 1 : multOf(col))
  const liens = k === 'auction' ? Math.round(Number(x.liens) || 0) : 0
  const backTaxes = k === 'auction' ? Math.round(Number(x.backTaxes) || 0) : 0
  if (pct) x.arv = (net + rehab + liens + backTaxes) / pct
}
function typeOffer(col, e) {
  const el = e.target
  const digitsBefore = (el.value.slice(0, el.selectionStart).match(/\d/g) || []).length
  const n = parseMoney(el.value)
  if (kind.value === 'list') {
    const tot = listCutPct(col)
    if (tot < 1) S()[col].arv = n / (1 - tot)
  } else {
    const fee = Math.round(Number(S()[col].fee) || 0)
    backsolveArv(col, n + fee)
    const offer = run(col).offer
    S()[col].profitPct = offer > 0 ? fee / offer : 0
  }
  nextTick(() => putCaret(el, digitsBefore, n ? money(n) : ''))
}

/** Type a repair BILL and get the $/sf. A straight divide — no multiplier in
 *  the path — so $37,800 is $30/sf under either formula.
 *
 *  Full precision on purpose. This field displays `$/sf × sqft`, so rounding
 *  the $/sf here loses the total the rep just typed: at 1,260 sf a half-typed
 *  "3" is $0.0024/sf, which to the nearest cent is ZERO, so the field
 *  re-rendered as "$0" and the caret restore interleaved the next keystroke
 *  into it — "$03", then "$38", then "$391". Unrounded, psf × sqft returns
 *  exactly what was typed and the field holds still while you type. */
function typeRehab(col, e) {
  const el = e.target
  const digitsBefore = (el.value.slice(0, el.selectionStart).match(/\d/g) || []).length
  const n = parseMoney(el.value)
  if (sqft.value) S()[col].rehabPsf = n / sqft.value
  nextTick(() => putCaret(el, digitsBefore, n ? money(n) : ''))
}

/** $/sf, carrying cents only when it has them. A typed bill rarely divides
 *  evenly — $45,000 over 1,260 sf is $35.714… — and snapping that to the cent
 *  was tried and removed: it dragged the total the rep had just typed to
 *  $44,995 the moment they clicked away, which reads as the tool losing their
 *  number. The bill is what they typed; the rate is the derived half, so the
 *  rate is where the remainder lives. */
function psfText(n) {
  const v = Number(n) || 0
  return Number.isInteger(v) ? money(v) : '$' + v.toFixed(2)
}

async function save() {
  if (!canSave.value) return false
  saving.value = true
  try {
    const payload = {
      scenarios: JSON.stringify(
        visible.value.map((i) => {
          const x = S()[i]
          const row = { kind: kind.value, arv: x.arv, ...run(i) }
          if (kind.value === 'list') {
            row.commission_pct = x.commissionPct
            row.closing_pct = x.closingPct
            row.concessions_pct = x.concessionsPct
          } else {
            row.pct = x.pct
            row.fee = x.fee
            row.profit_pct = row.offer > 0 ? row.fee / row.offer : 0
            if (kind.value === 'cash' || kind.value === 'rental' || kind.value === 'auction') {
              row.mult = kind.value === 'rental' ? 1 : multOf(i)
              row.rehabPsf = x.rehabPsf
            }
            if (kind.value === 'auction') {
              row.liens = Math.round(Number(x.liens) || 0)
              row.back_taxes = Math.round(Number(x.backTaxes) || 0)
            }
            if (kind.value === 'wholetail') {
              row.interest_pct = x.interestPct
              row.hold_months = x.holdMonths
            }
          }
          return row
        }),
      ),
      comps: JSON.stringify(
        tableComps.value.map((c) => ({
          name: c.name,
          address: c.address,
          price: c.price,
          square_footage: c.square_footage,
          distance_mi: c.distance_mi,
          status: c.status,
        })),
      ),
      subject_sqft: sqft.value,
      notes: notes.value,
    }
    if (props.practiceAttempt && props.practiceProperty) {
      await call('crm.api.practice.save_offer', {
        attempt: props.practiceAttempt,
        property: props.practiceProperty,
        ...payload,
      })
      toast.success(__('Saved to this practice run'))
    } else {
      await call('crm.api.cash_offer.save_cash_offer', {
        lead: props.lead,
        ...payload,
      })
      toast.success(__('Saved to the activity timeline'))
    }
    emit('saved')
    return true
  } catch (e) {
    toast.error(e.messages?.[0] || __('Could not save the calc.'))
    return false
  } finally {
    saving.value = false
  }
}

/** Ask AI handoff: put the AI's ARV and repair tier in the first cash column. */
function prefill({ arv, rehabPsf, mult }) {
  kind.value = 'cash'
  Object.assign(books.cash.s[0], { arv, rehabPsf, mult })
}

defineExpose({ save, canSave, prefill })

function onKeys(e) {
  if (e.key === 'Escape' && menuFor.value >= 0) {
    menuFor.value = -1
    return
  }
  let r = -1
  let c = -1
  for (let i = 0; i < grid.length; i++) {
    const j = grid[i].indexOf(e.target)
    if (j >= 0) {
      r = i
      c = j
      break
    }
  }
  if (r < 0) return
  let nr = r
  let nc = c
  const step = e.key === 'ArrowDown' ? 1 : e.key === 'ArrowUp' ? -1 : 0
  if (step) nr += step
  else if (e.key === 'ArrowRight') {
    if (e.target.selectionStart < String(e.target.value || '').length) return
    nc++
  } else if (e.key === 'ArrowLeft') {
    if (e.target.selectionStart > 0) return
    nc--
  } else return
  // Walk past rows with nothing focusable in this column: the Repairs row is a
  // dropdown rather than an input unless the rep chose "Other…", and a stale
  // ref can outlive the element it pointed at.
  let next = null
  while (nr >= 0 && nr < grid.length) {
    const cand = grid[nr] && grid[nr][nc]
    if (cand && cand.isConnected) {
      next = cand
      break
    }
    if (!step) break
    nr += step
  }
  if (!next) return
  e.preventDefault()
  next.focus()
  next.select?.()
}
</script>

<style scoped>
.wrap {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-start;
  gap: 12px 20px;
  min-width: 0;
  max-width: 100%;
  font: 13px/1.35 InterVar, Inter, -apple-system, 'Segoe UI', system-ui, sans-serif;
  color: var(--ink-gray-7);
}
.calc {
  min-width: 0;
  max-width: 100%;
  border: 1px solid var(--outline-gray-2);
  border-radius: 8px;
  background: var(--surface-white);
  padding: 8px 10px 10px;
}
.head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  color: var(--ink-gray-5);
  margin-bottom: 6px;
}
.head-r {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
  margin-left: auto;
}
.link {
  border: 0;
  background: none;
  padding: 0;
  font: inherit;
  font-size: 12px;
  color: var(--ink-blue-3);
  cursor: pointer;
}
.link:hover {
  text-decoration: underline;
}
.drop {
  border: 0;
  background: none;
  padding: 0 0 0 3px;
  font: inherit;
  font-weight: 400;
  color: var(--ink-gray-4);
  cursor: pointer;
  line-height: 1;
}
.drop:hover {
  color: var(--ink-red-3);
}
.save {
  border: 1px solid var(--outline-gray-2);
  border-radius: 5px;
  background: var(--surface-gray-7);
  color: var(--ink-white);
  font: inherit;
  font-weight: 600;
  padding: 3px 8px;
  cursor: pointer;
}
.save:disabled {
  opacity: 0.4;
  cursor: default;
}
.seller-note {
  margin: 8px 0 0;
  padding: 6px 7px;
  border-radius: 5px;
  background: var(--surface-gray-2);
  color: var(--ink-gray-8);
  font-size: 12px;
  line-height: 1.4;
}
.seller-note-lbl {
  display: block;
  margin-bottom: 2px;
  font-size: 10px;
  font-weight: 600;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  color: var(--ink-gray-5);
}
.notes {
  display: block;
  box-sizing: border-box;
  width: 100%;
  margin-top: 8px;
  border: 1px solid var(--outline-gray-2);
  border-radius: 5px;
  background: var(--surface-gray-2);
  padding: 6px 7px;
  font: inherit;
  color: var(--ink-gray-9);
  resize: vertical;
  min-height: 3.4em;
}
.notes:focus {
  outline: none;
  border-color: var(--ink-blue-3);
  background: var(--surface-white);
}
.grid {
  display: grid;
  column-gap: 8px;
  row-gap: 4px;
  align-items: center;
}
.colh {
  font-weight: 600;
  color: var(--ink-gray-9);
  text-align: right;
}
.lab {
  display: flex;
  align-items: center;
  gap: 4px;
  color: var(--ink-gray-5);
}
.lab i {
  font-style: normal;
  color: var(--ink-gray-4);
}
.why {
  position: relative;
  flex: none;
  width: 14px;
  height: 14px;
  border: 1px solid var(--outline-gray-2);
  border-radius: 99px;
  background: none;
  padding: 0;
  font: 10px/14px inherit;
  color: var(--ink-gray-5);
  cursor: help;
}
.why:hover,
.why:focus {
  color: var(--ink-gray-8);
  border-color: var(--ink-gray-5);
}
.why-tip {
  display: none;
  position: absolute;
  z-index: 20;
  left: 0;
  top: 18px;
  width: max-content;
  padding: 6px 8px;
  border: 1px solid var(--outline-gray-2);
  border-radius: 6px;
  background: var(--surface-white);
  box-shadow: 0 6px 18px rgb(0 0 0 / 13%);
  color: var(--ink-gray-7);
  font: 12px/1.4 inherit;
  font-weight: 400;
  text-align: left;
  white-space: nowrap;
}
.why:hover .why-tip,
.why:focus .why-tip {
  display: block;
}
.why-tip b {
  font-weight: 650;
  color: var(--ink-gray-9);
}
.lab.offer,
.out.offer {
  font-weight: 650;
  color: var(--ink-gray-9);
}
input {
  box-sizing: border-box;
  width: 100%;
  height: 26px;
  border: 1px solid var(--outline-gray-2);
  border-radius: 5px;
  background: var(--surface-gray-2);
  padding: 0 7px;
  text-align: right;
  font: inherit;
  font-variant-numeric: tabular-nums;
  color: var(--ink-gray-9);
}
input:focus {
  outline: none;
  border-color: var(--ink-blue-3);
  background: var(--surface-white);
}
input.empty {
  color: var(--ink-gray-5);
}
.pct {
  display: flex;
  align-items: center;
  gap: 3px;
  min-width: 0;
}
.pct i {
  font-style: normal;
  color: var(--ink-gray-5);
  flex: none;
}
.out {
  text-align: right;
  font-variant-numeric: tabular-nums;
  padding-right: 7px;
}
.out.bad {
  color: var(--ink-red-3);
}
/* The winning column keeps its number on the same right edge as the loser's —
   the badge grows leftward — or the two offers stop being comparable at a
   glance, which is the one thing this row exists for. */
.out.offer,
.offer-in {
  display: flex;
  align-items: baseline;
  justify-content: flex-end;
  gap: 6px;
  min-width: 0;
}
.offer-in input.offer {
  font-weight: 650;
}
input.offer.bad {
  color: var(--ink-red-3);
}
/* A higher offer is a problem, not a win — green read as "good" and the
   whole point of showing the other formula is that paying more is the thing
   to notice. Same red as a zero/negative offer. */
.gap {
  font-size: 11px;
  font-weight: 600;
  color: var(--ink-red-3);
  background: var(--surface-red-1);
  border-radius: 4px;
  padding: 1px 4px;
  white-space: nowrap;
}
.alt {
  margin-top: 7px;
  color: var(--ink-gray-5);
  font-size: 12px;
}
.alt b {
  font-weight: 600;
}
.alt .more {
  color: var(--ink-red-3);
}
.alt .less {
  color: var(--ink-gray-7);
}

/* Formula toggle. A segmented pair rather than a dropdown: there are exactly
   two, and which one is running has to be readable without opening anything. */
.seg {
  display: flex;
  min-width: 0;
  border: 1px solid var(--outline-gray-2);
  border-radius: 5px;
  background: var(--surface-gray-2);
  padding: 1px;
  gap: 1px;
}
.seg button {
  flex: 1;
  min-width: 0;
  height: 24px;
  border: 0;
  border-radius: 4px;
  background: none;
  padding: 0 4px;
  font: inherit;
  font-size: 11.5px;
  color: var(--ink-gray-6);
  cursor: pointer;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.seg button:hover {
  color: var(--ink-gray-9);
}
.seg button.on {
  background: var(--surface-white);
  color: var(--ink-gray-9);
  font-weight: 600;
  box-shadow: 0 1px 2px rgb(0 0 0 / 8%);
}
.kind {
  width: auto;
  max-width: 100%;
  flex: none;
  flex-wrap: wrap;
}
.kind button {
  flex: none;
  padding: 0 6px;
}
.hold {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 6px;
  min-width: 0;
}
.hold .pct {
  flex: none;
  width: 3.6rem;
}
.cut {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
  min-width: 0;
}
.cut .pct {
  flex: none;
  width: 3.6rem;
}
.cut .out {
  flex: 1;
  min-width: 0;
  padding-right: 0;
}
.cut input {
  flex: 1;
  min-width: 0;
}

/* A derived row can carry the figure it derived from. Same shape as the offer
   badge: annotation grows leftward, the number keeps the column's right edge. */
.out.annot {
  display: flex;
  align-items: baseline;
  justify-content: flex-end;
  gap: 6px;
}
.out.annot i {
  font-style: normal;
  font-size: 11.5px;
  color: var(--ink-gray-5);
  cursor: help;
  white-space: nowrap;
}

/* Repairs picker. The trigger's value column and the menu's have to land on
   the SAME x or the selected row reads as misaligned with the list it came
   from: the trigger reserves 6px + a 10px caret inside its 7px padding (23px),
   and the menu rows carry 20px of right padding inside the 3px popover pad to
   match it exactly. */
.rep {
  position: relative;
  display: flex;
  align-items: center;
  min-width: 0;
  container-type: inline-size;
}
/* The NAME is the one thing on the trigger that has to survive a narrow cell
   — it truncated to "Kitc…" in the Today modal on a phone. The rate and the
   total are both a glance away (the open menu, and the Rehab row directly
   below), so they give way first: the total, then the rate. */
@container (max-width: 230px) {
  .rep-btn .vals b {
    display: none;
  }
}
@container (max-width: 185px) {
  .rep-btn .vals {
    display: none;
  }
}
.rep-btn {
  display: flex;
  align-items: center;
  gap: 6px;
  width: 100%;
  height: 26px;
  border: 1px solid var(--outline-gray-2);
  border-radius: 5px;
  background: var(--surface-gray-2);
  padding: 0 7px;
  font: inherit;
  color: var(--ink-gray-9);
  cursor: pointer;
}
.rep-btn:hover,
.rep-btn.open {
  border-color: var(--ink-blue-3);
  background: var(--surface-white);
}
.rep-name {
  flex: 1;
  min-width: 0;
  text-align: left;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.caret {
  flex: none;
  width: 10px;
  text-align: right;
  color: var(--ink-gray-5);
}
.vals {
  flex: none;
  white-space: nowrap;
  font-variant-numeric: tabular-nums;
}
.vals i {
  font-style: normal;
  color: var(--ink-gray-5);
}
.vals b {
  font-weight: 400;
  color: var(--ink-gray-9);
}
.rep-psf {
  border-top-right-radius: 0;
  border-bottom-right-radius: 0;
  border-right: 0;
}
.rep-caret {
  flex: none;
  height: 26px;
  border: 1px solid var(--outline-gray-2);
  border-top-right-radius: 5px;
  border-bottom-right-radius: 5px;
  background: var(--surface-gray-3);
  color: var(--ink-gray-6);
  padding: 0 6px;
  font: inherit;
  cursor: pointer;
}
.rep-caret:hover {
  color: var(--ink-gray-9);
}
.rep-sf {
  flex: none;
  padding-left: 5px;
  color: var(--ink-gray-5);
}
/* Anchored to the RIGHT edge, never to the cell: the value column's alignment
   is a right-edge relationship, so growing leftward keeps it while letting the
   rows stay on one line. Inside the Today modal at 390px the Repairs cell is
   only 160px, which wrapped every row in half. */
.menu {
  position: absolute;
  z-index: 50;
  top: 29px;
  right: 0;
  min-width: max(260px, 100%);
  max-width: calc(100vw - 24px);
  border: 1px solid var(--outline-gray-2);
  border-radius: 6px;
  background: var(--surface-white);
  box-shadow: 0 6px 18px rgb(0 0 0 / 13%);
  padding: 3px;
  font-size: 12.5px;
}
.mrow {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 14px;
  width: 100%;
  border: 0;
  border-radius: 4px;
  background: none;
  padding: 5px 20px 5px 9px;
  font: inherit;
  text-align: left;
  color: var(--ink-gray-8);
  cursor: pointer;
}
.mrow.on {
  background: var(--surface-gray-2);
}
.mrow:hover {
  background: var(--surface-gray-3);
}
.mrow .vals b {
  color: var(--ink-gray-7);
}
.mrow.other {
  color: var(--ink-gray-6);
}
.msep {
  border-top: 1px solid var(--outline-gray-1);
  margin: 3px 0;
}

.tbl {
  flex: 1;
  min-width: 22rem;
  width: auto;
  border-collapse: collapse;
  margin-top: 0;
}
.tbl th,
.tbl td {
  padding: 3px 6px 3px 0;
  text-align: left;
  font-weight: 400;
}
.tbl th {
  color: var(--ink-gray-5);
}
.tbl td {
  border-top: 1px solid var(--outline-gray-1);
}
.tbl .n {
  text-align: right;
  font-variant-numeric: tabular-nums;
}
.tbl .street {
  max-width: 7.5rem;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--ink-gray-9);
}
.tbl .street button {
  border: 0;
  background: none;
  padding: 0;
  font: inherit;
  font-weight: inherit;
  color: var(--ink-blue-3);
  cursor: pointer;
  text-decoration: underline;
  text-underline-offset: 2px;
}
.tbl .subj td {
  background: var(--surface-blue-1);
  font-weight: 600;
  color: var(--ink-gray-9);
  border-bottom: 1px solid var(--outline-blue-1);
}
.tbl .avg td {
  color: var(--ink-gray-5);
  border-top: 1px solid var(--outline-gray-2);
}
.tbl .rm {
  width: 1.4rem;
  padding-right: 0;
  text-align: right;
}
.tbl .rm button {
  border: 0;
  background: none;
  padding: 0 2px;
  font: inherit;
  color: var(--ink-gray-4);
  cursor: pointer;
  line-height: 1;
}
.tbl .rm button:hover {
  color: var(--ink-red-3);
}
</style>
