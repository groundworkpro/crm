<template>
  <!-- Exe's 13 comping steps beside the comps page (mockup 4A). Steps shows what each
       step decided and why; Chat talks to the same agent and shows its numbers with the
       math (it never writes the rep's calculator or picks, Exe 2026-11-05); Changes is the team's log of every override with its reason.
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

    <div ref="scroller" class="min-h-0 flex-1 space-y-2 overflow-y-auto px-3 py-3 text-sm">
      <template v-if="tab === 'steps'">
        <p v-if="!steps.length" class="pt-1 text-ink-gray-5">
          {{ running ? __('Running the steps…') : __('Run the 13 steps to see the comps, value, repairs and offers Ask AI would use. Your calculator and picks stay yours. Change any step first if you like.') }}
        </p>
        <!-- A team member's step changes are suggestions: they run on this house for them only (Exe, 2026-11-04). -->
        <div v-if="mine.length" class="rounded-md border border-outline-amber-2 bg-surface-amber-1 px-3 py-2 text-xs text-ink-gray-8">
          {{ __('Your changes to step {0} apply only to your runs of this house. They are saved as suggestions for review and do not change the team setup.', [mine.join(', ')]) }}
          <button type="button" class="ml-1 underline" :disabled="running" @click="useTeamSetup">{{ __('Use the team setup') }}</button>
        </div>
        <template v-if="cards.length">
          <div v-if="run && steps.length" class="rounded-md bg-surface-gray-1 px-3 py-2">
            <div class="flex items-baseline gap-2">
              <span class="text-base font-semibold text-ink-gray-9">{{ money(run.arv) }}</span>
              <span class="text-ink-gray-6">{{ money(run.arv_low) }} to {{ money(run.arv_high) }}</span>
              <span class="ml-auto text-xs text-ink-gray-6">{{ __('{0} confidence', [run.confidence]) }}</span>
            </div>
            <LowConfidenceWhy
              v-if="run.confidence === 'low'"
              :why="run.low_why"
              :subject="run.subject"
              :busy="running"
              @rerun="(facts) => runSteps({ facts })"
            />
          </div>
          <div v-for="s in cards" :key="s.n" class="rounded-md border border-outline-gray-2 px-3 py-2">
            <div class="flex items-center gap-1.5">
              <span class="w-5 text-ink-gray-5">{{ s.n }}</span>
              <span class="font-medium text-ink-gray-9">{{ s.title }}</span>
              <span v-if="mine.includes(String(s.n))" class="rounded bg-surface-amber-2 px-1 text-2xs text-ink-amber-3" :title="__('Your change, this house only')">{{ __('yours') }}</span>
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
            <!-- Change how this step runs (team setup for the owner, this house only for anyone else). The reason goes in the Changes log. -->
            <div v-if="editing === s.n" class="mt-2 space-y-2 rounded bg-surface-gray-1 p-2 pl-6 text-xs">
              <label class="block text-ink-gray-7">{{ __('Who makes this call') }}
                <select v-model="form.tool" class="form-select mt-1 w-full text-sm">
                  <optgroup :label="__('Code')">
                    <option value="code">{{ __('Rule (code, no AI)') }}</option>
                  </optgroup>
                  <optgroup :label="__('Decision model')">
                    <option value="jev">{{ __('Jev') }}</option>
                  </optgroup>
                  <optgroup :label="__('Language model')">
                    <option v-for="m in models" :key="m" :value="'model:' + m">{{ m }}</option>
                    <option value="other">{{ __('Other model…') }}</option>
                  </optgroup>
                </select>
              </label>
              <p class="text-ink-gray-6">{{ form.tool === 'code' ? __('Code runs the rule as written.') : __('The AI decides: {0}.', [STEP_CALLS[s.n]]) }}</p>
              <input v-if="form.tool === 'other'" v-model="form.other" class="form-input w-full text-sm" :placeholder="__('provider/model, or hf/org/model')" />
              <label class="block text-ink-gray-7">{{ teamWide ? __('Team rule for this step (optional, read by the AI)') : __('Rule for this house (optional, read by the AI)') }}
                <textarea v-model="form.note" rows="2" class="form-textarea mt-1 w-full resize-none text-sm" :placeholder="__('e.g. Built before 1950 and no photos: full rehab')" />
              </label>
              <label v-if="s.n === 5" class="block text-ink-gray-7">{{ __('Comps within this % of the subject size') }}
                <input v-model.number="form.size_band" type="number" min="5" max="100" step="5" class="form-input mt-1 w-24 text-sm" />
              </label>
              <label v-if="s.n === 7" class="block text-ink-gray-7">{{ __('Farthest radius, miles') }}
                <input v-model.number="form.max_mi" type="number" min="0.25" max="5" step="0.25" class="form-input mt-1 w-24 text-sm" />
              </label>
              <template v-if="editable(s.n)">
                <textarea v-model="form.why" rows="2" class="form-textarea w-full resize-none text-sm" :placeholder="teamWide ? __('Why the change? It changes the team setup.') : __('Why the change? Saved as a suggestion for review.')" />
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
          {{ __('Ask about a step, correct it, or try an idea. Ask AI\'s numbers and the math behind them show here; your calculator stays yours.') }}
        </p>
        <template v-for="(m, i) in messages" :key="i">
          <div v-if="m.kind === 'change'" class="rounded-md border border-outline-amber-2 bg-surface-amber-1 px-3 py-1.5 text-xs text-ink-gray-8">
            {{ m.text }}
          </div>
          <div v-else-if="m.role === 'user'" class="ml-8 rounded-lg bg-surface-gray-2 px-3 py-2 text-ink-gray-9">{{ m.text }}</div>
          <div v-else-if="m.pending" class="flex items-center gap-2 text-ink-gray-5"><LoadingIndicator class="h-4 w-4" />{{ m.text }}</div>
          <div v-else-if="m.kind === 'calc'" class="whitespace-pre-wrap rounded-lg border border-outline-gray-2 bg-surface-gray-1 px-3 py-2 text-ink-gray-8 tabular-nums">{{ m.text }}</div>
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
import { useRoute } from 'vue-router'
import { Button, LoadingIndicator, call, toast } from 'frappe-ui'
import { addressMatchKey } from '@/utils/comps'
import LowConfidenceWhy from '@/components/LowConfidenceWhy.vue'
import { aiCalcText } from '@/utils/aiCalc'
import { JEV_STEPS, MODELS, STEP_CALLS, STEP_TITLES, toolLabel } from '@/utils/compSteps'

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
const tab = ref('steps')
const steps = ref([])
const run = ref(null)
const running = ref(false)
const messages = ref([])
const draft = ref('')
const busy = ref(false)
const changes = ref([])
const saving = ref(false)
const setup = ref({})
const mine = ref([]) // steps this user changed for this house only
const teamWide = ref(false) // the pricer's owner: step changes go to the team setup
const editing = ref(null)
const form = ref({})
const scroller = ref(null)
const input = ref(null)
const newChatId = () => Array.from(crypto.getRandomValues(new Uint8Array(16)), (b) => b.toString(16).padStart(2, '0')).join('')
let chatId = newChatId()
const route = useRoute()

const money = (n) => (Number(n) ? '$' + Math.round(Number(n)).toLocaleString() : '—')
const when = (d) => (d ? new Date(d.replace(' ', 'T')).toLocaleString([], { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }) : '')
const rows = () => props.view?.rows?.() || []
const byAddress = (a) => rows().find((c) => addressMatchKey(c.address) === addressMatchKey(a))
const street = (c) => (c?.address || '').split(',')[0]

// The 13 step cards: the last run's text, or just titles before any run, so steps can be changed first.
const cards = computed(() => (steps.value.length ? steps.value : STEP_TITLES.map((title, i) => ({ n: i + 1, title, text: '' }))))
const models = computed(() => run.value?.models || MODELS)
const conf = (n) => setup.value[String(n)] || {}
const toolOf = (n) => conf(n).tool || (JEV_STEPS.includes(n) ? 'jev' : 'code')
const editable = (n) => n >= 1 && n <= 13
function settingText(n) {
  const c = conf(n)
  const out = []
  if (n === 5 && c.size_band) out.push(__('Size band: within {0}%', [Math.round(c.size_band * 100)]))
  if (n === 7 && c.max_mi) out.push(__('Radius: up to {0} mi', [c.max_mi]))
  if (c.note) out.push(mine.value.includes(String(n)) ? __('Your rule: {0}', [c.note]) : __('Team rule: {0}', [c.note]))
  return out.join('. ')
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
function takeSetup(r) {
  if (!r) return
  setup.value = r.setup || {}
  mine.value = r.mine || []
  teamWide.value = !!r.team_wide
}
async function loadSetup() {
  try {
    takeSetup(await call('crm.api.ai_pricer.step_setup', { lead: props.lead, detail: 1 }))
  } catch {
    /* Ask AI off */
  }
}
watch(() => props.lead, loadSetup, { immediate: true })
async function useTeamSetup() {
  try {
    takeSetup(await call('crm.api.ai_pricer.clear_run_setup', { lead: props.lead }))
    runSteps()
  } catch (e) {
    toast.error(e?.messages?.[0] || e?.message || __('Could not reset the steps.'))
  }
}

// One step change (from the card or from chat), logged on this lead with its reason and filed as a
// suggestion. The pricer's owner changes the team setup; anyone else changes their own runs of this house.
async function changeStep(n, fields, reason, reasonType) {
  const r = await call('crm.api.ai_pricer.save_step', { n, reason, reason_type: reasonType, lead: props.lead, chat: chatId, ...fields })
  setup.value = r.setup || setup.value
  if (!r.team_wide && (r.lines || []).length && !mine.value.includes(String(n))) mine.value = [...mine.value, String(n)].sort((a, b) => a - b)
  for (const t of r.lines || []) messages.value.push({ role: 'user', kind: 'change', text: t })
  return (r.lines || []).length
}
async function saveStep(n) {
  const f = form.value
  const fields = {}
  fields.tool = f.tool === 'other' ? 'model:' + f.other.trim() : f.tool
  fields.note = f.note
  if (n === 5) fields.size_band = Number(f.size_band) / 100
  if (n === 7) fields.max_mi = Number(f.max_mi)
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

// What the page shows now (the rep's calculator and picks), for the chat's context.
function snapshot() {
  const v = props.view?.calc?.()?.current?.() || { arv: 0, rehabPsf: 0 }
  return { ...v, comps: rows().filter((c) => c.selected).map((c) => c.name).sort() }
}

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

// Show a 13-step run: its steps, and "AI comp" marks on the board's cards. It does not
// touch the calculator or the rep's picks; its comps list apart in the tray, and its
// numbers go in the chat with the math (Exe, 2026-11-05).
function applyRun(d) {
  run.value = d
  steps.value = d.steps || []
  const marks = {}
  for (const u of d.comps_used || []) {
    const c = byAddress(u.address)
    if (c) marks[c.name] = { why: `Step ${u.role === 'anchor' ? 9 : 10}: ${u.role}. ${u.why || ''}`.trim() }
  }
  props.view?.markSteps?.(marks)
}

/** Ask AI's numbers and the math, as an AI chat message. */
function calcMessage(d) {
  const text = aiCalcText(d, d?.subject?.sqft || props.view?.subject?.()?.sqft)
  return text ? { role: 'ai', kind: 'calc', text } : null
}

async function runSteps(opts) {
  if (running.value) return
  running.value = true
  if (opts?.stay !== true) tab.value = 'steps'
  try {
    const { job } = await call('crm.api.ai_pricer.ask', {
      lead: props.lead, kind: 'decide', chat: chatId, ...(opts?.facts ? { facts: JSON.stringify(opts.facts) } : {}),
    })
    const r = await poll(job)
    applyRun(r.decide)
    const m = calcMessage(r.decide)
    if (m) messages.value.push(m)
  } catch (e) {
    toast.error(e?.messages?.[0] || e?.message || __('Could not run the steps.'))
  } finally {
    running.value = false
  }
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

// Apply what the agent said to change. Step changes the rep asked for are applied (they
// are logged as suggestions by save_step). A value, repairs figure or comp is only said in
// the chat: the calculator and picks are the rep's (Exe, 2026-11-05).
async function applyActions(actions, question) {
  let rerun = false
  for (const a of (actions || []).filter((x) => x.do === 'step')) {
    const f = Object.fromEntries(['tool', 'note', 'size_band', 'max_mi'].filter((k) => k in a).map((k) => [k, a[k]]))
    rerun = (await changeStep(Number(a.n), f, question, __('Chat')).catch((e) => {
      messages.value.push({ role: 'ai', error: true, text: e?.messages?.[0] || e?.message || __('Could not change step {0}.', [a.n]) })
      return 0
    })) || rerun
  }
  const v = snapshot()
  const said = (text) => messages.value.push({ role: 'ai', kind: 'calc', text: text + ' ' + __('Not applied; change it yourself if you agree.') })
  for (const a of (actions || []).filter((x) => x.do !== 'step')) {
    if (a.do === 'repairs_psf' && Number(a.value)) {
      said(__('Ask AI would use repairs of ${0}/sf (your calculator: ${1}/sf).', [Number(a.value), v.rehabPsf || 0]))
    } else if (a.do === 'arv' && Number(a.value)) {
      said(__('Ask AI would use an Exit Price of {0} (your calculator: {1}).', [money(a.value), money(v.arv)]))
    } else if (a.do === 'remove' || a.do === 'use') {
      const c = byAddress(a.address || '')
      if (!c) continue
      const picked = v.comps.includes(c.name)
      if (a.do === 'use' && !picked) said(__('Ask AI would add {0} to your comps.', [street(c)]))
      else if (a.do === 'remove' && picked) said(__('Ask AI would take {0} out of your comps.', [street(c)]))
    }
  }
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

// A price job's step cards while it is still in Redis (cards logged before 2026-11-04 kept only the price).
async function jobSteps(job) {
  if (!job) return null
  try {
    const r = await call('crm.api.ai_pricer.get_job', { job })
    return r?.status === 'done' ? r.decide || null : null
  } catch {
    return null
  }
}

// Pick the last chat back up, with its last 13-step run shown (never applied to the page).
// Each Ask AI run in the chat (13 steps here, or a price card) shows as its numbers and math.
watch(
  () => props.lead,
  async (lead) => {
    try {
      const h = await call('crm.api.ai_pricer.chat_history', { lead })
      let d = null
      const jobs = new Set()
      if (h?.chat) {
        chatId = h.chat
        messages.value = h.messages.flatMap((m) => {
          const r = m.decide || m.steps_run || m.result
          if (m.role === 'ai' && !m.error && r?.arv) {
            if (m.job) jobs.add(m.job)
            const c = calcMessage(r)
            return c ? [c] : []
          }
          return m.decide ? [] : [{ role: m.role, kind: m.kind, text: m.text, error: m.error }]
        })
        // The last 13-step run: a "Run the 13 steps" here, or an Ask AI price card (also the 13 steps).
        const last = [...h.messages].reverse().find((m) => m.decide || m.steps_run || (m.kind === 'price' && m.result))
        d = last?.decide || last?.steps_run || (await jobSteps(last?.job))
      }
      // Opened from an Ask AI card ("Finish on comps page", ?ai=<job>): show that run's steps,
      // and its numbers in the chat when the run was not this rep's chat.
      const ai = route.query.ai
      if (ai && typeof ai === 'string') {
        const j = await jobSteps(ai)
        if (j) {
          d = j
          const c = !jobs.has(ai) && calcMessage(j)
          if (c) messages.value.push(c)
        }
      }
      if (d?.steps) {
        run.value = d
        steps.value = d.steps
      }
    } catch {
      /* Ask AI off or offline: the rail stays empty */
    }
  },
  { immediate: true },
)
</script>
