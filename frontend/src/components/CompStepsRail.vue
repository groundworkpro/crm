<template>
  <!-- Exe's 13 comping steps beside the comps page (mockup 4A). Steps shows what each
       step decided and why; Chat talks to the same agent, and what it changes lands on
       the page; Changes is the team's log of every override with its reason.
       Rule steps run in code on agentbox; steps 6, 9 and 12 use the tool the team picked
       (Jev, a rule, or any model), and anyone can change a step with a reason. -->
  <aside class="flex min-h-0 w-[380px] shrink-0 flex-col overflow-hidden rounded-lg border border-outline-gray-2 bg-surface-white">
    <div class="flex items-center gap-1 border-b border-outline-gray-2 px-2 py-1.5">
      <button
        v-for="t in tabs"
        :key="t.key"
        type="button"
        class="rounded px-2 py-1 text-sm"
        :class="tab === t.key ? 'bg-surface-gray-3 font-medium text-ink-gray-9' : 'text-ink-gray-6 hover:bg-surface-gray-2'"
        :title="t.hint"
        @click="show(t.key)"
      >
        {{ t.label }}
      </button>
      <Button class="ml-auto" size="sm" :variant="steps.length ? 'subtle' : 'solid'" :loading="running" @click="runSteps">
        {{ steps.length ? __('Run again') : __('Run the 13 steps') }}
      </Button>
    </div>

    <!-- Any override of what the steps filled waits here until the rep says why. -->
    <div v-if="diffs.length" class="space-y-2 border-b border-outline-gray-2 bg-surface-amber-1 px-3 py-2.5 text-sm">
      <div v-for="d in diffs" :key="d.key" class="text-ink-gray-9">
        <span class="font-medium">{{ d.changed }}</span>:
        <s class="text-ink-gray-5">{{ d.old_value }}</s> {{ __('to') }} <b>{{ d.new_value }}</b>
        <span class="text-xs text-ink-gray-5">({{ __('step {0}', [d.step]) }})</span>
      </div>
      <div class="text-xs text-ink-gray-7">{{ __('Why the change? The team sees this, and the agent learns from it.') }}</div>
      <div class="flex flex-wrap gap-1">
        <button
          v-for="c in chips"
          :key="c"
          type="button"
          class="rounded-full border px-2 py-0.5 text-xs"
          :class="reasonType === c ? 'border-outline-gray-5 bg-surface-white font-medium text-ink-gray-9' : 'border-outline-gray-2 text-ink-gray-7'"
          @click="reasonType = reasonType === c ? '' : c"
        >
          {{ c }}
        </button>
      </div>
      <textarea
        v-model="why"
        rows="2"
        class="form-textarea w-full resize-none text-sm"
        :placeholder="__('Say what you saw, so the next person understands')"
      />
      <div class="flex items-center gap-2">
        <Button size="sm" variant="solid" :disabled="!why.trim()" :loading="saving" @click="saveChange('Teach the agent')">
          {{ __('Save change') }}
        </Button>
        <Button size="sm" :disabled="!why.trim()" :loading="saving" @click="saveChange('This house only')">
          {{ __('This house only') }}
        </Button>
        <Button size="sm" variant="ghost" class="ml-auto" @click="undo">{{ __('Undo') }}</Button>
      </div>
    </div>

    <div ref="scroller" class="min-h-0 flex-1 space-y-2 overflow-y-auto px-3 py-3 text-sm">
      <template v-if="tab === 'steps'">
        <p v-if="!steps.length" class="pt-1 text-ink-gray-5">
          {{ running ? __('Running the steps…') : __('Run the 13 steps to fill the comps, the value and the repairs. Change any step first if you like.') }}
        </p>
        <template v-if="cards.length">
          <div v-if="run && steps.length" class="rounded-md bg-surface-gray-1 px-3 py-2">
            <div class="flex items-baseline gap-2">
              <span class="text-base font-semibold text-ink-gray-9">{{ money(run.arv) }}</span>
              <span class="text-ink-gray-6">{{ money(run.arv_low) }} to {{ money(run.arv_high) }}</span>
              <span class="ml-auto text-xs text-ink-gray-6">{{ __('{0} confidence', [run.confidence]) }}</span>
            </div>
          </div>
          <div v-for="s in cards" :key="s.n" class="rounded-md border border-outline-gray-2 px-3 py-2">
            <div class="flex items-center gap-1.5">
              <span class="w-5 text-ink-gray-5">{{ s.n }}</span>
              <span class="font-medium text-ink-gray-9">{{ s.title }}</span>
              <span
                class="ml-auto rounded px-1.5 py-0.5 text-2xs"
                :class="toolOf(s.n) === 'code' ? 'bg-surface-gray-2 text-ink-gray-7' : 'bg-surface-gray-7 text-ink-white'"
                :title="__('What makes this step\'s call')"
              >{{ toolLabel(toolOf(s.n)) }}</span>
              <button type="button" class="whitespace-nowrap rounded px-1 text-xs text-ink-gray-5 hover:bg-surface-gray-2" @click="editStep(s.n)">
                {{ editing === s.n ? __('Close') : __('Change') }}
              </button>
            </div>
            <p v-if="s.text" class="mt-1 whitespace-pre-line pl-6 text-ink-gray-7">{{ s.text }}</p>
            <p v-if="settingText(s.n)" class="mt-1 pl-6 text-xs text-ink-gray-6">{{ settingText(s.n) }}</p>
            <!-- Change how this step runs, for the whole team. The reason goes in the Changes log. -->
            <div v-if="editing === s.n" class="mt-2 space-y-2 rounded bg-surface-gray-1 p-2 pl-6 text-xs">
              <template v-if="TOOL_STEPS.includes(s.n)">
                <label class="block text-ink-gray-7">{{ __('Who makes this call') }}
                  <select v-model="form.tool" class="form-select mt-1 w-full text-sm">
                    <option value="jev">{{ __('Jev (decision model)') }}</option>
                    <option value="code">{{ __('Rule (code, no AI)') }}</option>
                    <option v-for="m in models" :key="m" :value="'model:' + m">{{ m }}</option>
                    <option value="other">{{ __('Other model…') }}</option>
                  </select>
                </label>
                <input v-if="form.tool === 'other'" v-model="form.other" class="form-input w-full text-sm" :placeholder="__('provider/model, or hf/org/model')" />
                <label class="block text-ink-gray-7">{{ __('Team rule for this step (optional)') }}
                  <textarea v-model="form.note" rows="2" class="form-textarea mt-1 w-full resize-none text-sm" :placeholder="__('e.g. Built before 1950 and no photos: full rehab')" />
                </label>
              </template>
              <label v-else-if="s.n === 5" class="block text-ink-gray-7">{{ __('Comps within this % of the subject size') }}
                <input v-model.number="form.size_band" type="number" min="5" max="100" step="5" class="form-input mt-1 w-24 text-sm" />
              </label>
              <label v-else-if="s.n === 7" class="block text-ink-gray-7">{{ __('Farthest radius, miles') }}
                <input v-model.number="form.max_mi" type="number" min="0.25" max="5" step="0.25" class="form-input mt-1 w-24 text-sm" />
              </label>
              <p v-else class="text-ink-gray-6">{{ __('This step is a fixed rule in code. Describe the change in Chat and the agent will draft it for the team.') }}</p>
              <template v-if="editable(s.n)">
                <textarea v-model="form.why" rows="2" class="form-textarea w-full resize-none text-sm" :placeholder="__('Why the change? The team sees this.')" />
                <div class="flex gap-2">
                  <Button size="sm" variant="solid" :disabled="!form.why.trim()" :loading="saving" @click="saveStep(s.n)">{{ __('Save and rerun') }}</Button>
                  <Button size="sm" variant="ghost" @click="editing = null">{{ __('Cancel') }}</Button>
                </div>
              </template>
            </div>
          </div>
        </template>
      </template>

      <template v-else-if="tab === 'chat'">
        <p v-if="!messages.length" class="pt-4 text-center text-ink-gray-5">
          {{ __('Ask about a step, correct it, or try an idea. When the agent changes the page, it shows here too.') }}
        </p>
        <template v-for="(m, i) in messages" :key="i">
          <div v-if="m.kind === 'change'" class="rounded-md border border-outline-amber-2 bg-surface-amber-1 px-3 py-1.5 text-xs text-ink-gray-8">
            {{ m.text }}
          </div>
          <div v-else-if="m.role === 'user'" class="ml-8 rounded-lg bg-surface-gray-2 px-3 py-2 text-ink-gray-9">{{ m.text }}</div>
          <div v-else-if="m.pending" class="flex items-center gap-2 text-ink-gray-5"><LoadingIndicator class="h-4 w-4" />{{ m.text }}</div>
          <div v-else-if="m.error" class="rounded-lg bg-surface-red-1 px-3 py-2 text-ink-red-4">{{ m.text }}</div>
          <div v-else class="whitespace-pre-line rounded-lg border border-outline-gray-2 px-3 py-2 text-ink-gray-8">{{ m.text }}</div>
        </template>
      </template>

      <template v-else>
        <p v-if="!changes.length" class="pt-4 text-center text-ink-gray-5">{{ __('No changes on this house yet.') }}</p>
        <div v-for="c in changes" :key="c.name" class="rounded-md border border-outline-gray-2 px-3 py-2">
          <div class="flex items-center gap-2 text-xs text-ink-gray-5">
            <span class="font-medium text-ink-gray-8">{{ (c.user || '').split('@')[0] }}</span>
            <span>{{ when(c.creation) }}</span>
            <span class="ml-auto">{{ c.scope }}</span>
          </div>
          <div class="mt-1 text-ink-gray-9">
            <span v-if="c.step" class="text-ink-gray-5">{{ __('Step {0}', [c.step]) }}: </span>
            {{ c.changed }}: <s class="text-ink-gray-5">{{ c.old_value || '—' }}</s> {{ __('to') }} {{ c.new_value || '—' }}
          </div>
          <div class="mt-0.5 text-ink-gray-7">
            <span v-if="c.reason_type" class="font-medium">{{ c.reason_type }}: </span>{{ c.correction }}
          </div>
        </div>
      </template>
    </div>

    <form v-if="tab === 'chat'" class="flex items-end gap-2 border-t border-outline-gray-2 p-2" @submit.prevent="send">
      <textarea
        ref="input"
        v-model="draft"
        rows="2"
        class="form-textarea min-w-0 flex-1 resize-none text-sm"
        :placeholder="__('Correct a step, ask why, or try an idea')"
        @keydown.enter.exact.prevent="send"
      />
      <Button variant="solid" :loading="busy" :disabled="!draft.trim()" @click="send">{{ __('Send') }}</Button>
    </form>
  </aside>
