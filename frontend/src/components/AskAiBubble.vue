<template>
  <!-- Ask AI (mockup 1B): a bubble on every page that opens a chat with the
       property pricer. On a lead page it uses that lead; elsewhere the rep picks one.
       The pricing itself runs on agentbox (crm/api/ai_pricer.py explains why). -->
  <div v-if="enabled" class="fixed bottom-5 right-5 z-40 flex flex-col items-end gap-3">
    <div
      v-if="open"
      class="flex h-[min(640px,calc(100vh-7rem))] w-[400px] flex-col overflow-hidden rounded-xl border bg-surface-white shadow-2xl"
    >
      <div class="flex items-center gap-2 border-b px-3 py-2.5">
        <FeatherIcon name="zap" class="h-4 w-4 text-ink-gray-7" />
        <div class="text-base font-semibold text-ink-gray-9">{{ __('Ask AI') }}</div>
        <span
          class="ml-1 rounded px-1.5 py-0.5 text-xs"
          :class="online ? 'bg-surface-green-2 text-ink-green-7' : 'bg-surface-gray-2 text-ink-gray-6'"
        >
          {{ online ? __('Pricer online') : __('Pricer offline') }}
        </span>
        <Button variant="ghost" class="ml-auto" :title="__('New chat')" @click="reset">
          <template #icon><FeatherIcon name="refresh-cw" class="h-3.5 w-3.5" /></template>
        </Button>
        <Button variant="ghost" :title="__('Close')" @click="open = false">
          <template #icon><FeatherIcon name="x" class="h-4 w-4" /></template>
        </Button>
      </div>

      <div class="flex items-center gap-2 border-b bg-surface-gray-1 px-3 py-2 text-sm">
        <template v-if="lead">
          <FeatherIcon name="home" class="h-3.5 w-3.5 shrink-0 text-ink-gray-5" />
          <span class="truncate text-ink-gray-8" :title="lead.address">{{ lead.address || lead.name }}</span>
          <button class="ml-auto shrink-0 text-ink-gray-5 hover:text-ink-gray-8" @click="clearLead">
            {{ __('Change') }}
          </button>
        </template>
        <div v-else class="relative w-full">
          <TextInput
            v-model="search"
            :placeholder="__('Which lead? Type part of the address…')"
            class="w-full"
            @update:modelValue="findLeads"
          />
          <div v-if="matches.length" class="absolute left-0 right-0 top-full z-10 mt-1 rounded-lg border bg-surface-white py-1 shadow-lg">
            <button
              v-for="m in matches"
              :key="m.name"
              class="block w-full truncate px-3 py-1.5 text-left text-sm hover:bg-surface-gray-2"
              @click="pickLead(m.name)"
            >
              {{ m.property_address || m.lead_name || m.name }}
              <span class="text-ink-gray-5">· {{ m.lead_name || m.name }}</span>
            </button>
          </div>
        </div>
      </div>

      <div ref="scroller" class="flex-1 space-y-3 overflow-y-auto px-3 py-3">
        <div v-if="!messages.length" class="pt-6 text-center text-sm text-ink-gray-5">
          <p v-if="lead">{{ __('Price this house: comps, after-repair value and offers in about a minute.') }}</p>
          <p v-else>{{ __('Pick a lead to price it.') }}</p>
          <Button v-if="lead" class="mt-3" variant="solid" :loading="busy" @click="price">
            {{ __('Price this house') }}
          </Button>
        </div>

        <template v-for="(m, i) in messages" :key="i">
          <div v-if="m.role === 'user'" class="ml-10 rounded-lg bg-surface-gray-2 px-3 py-2 text-base text-ink-gray-9">
            {{ m.text }}
          </div>
          <div v-else-if="m.pending" class="flex items-center gap-2 text-sm text-ink-gray-5">
            <LoadingIndicator class="h-4 w-4" />
            {{ m.text }}
          </div>
          <div v-else-if="m.error" class="rounded-lg bg-surface-red-1 px-3 py-2 text-sm text-ink-red-4">{{ m.text }}</div>
          <div v-else-if="m.result" class="rounded-lg border px-3 py-3 text-base text-ink-gray-8">
            <div class="text-xs uppercase tracking-wide text-ink-gray-5">{{ __('After-repair value') }}</div>
            <div class="mt-0.5 flex items-baseline gap-2">
              <span class="text-2xl font-semibold text-ink-gray-9">{{ money(m.result.arv) }}</span>
              <span class="text-sm text-ink-gray-5">{{ money(m.result.arv_low) }} – {{ money(m.result.arv_high) }}</span>
              <span class="ml-auto rounded px-1.5 py-0.5 text-xs" :class="confClass(m.result.confidence)">
                {{ confLabel(m.result.confidence) }}
              </span>
            </div>
            <p v-if="m.result.subject_note" class="mt-2 text-sm text-ink-gray-6">{{ m.result.subject_note }}</p>

            <div v-if="m.result.comps_used?.length" class="mt-3">
              <div class="mb-1 text-xs text-ink-gray-5">{{ __('Comps used') }}</div>
              <div v-for="c in m.result.comps_used" :key="c.address" class="border-t py-1.5 text-sm">
                <div class="flex gap-2">
                  <span class="truncate text-ink-gray-8">{{ c.address }}</span>
                  <span class="ml-auto shrink-0 font-medium text-ink-gray-9">{{ money(c.price) }}</span>
                </div>
                <div class="text-ink-gray-5">{{ c.why }}</div>
              </div>
            </div>

            <div v-if="m.result.offers" class="mt-3 grid grid-cols-2 gap-2 text-sm">
              <div
                v-for="o in offerRows(m.result.offers)"
                :key="o.key"
                class="rounded-md border px-2 py-1.5"
                :class="o.recommended ? 'border-outline-gray-4 bg-surface-gray-1' : ''"
              >
                <div class="text-ink-gray-5">{{ o.label }}<span v-if="o.recommended"> · {{ __('use this') }}</span></div>
                <div class="font-semibold text-ink-gray-9">{{ money(o.value) }}</div>
              </div>
              <div class="col-span-2 text-xs text-ink-gray-5">
                {{ __('Repairs {0} (kitchen & baths tier), fee {1}', [money(m.result.offers.repairs), money(m.result.offers.fee)]) }}
              </div>
            </div>
            <p v-if="m.result.would_change_it" class="mt-2 text-xs text-ink-gray-5">
              {{ __('Would change it:') }} {{ m.result.would_change_it }}
            </p>

            <div class="mt-3 flex flex-wrap gap-2">
              <SaveArvButton
                :lead="lead?.name"
                :arv="m.result.arv"
                :arv-low="m.result.arv_low"
                :arv-high="m.result.arv_high"
                :offer="m.result.offers?.offer"
              />
              <PricerCompsLink :lead="lead?.name" :job="m.job" />
              <button
                class="inline-flex h-7 items-center gap-1.5 rounded bg-surface-gray-2 px-2 text-base text-ink-gray-8 hover:bg-surface-gray-3"
                @click="feedbackFor = m.result"
              >
                <FeatherIcon name="thumbs-down" class="h-3.5 w-3.5" />
                {{ __('Disagree?') }}
              </button>
            </div>
          </div>
          <div v-else class="whitespace-pre-wrap rounded-lg border px-3 py-2 text-base text-ink-gray-8">{{ m.text }}</div>
        </template>
      </div>

      <!-- One composer box: the text grows with what is typed (up to ~5 lines),
           no resize handle, and the send button sits inside it. -->
      <form class="border-t px-3 py-2.5" @submit.prevent="send">
        <div
          class="flex items-end gap-1 rounded-lg border border-outline-gray-2 bg-surface-white py-1 pl-3 pr-1 focus-within:border-outline-gray-4"
          :class="!lead || busy ? 'opacity-60' : ''"
        >
          <textarea
            ref="input"
            v-model="draft"
            rows="1"
            class="max-h-[120px] min-w-0 flex-1 resize-none border-none bg-transparent px-0 py-1 text-base leading-5 text-ink-gray-9 placeholder:text-ink-gray-4 focus:outline-none focus:ring-0"
            :disabled="!lead || busy"
            :placeholder="lead ? __('Ask about this house…') : __('Pick a lead first')"
            :title="lead ? __('e.g. what if it needs a full rehab?') : ''"
            @input="grow"
            @keydown.enter.exact.prevent="send"
          />
          <Button variant="solid" type="submit" :disabled="!lead || busy || !draft.trim()" :title="__('Send')">
            <template #icon><FeatherIcon name="send" class="h-3.5 w-3.5" /></template>
          </Button>
        </div>
      </form>
    </div>

    <button
      class="flex h-12 items-center gap-2 rounded-full bg-surface-gray-7 px-4 text-base font-medium text-ink-white shadow-xl hover:bg-surface-gray-6"
      :title="__('Ask AI to price a house')"
      @click="toggle"
    >
      <FeatherIcon :name="open ? 'chevron-down' : 'zap'" class="h-4 w-4" />
      {{ __('Ask AI') }}
    </button>

    <ArvFeedbackModal
      v-if="feedbackFor"
      :model-value="!!feedbackFor"
      :arv="feedbackFor.arv"
      :lead="lead?.name"
      :address="lead?.address"
      :details="feedbackDetails(feedbackFor)"
      @update:modelValue="(v) => !v && (feedbackFor = null)"
    />
  </div>