</template>

<script setup>
import { computed, nextTick, ref, watch } from 'vue'
import { Button, LoadingIndicator, call, toast } from 'frappe-ui'
import { addressMatchKey } from '@/utils/comps'

const props = defineProps({
  lead: { type: String, required: true },
  address: { type: String, default: '' },
  view: { type: Object, default: null },
})

const tabs = [
  { key: 'steps', label: __('Steps'), hint: __('What each step decided') + ' (T)' },
  { key: 'chat', label: __('Chat'), hint: __('Talk to the agent') + ' (/)' },
  { key: 'changes', label: __('Changes'), hint: __("The team's changes and why") },
]
const CHIPS = {
  arv: [__('Model read it wrong'), __('Bad sale data'), __('Company rule'), __('Other')],
  rehabPsf: [__('I saw it in person'), __('Seller told me'), __('Photos show it'), __('Model read it wrong'), __('Company rule')],
  comps: [__('I know the street'), __('Not really updated'), __('Different buyer pool'), __('Bad sale data'), __('Other')],
}

const STEP_TITLES = ['Subject facts', 'Area price feel', 'Ceiling', 'Same side of road and river', 'Similar size, type, era',
  'Condition of each comp', 'Sold, days on market, recency', 'Flip history', 'Best comp is the anchor', 'Second comp to balance',
  'Size adjustment, conservative', 'Repairs', 'Save the calcs']
const TOOL_STEPS = [6, 9, 12]
const MODELS = ['anthropic/claude-haiku-5-5', 'anthropic/claude-sonnet-5-5', 'openai-codex/gpt-6-luna', 'antigravity/gemini-3-8-flash',
  'inco/deepseek-v4.1-flash', 'hf/Qwen/Qwen3-235B-A22B-Instruct-2507']

const tab = ref('steps')
const steps = ref([])
const run = ref(null)
const running = ref(false)
const messages = ref([])
const draft = ref('')
const busy = ref(false)
const changes = ref([])
const why = ref('')
const reasonType = ref('')
const saving = ref(false)
const setup = ref({})
const editing = ref(null)
const form = ref({})
const scroller = ref(null)
const input = ref(null)
const newChatId = () => Array.from(crypto.getRandomValues(new Uint8Array(16)), (b) => b.toString(16).padStart(2, '0')).join('')
let chatId = newChatId()

const money = (n) => (Number(n) ? '$' + Math.round(Number(n)).toLocaleString() : '—')
const when = (d) => (d ? new Date(d.replace(' ', 'T')).toLocaleString([], { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }) : '')
const rows = () => props.view?.rows?.() || []
const byAddress = (a) => rows().find((c) => addressMatchKey(c.address) === addressMatchKey(a))
const street = (c) => (c?.address || '').split(',')[0]