</template>

<script setup>
import { ref, watch, nextTick } from 'vue'
import { useRoute } from 'vue-router'
import { Button, FeatherIcon, LoadingIndicator, TextInput, call } from 'frappe-ui'
import SaveArvButton from '@/components/SaveArvButton.vue'
import PricerCompsLink from '@/components/PricerCompsLink.vue'
import ArvFeedbackModal from '@/components/Modals/ArvFeedbackModal.vue'

const route = useRoute()
const enabled = ref(false)
const online = ref(false)
const open = ref(false)
const lead = ref(null)
const search = ref('')
const matches = ref([])
const messages = ref([])
const draft = ref('')
const busy = ref(false)
const feedbackFor = ref(null)
const scroller = ref(null)
const input = ref(null)

function grow() {
  const el = input.value
  if (!el) return
  el.style.height = 'auto'
  el.style.height = Math.min(el.scrollHeight, 120) + 'px'
}

call('crm.api.ai_pricer.status')
  .then((r) => {
    enabled.value = !!r?.enabled
    online.value = !!r?.online
  })
  .catch(() => {})

const money = (n) => (n || n === 0 ? '$' + Math.round(n).toLocaleString('en-US') : '—')
const confLabel = (c) => ({ high: __('High confidence'), med: __('Medium confidence'), low: __('Low confidence') })[c] || c
const confClass = (c) =>
  c === 'high' ? 'bg-surface-green-2 text-ink-green-7' : c === 'low' ? 'bg-surface-amber-2 text-ink-amber-7' : 'bg-surface-gray-2 text-ink-gray-7'