// The 13 step cards: the last run's text, or just titles before any run, so steps can be changed first.
const cards = computed(() => (steps.value.length ? steps.value : STEP_TITLES.map((title, i) => ({ n: i + 1, title, text: '' }))))
const models = computed(() => run.value?.models || MODELS)
const conf = (n) => setup.value[String(n)] || {}
const toolOf = (n) => (TOOL_STEPS.includes(n) ? conf(n).tool || 'jev' : 'code')
const toolLabel = (t) => (t === 'jev' ? 'Jev' : t === 'code' ? __('Rule') : t.split(':').pop().split('/').pop())
const editable = (n) => TOOL_STEPS.includes(n) || n === 5 || n === 7
function settingText(n) {
  const c = conf(n)
  if (n === 5 && c.size_band) return __('Size band: within {0}%', [Math.round(c.size_band * 100)])
  if (n === 7 && c.max_mi) return __('Radius: up to {0} mi', [c.max_mi])
  return c.note ? __('Team rule: {0}', [c.note]) : ''
}
function editStep(n) {
  if (editing.value === n) return (editing.value = null)
  const c = conf(n)
  const t = toolOf(n)
  const known = ['jev', 'code', ...models.value.map((m) => 'model:' + m)].includes(t)
  form.value = { tool: known ? t : 'other', other: known ? '' : t.replace(/^model:/, ''), note: c.note || '',
    size_band: Math.round((c.size_band || 0.3) * 100), max_mi: c.max_mi || 2, why: '' }
  editing.value = n
}
async function loadSetup() {
  try {
    setup.value = (await call('crm.api.ai_pricer.step_setup')) || {}
  } catch {
    /* Ask AI off */
  }
}
loadSetup()

// One step change for the team (from the card or from chat), logged on this lead with its reason.
async function changeStep(n, fields, reason, reasonType) {
  const r = await call('crm.api.ai_pricer.save_step', { n, reason, reason_type: reasonType, lead: props.lead, chat: chatId, ...fields })
  setup.value = r.setup || setup.value
  for (const t of r.lines || []) messages.value.push({ role: 'user', kind: 'change', text: t })
  return (r.lines || []).length
}
async function saveStep(n) {
  const f = form.value
  const fields = {}
  if (TOOL_STEPS.includes(n)) {
    fields.tool = f.tool === 'other' ? 'model:' + f.other.trim() : f.tool
    fields.note = f.note
  } else if (n === 5) fields.size_band = Number(f.size_band) / 100
  else if (n === 7) fields.max_mi = Number(f.max_mi)
  saving.value = true
  try {
    const changed = await changeStep(n, fields, f.why.trim(), '')
    editing.value = null
    if (tab.value === 'changes') loadChanges()
    if (changed) runSteps()
    else toast.info(__('Nothing changed'))
  } catch (e) {
    toast.error(e?.messages?.[0] || e?.message || __('Could not save the step.'))
  } finally {
    saving.value = false
  }
}

// What the page shows now, and what the agent last put there. Diffs need a reason.
function snapshot() {
  const v = props.view?.calc?.()?.current?.() || { arv: 0, rehabPsf: 0 }
  return { ...v, comps: rows().filter((c) => c.selected).map((c) => c.name).sort() }
}
const baseline = ref(null)
const diffs = computed(() => {
  const b = baseline.value
  if (!b) return []
  const now = snapshot()
  const out = []
  if (now.arv !== b.arv) out.push({ key: 'arv', step: 11, changed: __('Exit Price'), old_value: money(b.arv), new_value: money(now.arv) })
  if (now.rehabPsf !== b.rehabPsf)
    out.push({ key: 'rehabPsf', step: 12, changed: __('Repairs'), old_value: `$${b.rehabPsf}/sf`, new_value: `$${now.rehabPsf}/sf` })
  const name = (n) => street(rows().find((c) => c.name === n)) || n
  for (const n of b.comps.filter((x) => !now.comps.includes(x)))
    out.push({ key: 'out' + n, step: 9, changed: __('Removed comp'), old_value: name(n), new_value: '' })
  for (const n of now.comps.filter((x) => !b.comps.includes(x)))
    out.push({ key: 'in' + n, step: 9, changed: __('Used comp'), old_value: '', new_value: name(n) })
  return out
})
const chips = computed(() => CHIPS[diffs.value[0]?.key in CHIPS ? diffs.value[0].key : 'comps'])

function show(t) {
  tab.value = t
  if (t === 'changes') loadChanges()
  if (t === 'chat') nextTick(() => input.value?.focus())
  scrollDown()
}
defineExpose({ show })

function scrollDown() {
  nextTick(() => scroller.value && (scroller.value.scrollTop = scroller.value.scrollHeight))
}

async function poll(job, onRunning) {
  const started = Date.now()
  while (Date.now() - started < 5 * 60 * 1000) {
    await new Promise((r) => setTimeout(r, 2000))
    const r = await call('crm.api.ai_pricer.get_job', { job })
    if (r.status === 'done') return r
    if (r.status === 'error') throw new Error(r.error)
    onRunning?.(Math.round((Date.now() - started) / 1000))
  }
  throw new Error(__('The agent took too long. Try again.'))
}

async function settle() {
  await nextTick()
  baseline.value = snapshot()
}

// Fill the page from a 13-step run: comps (steps 9 and 10), value (11), repairs (12).
async function applyRun(d) {
  run.value = d
  steps.value = d.steps || []
  await props.view?.openCalc?.()
  props.view?.calc?.()?.prefill?.({ arv: Number(d.arv) || 0, rehabPsf: Number(d.repairs?.per_sf) || 30, mult: 2 })
  const marks = {}
  for (const u of d.comps_used || []) {
    const c = byAddress(u.address)
    if (!c) continue
    marks[c.name] = { why: `Step ${u.role === 'anchor' ? 9 : 10}: ${u.role}. ${u.why || ''}`.trim() }
    if (!c.selected) await props.view.setCompState(c.name, 'selected')
  }
  props.view?.markSteps?.(marks)
  await settle()
}

async function runSteps(opts) {
  if (running.value) return
  running.value = true
  if (opts?.stay !== true) tab.value = 'steps'
  try {
    const { job } = await call('crm.api.ai_pricer.ask', { lead: props.lead, kind: 'decide', chat: chatId })
    const r = await poll(job)
    await applyRun(r.decide)
    messages.value.push({ role: 'ai', text: __('Ran the 13 steps: ARV {0}, repairs ${1}/sf.', [money(r.decide.arv), r.decide.repairs?.per_sf]) })
  } catch (e) {
    toast.error(e?.messages?.[0] || e?.message || __('Could not run the steps.'))
  } finally {
    running.value = false
  }
}

async function logChange(d, reason, reason_type, scope) {
  const r = await call('crm.api.ai_pricer.log_change', {
    lead: props.lead, reason, reason_type, scope, chat: chatId,
    step: d.step, changed: d.changed, old_value: d.old_value, new_value: d.new_value,
  })
  messages.value.push({ role: 'user', kind: 'change', text: r.text })
}

async function saveChange(scope) {
  if (!why.value.trim() || saving.value) return
  saving.value = true
  try {
    for (const d of diffs.value) await logChange(d, why.value.trim(), reasonType.value, scope)
    why.value = ''
    reasonType.value = ''
    await settle()
    if (tab.value === 'changes') loadChanges()
    toast.success(__('Change saved for the team'))
  } catch (e) {
    toast.error(e?.messages?.[0] || e?.message || __('Could not save the change.'))
  } finally {
    saving.value = false
  }
}

async function undo() {
  const b = baseline.value
  props.view?.calc?.()?.prefill?.({ arv: b.arv, rehabPsf: b.rehabPsf })
  const now = snapshot().comps
  for (const n of b.comps.filter((x) => !now.includes(x))) await props.view.setCompState(n, 'selected')
  for (const n of now.filter((x) => !b.comps.includes(x))) await props.view.setCompState(n, 'none')
  why.value = ''
}