const offerRows = (o) => [
  { key: 'classic', label: __('Classic 70%'), value: o.classic, recommended: o.recommend === 'classic' },
  { key: 'two_x', label: __('2× repairs'), value: o.two_x, recommended: o.recommend === 'two_x' },
]
const feedbackDetails = (r) =>
  [
    `AI range ${money(r.arv_low)}–${money(r.arv_high)}, ${r.confidence} confidence`,
    ...(r.comps_used || []).map((c) => `Comp: ${c.address} ${money(c.price)} — ${c.why}`),
    r.offers ? `Repairs ${money(r.offers.repairs)}, fee ${money(r.offers.fee)}, classic ${money(r.offers.classic)}, 2x ${money(r.offers.two_x)}` : '',
  ]
    .filter(Boolean)
    .join('\n')

async function pickLead(name) {
  matches.value = []
  search.value = ''
  if (lead.value?.name === name) return
  messages.value = []
  try {
    lead.value = await call('crm.api.ai_pricer.lead_info', { lead: name })
  } catch {
    lead.value = null
  }
}

function clearLead() {
  lead.value = null
  messages.value = []
}

let searchTimer
function findLeads(q) {
  clearTimeout(searchTimer)
  if (!q || q.trim().length < 3) return (matches.value = [])
  searchTimer = setTimeout(async () => {
    matches.value = await call('frappe.client.get_list', {
      doctype: 'CRM Lead',
      filters: [['property_address', 'like', `%${q.trim()}%`]],
      fields: ['name', 'lead_name', 'property_address'],
      order_by: 'modified desc',
      limit_page_length: 6,
    }).catch(() => [])
  }, 250)
}