// What the rep sees, sent with each chat message so the agent can point at it and change it.
function pageText() {
  const v = snapshot()
  const s = props.view?.subject?.() || {}
  const line = (c) => `- ${c.address}: ${money(c.price)}, ${c.square_footage || '?'} sf`
  const used = rows().filter((c) => c.selected)
  const other = rows().filter((c) => !c.selected).slice(0, 25)
  return [
    `Subject: ${props.address}, ${s.sqft || '?'} sf`,
    `Calculator: Exit Price ${money(v.arv)}, repairs $${v.rehabPsf}/sf`,
    'Comps in use:', ...used.map(line),
    'Other comps on the map:', ...other.map(line),
  ].join('\n')
}

// Apply what the agent said to change, and log each one with the rep's message as the reason.
async function applyActions(actions, question) {
  let rerun = false
  for (const a of (actions || []).filter((x) => x.do === 'step')) {
    const f = Object.fromEntries(['tool', 'note', 'size_band', 'max_mi'].filter((k) => k in a).map((k) => [k, a[k]]))
    rerun = (await changeStep(Number(a.n), f, question, __('Chat')).catch((e) => {
      messages.value.push({ role: 'ai', error: true, text: e?.messages?.[0] || e?.message || __('Could not change step {0}.', [a.n]) })
      return 0
    })) || rerun
  }
  actions = (actions || []).filter((x) => x.do !== 'step')
  if (actions.length) await props.view?.openCalc?.()
  const calc = props.view?.calc?.()
  for (const a of actions) {
    const before = snapshot()
    let d = null
    if (a.do === 'repairs_psf' && Number(a.value)) {
      calc?.prefill?.({ rehabPsf: Number(a.value) })
      d = { step: 12, changed: __('Repairs'), old_value: `$${before.rehabPsf}/sf`, new_value: `$${Number(a.value)}/sf` }
    } else if (a.do === 'arv' && Number(a.value)) {
      calc?.prefill?.({ arv: Number(a.value) })
      d = { step: 11, changed: __('Exit Price'), old_value: money(before.arv), new_value: money(a.value) }
    } else if (a.do === 'remove' || a.do === 'use') {
      const c = byAddress(a.address || '')
      if (!c) continue
      await props.view.setCompState(c.name, a.do === 'use' ? 'selected' : 'none')
      d = a.do === 'use'
        ? { step: 9, changed: __('Used comp'), old_value: '', new_value: street(c) }
        : { step: 9, changed: __('Removed comp'), old_value: street(c), new_value: '' }
    }
    if (d) await logChange(d, question, __('Chat'), 'Teach the agent').catch(() => {})
  }
  if (baseline.value) await settle()
  if (rerun) runSteps({ stay: true })
}

async function send() {
  const q = draft.value.trim()
  if (!q || busy.value) return
  draft.value = ''
  busy.value = true
  const hist = messages.value.filter((m) => !m.pending && !m.error).slice(-12).map((m) => ({ role: m.role, text: m.text }))
  messages.value.push({ role: 'user', text: q })
  const waiting = { role: 'ai', pending: true, text: __('Thinking…') }
  messages.value.push(waiting)
  scrollDown()
  try {
    const { job } = await call('crm.api.ai_pricer.ask', {
      lead: props.lead, kind: 'ask', question: q, history: JSON.stringify(hist), chat: chatId, page: pageText(),
    })
    const r = await poll(job)
    messages.value.splice(messages.value.indexOf(waiting), 1, { role: 'ai', text: r.text })
    await applyActions(r.actions, q)
  } catch (e) {
    messages.value.splice(messages.value.indexOf(waiting), 1, { role: 'ai', error: true, text: e?.messages?.[0] || e?.message || __('Could not reach the agent.') })
  } finally {
    busy.value = false
    scrollDown()
  }
}

async function loadChanges() {
  try {
    changes.value = await call('crm.api.ai_pricer.changes', { lead: props.lead })
  } catch {
    changes.value = []
  }
}

// Pick the last chat back up, with its last 13-step run shown (not re-applied: the page
// keeps what was saved; Run again refills it).
watch(
  () => props.lead,
  async (lead) => {
    try {
      const h = await call('crm.api.ai_pricer.chat_history', { lead })
      if (!h?.chat) return
      chatId = h.chat
      messages.value = h.messages
        .filter((m) => !m.decide)
        .map((m) => ({ role: m.role, kind: m.kind, text: m.text, error: m.error }))
      const last = [...h.messages].reverse().find((m) => m.decide)
      if (last) {
        run.value = last.decide
        steps.value = last.decide.steps || []
      }
    } catch {
      /* Ask AI off or offline: the rail stays empty */
    }
  },
  { immediate: true },
)
</script>