// Follow the lead page the rep is on; leave a picked lead alone elsewhere.
watch(
  () => route.params.leadId,
  (id) => id && open.value && pickLead(id),
)

function toggle() {
  open.value = !open.value
  if (open.value && route.params.leadId) pickLead(route.params.leadId)
  call('crm.api.ai_pricer.status').then((r) => (online.value = !!r?.online)).catch(() => {})
}

function reset() {
  messages.value = []
}

function scrollDown() {
  nextTick(() => scroller.value && (scroller.value.scrollTop = scroller.value.scrollHeight))
}

// The model sees the conversation as text; a price card becomes one summary line.
function history() {
  return messages.value
    .filter((m) => !m.pending && !m.error)
    .map((m) =>
      m.result
        ? { role: 'ai', text: `Priced it: ARV ${money(m.result.arv)} (${money(m.result.arv_low)}–${money(m.result.arv_high)}), ${m.result.confidence} confidence, repair tier ${m.result.repair_tier}, offer ${money(m.result.offers?.offer)}. Comps: ${(m.result.comps_used || []).map((c) => `${c.address} ${money(c.price)}`).join('; ')}` }
        : { role: m.role, text: m.text },
    )
}

async function run(kind, question = '') {
  const forLead = lead.value?.name
  busy.value = true
  const waiting = {
    role: 'ai',
    pending: true,
    text: kind === 'price' ? __('Pulling comps and pricing… about a minute') : __('Thinking…'),
  }
  const hist = history()
  if (question) messages.value.push({ role: 'user', text: question })
  messages.value.push(waiting)
  scrollDown()
  const idx = messages.value.length - 1
  const settle = (m) => {
    if (lead.value?.name === forLead) messages.value.splice(idx, 1, m)
    busy.value = false
    scrollDown()
  }
  try {
    const { job } = await call('crm.api.ai_pricer.ask', { lead: forLead, kind, question, history: JSON.stringify(hist) })
    const started = Date.now()
    while (Date.now() - started < 5 * 60 * 1000) {
      await new Promise((r) => setTimeout(r, 2500))
      if (lead.value?.name !== forLead) return (busy.value = false)
      const r = await call('crm.api.ai_pricer.get_job', { job })
      if (r.status === 'done') return settle(r.answer ? { role: 'ai', result: r.answer, job } : { role: 'ai', text: r.text })
      if (r.status === 'error') return settle({ role: 'ai', error: true, text: r.error })
      if (r.status === 'running' && kind === 'price') messages.value[idx].text = __('Pulling comps and pricing… {0}s', [Math.round((Date.now() - started) / 1000)])
    }
    settle({ role: 'ai', error: true, text: __('The pricer took too long. Try again.') })
  } catch (e) {
    settle({ role: 'ai', error: true, text: e?.messages?.[0] || e?.message || __('Could not reach the pricer.') })
  }
}

const price = () => run('price')

function send() {
  const q = draft.value.trim()
  if (!q || !lead.value || busy.value) return
  draft.value = ''
  nextTick(grow)
  run('ask', q)
}
</script>
