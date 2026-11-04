<template>
  <LayoutHeader>
    <template #left-header>
      <Breadcrumbs :items="crumbs" />
    </template>
    <template #right-header>
      <Button v-if="!runId" variant="solid" iconLeft="plus" :label="creating ? __('Close') : __('New run')" @click="toggleNew" />
    </template>
  </LayoutHeader>

  <div class="flex-1 overflow-y-auto px-3 pb-6 sm:px-5">
    <!-- Run list and the new-run form -->
    <template v-if="!runId">
      <p class="mt-3 max-w-3xl text-sm text-ink-gray-5">
        {{ __('Rerun the 13 steps on many leads with a trial setup, then mark the steps you are testing right or wrong. The team setup does not change until you apply a run.') }}
      </p>

      <div v-if="creating" class="mt-4 max-w-5xl space-y-5 rounded-lg border border-outline-gray-2 p-4">
        <FormControl v-model="draft.title" :label="__('Run name')" :placeholder="__('Step 4 on Jev, east side')" />

        <section>
          <div class="mb-1 flex items-center gap-3 text-sm font-medium text-ink-gray-8">
            {{ __('Leads') }}
            <span class="text-xs font-normal text-ink-gray-5">{{ __('{0} of {1} picked', [draft.leads.length, opts.max_leads || 25]) }}</span>
            <select v-model="draft.set" class="ml-auto rounded border-outline-gray-2 py-1 text-xs" @change="pickSet">
              <option value="">{{ __('Pick from a Practice set') }}</option>
              <option v-for="s in opts.sets || []" :key="s.name" :value="s.name">{{ s.title }} ({{ s.leads.length }})</option>
            </select>
          </div>
          <p class="mb-2 text-xs text-ink-gray-5">{{ __('Leads with a saved cash offer. The offer ARV is the answer each run is compared to.') }}</p>
          <div class="max-h-64 overflow-y-auto rounded border border-outline-gray-1">
            <label v-for="l in opts.leads || []" :key="l.lead" class="flex cursor-pointer items-center gap-2 border-b border-outline-gray-1 px-2 py-1.5 text-sm last:border-0 hover:bg-surface-gray-1">
              <input v-model="draft.leads" type="checkbox" :value="l.lead" :disabled="full && !draft.leads.includes(l.lead)" class="rounded" />
              <span class="min-w-0 flex-1 truncate text-ink-gray-8">{{ l.address }}</span>
              <span class="w-24 shrink-0 text-right tabular-nums text-ink-gray-7">{{ money(l.team_arv) }}</span>
              <span class="w-24 shrink-0 text-right text-xs text-ink-gray-5">{{ l.at }}</span>
            </label>
            <div v-if="optsRes.loading" class="p-3 text-sm text-ink-gray-5">{{ __('Loading leads…') }}</div>
          </div>
        </section>

        <section>
          <div class="mb-1 text-sm font-medium text-ink-gray-8">{{ __('Trial setup') }}</div>
          <p class="mb-2 text-xs text-ink-gray-5">{{ __('Starts as the team setup. Change any step to code, Jev or a model; changed steps are checked below.') }}</p>
          <div class="divide-y divide-outline-gray-1 rounded border border-outline-gray-1">
            <div v-for="(t, i) in STEP_TITLES" :key="i" class="flex flex-wrap items-center gap-2 px-2 py-1.5 text-sm" :class="differs(i + 1) ? 'bg-surface-amber-1' : ''">
              <span class="w-5 text-ink-gray-5">{{ i + 1 }}</span>
              <span class="w-56 shrink-0 text-ink-gray-8">{{ t }}</span>
              <select :value="pickValue(i + 1)" class="w-56 rounded border-outline-gray-2 py-1 text-xs" @change="setTool(i + 1, $event.target.value)">
                <optgroup :label="__('Code')"><option value="code">{{ __('Rule (code, no AI)') }}</option></optgroup>
                <optgroup :label="__('Decision model')"><option value="jev">Jev</option></optgroup>
                <optgroup :label="__('Language model')">
                  <option v-for="m in MODELS" :key="m" :value="'model:' + m">{{ toolLabel('model:' + m) }}</option>
                  <option value="other">{{ __('Other model…') }}</option>
                </optgroup>
              </select>
              <input v-if="other[i + 1]" v-model="trial[i + 1].tool" class="w-56 rounded border-outline-gray-2 py-1 text-xs" :placeholder="'model:provider/name'" />
              <input v-if="i + 1 === 5" v-model.number="trial[5].size_band" type="number" step="0.05" min="0.05" max="1" class="w-20 rounded border-outline-gray-2 py-1 text-xs" :title="__('Size band, 0.3 = 30%')" />
              <input v-if="i + 1 === 7" v-model.number="trial[7].max_mi" type="number" step="0.25" min="0.25" max="5" class="w-20 rounded border-outline-gray-2 py-1 text-xs" :title="__('Radius, miles')" />
              <input v-model="trial[i + 1].note" class="min-w-40 flex-1 rounded border-outline-gray-2 py-1 text-xs" :placeholder="__('Team rule for this step (optional)')" />
            </div>
          </div>
        </section>

        <section>
          <div class="mb-1 text-sm font-medium text-ink-gray-8">{{ __('Steps to check') }}</div>
          <div class="flex flex-wrap gap-1.5">
            <button v-for="n in 13" :key="n" type="button" class="whitespace-nowrap rounded border focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-outline-gray-4 active:opacity-80 px-2 py-1 text-xs"
              :class="draft.steps.includes(n) ? 'border-outline-gray-7 bg-surface-gray-7 text-ink-white' : 'border-outline-gray-2 text-ink-gray-7 hover:bg-surface-gray-1'"
              @click="toggle(draft.steps, n)">{{ n }} {{ STEP_TITLES[n - 1] }}</button>
          </div>
        </section>

        <div class="flex items-center gap-3">
          <Button variant="solid" :label="__('Run {0} leads', [draft.leads.length])" :disabled="!draft.leads.length" :loading="starting" @click="start" />
          <span class="text-xs text-ink-gray-5">{{ __('About {0} min. Reps on the comps page always go first.', [Math.max(1, Math.round(draft.leads.length * 0.7))]) }}</span>
        </div>
      </div>

      <div v-if="runsRes.data?.length" class="mt-5">
        <div class="flex border-b border-outline-gray-1 py-1.5 text-xs font-medium text-ink-gray-5">
          <span class="min-w-0 flex-1">{{ __('Run') }}</span>
          <span class="w-24 shrink-0">{{ __('Leads') }}</span>
          <span class="w-32 shrink-0">{{ __('Median gap') }}</span>
          <span class="w-28 shrink-0">{{ __('Within 10%') }}</span>
          <span class="w-40 shrink-0">{{ __('Step marks') }}</span>
          <span class="w-40 shrink-0">{{ __('Started') }}</span>
        </div>
        <button v-for="r in runsRes.data" :key="r.name" class="flex w-full items-center whitespace-nowrap border-b focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-outline-gray-4 active:opacity-80 border-outline-gray-1 py-2.5 text-left text-sm hover:bg-surface-gray-1"
          @click="$router.push({ name: 'AvmTrainingRun', params: { runId: r.name } })">
          <span class="min-w-0 flex-1 truncate font-medium text-ink-gray-9">{{ r.title }}</span>
          <span class="w-24 shrink-0 text-ink-gray-6">{{ r.summary.done }} / {{ r.summary.leads }}</span>
          <span class="w-32 shrink-0 tabular-nums text-ink-gray-6">{{ pct(r.summary.median_gap) }}</span>
          <span class="w-28 shrink-0 text-ink-gray-6">{{ r.summary.compared ? r.summary.within_10 + ' / ' + r.summary.compared : '—' }}</span>
          <span class="w-40 shrink-0 text-ink-gray-6">{{ markText(r.summary.marks) }}</span>
          <span class="w-40 shrink-0 text-ink-gray-5">{{ r.at }}, {{ r.user.split('@')[0] }}</span>
        </button>
      </div>
      <div v-else-if="!runsRes.loading && !creating" class="mt-12 text-center text-sm text-ink-gray-5">{{ __('No training runs yet.') }}</div>
    </template>

    <!-- One run -->
    <template v-else-if="detail">
      <div class="mt-3 flex flex-wrap items-center gap-x-6 gap-y-1 text-sm text-ink-gray-7">
        <span>{{ __('{0} of {1} done', [detail.summary.done, detail.summary.leads]) }}</span>
        <span v-if="detail.summary.failed" class="text-ink-red-4">{{ __('{0} failed', [detail.summary.failed]) }}</span>
        <span>{{ __('Median gap to the team ARV: {0}', [pct(detail.summary.median_gap)]) }}</span>
        <span>{{ __('Within 10%: {0} of {1}', [detail.summary.within_10, detail.summary.compared]) }}</span>
        <span class="text-ink-gray-5">{{ __('Started {0} by {1}', [detail.at, detail.user]) }}</span>
        <LoadingIndicator v-if="pending" class="h-4 w-4 text-ink-gray-5" />
      </div>

      <div class="mt-3 rounded-lg border border-outline-gray-2 p-3 text-sm">
        <div class="font-medium text-ink-gray-8">{{ __('Trial setup') }}</div>
        <p v-if="!detail.differs.length" class="mt-1 text-ink-gray-6">{{ __('Same as the team setup.') }}</p>
        <ul v-else class="mt-1 space-y-0.5 text-ink-gray-7">
          <li v-for="n in detail.differs" :key="n">
            <span class="text-ink-gray-5">{{ n }}</span> {{ STEP_TITLES[n - 1] }}: {{ setupText(detail.team_setup, n) }} → <b>{{ setupText(detail.setup, n) }}</b>
          </li>
        </ul>
        <p v-if="detail.differs.length && !detail.team_wide" class="mt-2 text-xs text-ink-gray-6">
          {{ __("Only the pricer's owner changes the team setup. Your marks go to them as suggestions.") }}
        </p>
        <div v-if="detail.differs.length && detail.team_wide" class="mt-2 flex flex-wrap items-center gap-2">
          <input v-model="applyWhy" class="min-w-64 flex-1 rounded border-outline-gray-2 py-1 text-sm" :placeholder="__('Why make this the team setup? The team sees this.')" />
          <Button :label="__('Make this the team setup')" :disabled="!applyWhy.trim()" :loading="applying" @click="apply" />
        </div>
      </div>

      <div class="mt-4 flex flex-wrap items-center gap-1.5">
        <span class="mr-1 text-xs text-ink-gray-5">{{ __('Checking') }}</span>
        <button v-for="n in 13" :key="n" type="button" class="whitespace-nowrap rounded border focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-outline-gray-4 active:opacity-80 px-2 py-0.5 text-xs"
          :class="shown.includes(n) ? 'border-outline-gray-7 bg-surface-gray-7 text-ink-white' : 'border-outline-gray-2 text-ink-gray-6 hover:bg-surface-gray-1'"
          :title="STEP_TITLES[n - 1]" @click="toggle(shown, n)">{{ n }}<span v-if="tally(n)" class="ml-1 opacity-75">{{ tally(n) }}</span></button>
      </div>

      <div class="mt-3 space-y-3">
        <div v-for="r in detail.rows" :key="r.lead" class="rounded-lg border border-outline-gray-2">
          <div class="flex flex-wrap items-center gap-x-4 gap-y-1 border-b border-outline-gray-1 px-3 py-2 text-sm">
            <router-link :to="{ name: 'Lead', params: { leadId: r.lead }, hash: '#comps' }" class="min-w-0 flex-1 truncate font-medium text-ink-gray-9 hover:underline">{{ r.address }}</router-link>
            <template v-if="r.status === 'done'">
              <span class="tabular-nums text-ink-gray-8">{{ __('AI {0}', [money(r.arv)]) }}</span>
              <span class="tabular-nums text-ink-gray-6">{{ __('Team {0}', [money(r.team_arv)]) }}</span>
              <span class="w-16 text-right tabular-nums" :class="Math.abs(r.gap) > 0.1 ? 'text-ink-red-4' : 'text-ink-green-3'">{{ signed(r.gap) }}</span>
            </template>
            <span v-else-if="r.status === 'error'" class="text-ink-red-4">{{ r.error || __('Failed') }}</span>
            <span v-else class="text-ink-gray-5">{{ r.status === 'running' ? __('Running…') : r.status === 'expired' ? __('Expired') : __('Queued') }}</span>
          </div>
          <div v-if="r.status === 'done'" class="divide-y divide-outline-gray-1">
            <div v-for="s in r.steps.filter((x) => shown.includes(x.n))" :key="s.n" class="flex gap-3 px-3 py-2 text-sm">
              <span class="w-5 shrink-0 text-ink-gray-5">{{ s.n }}</span>
              <div class="min-w-0 flex-1">
                <div class="flex items-center gap-2">
                  <span class="font-medium text-ink-gray-8">{{ s.title }}</span>
                  <span class="rounded px-1.5 text-xs" :class="s.tool === 'code' ? 'bg-surface-gray-2 text-ink-gray-7' : 'bg-surface-gray-7 text-ink-white'">{{ toolLabel(s.tool) }}</span>
                </div>
                <p class="mt-0.5 whitespace-pre-line text-ink-gray-7">{{ s.text }}</p>
                <p v-if="markOf(r, s)?.reason" class="mt-1 text-xs text-ink-red-4">{{ __('Wrong: {0}', [markOf(r, s).reason]) }}</p>
                <div v-if="wrongAt === key(r, s)" class="mt-2 flex gap-2">
                  <input v-model="wrongWhy" class="flex-1 rounded border-outline-gray-2 py-1 text-sm" :placeholder="__('What should it have chosen, and why?')" @keydown.enter="sendMark(r, s, false)" />
                  <Button size="sm" variant="solid" :label="__('Save')" :disabled="!wrongWhy.trim()" @click="sendMark(r, s, false)" />
                </div>
              </div>
              <div class="flex shrink-0 items-start gap-1">
                <button type="button" class="whitespace-nowrap rounded border focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-outline-gray-4 active:opacity-80 px-2 py-0.5 text-xs"
                  :class="markOf(r, s)?.right === true ? 'border-outline-green-2 bg-surface-green-2 text-ink-green-3' : 'border-outline-gray-2 text-ink-gray-6 hover:bg-surface-gray-1'"
                  @click="sendMark(r, s, true)">{{ __('Right') }}</button>
                <button type="button" class="whitespace-nowrap rounded border focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-outline-gray-4 active:opacity-80 px-2 py-0.5 text-xs"
                  :class="markOf(r, s)?.right === false ? 'border-outline-red-2 bg-surface-red-2 text-ink-red-4' : 'border-outline-gray-2 text-ink-gray-6 hover:bg-surface-gray-1'"
                  @click="openWrong(r, s)">{{ __('Wrong') }}</button>
              </div>
            </div>
            <p v-if="!shown.length" class="px-3 py-2 text-xs text-ink-gray-5">{{ __('Pick steps above to check them.') }}</p>
          </div>
        </div>
      </div>
    </template>
    <div v-else-if="runRes.error" class="mt-8 text-sm text-ink-red-4">{{ runRes.error.messages?.[0] || __('Could not load this run.') }}</div>
  </div>
</template>

<script setup>
// AVM training (Exe, 2026-11-02): bulk rerun the 13 steps with a trial setup and mark steps right or wrong.
// Backend: crm/api/avm_training.py. Runs go on the worker's bulk queue, behind any rep's job.
import LayoutHeader from '@/components/LayoutHeader.vue'
import { Breadcrumbs, Button, FormControl, LoadingIndicator, call, createResource, toast } from 'frappe-ui'
import { computed, onBeforeUnmount, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { MODELS, STEP_TITLES, defaultTool, toolLabel } from '@/utils/compSteps'

const props = defineProps({ runId: { type: String, default: '' } })
const router = useRouter()

const money = (n) => (Number(n) ? '$' + Math.round(Number(n)).toLocaleString() : '—')
const pct = (g) => (g == null ? '—' : (g * 100).toFixed(1) + '%')
const signed = (g) => (g == null ? '—' : (g > 0 ? '+' : '') + (g * 100).toFixed(1) + '%')
const toggle = (arr, n) => (arr.includes(n) ? arr.splice(arr.indexOf(n), 1) : arr.push(n))
const markText = (m) => Object.entries(m || {}).map(([n, v]) => `${n}: ${v.right}✓ ${v.wrong}✗`).join('  ') || '—'

// ---- list + new run
const creating = ref(false)
const starting = ref(false)
const runsRes = createResource({ url: 'crm.api.avm_training.runs', auto: !props.runId })
const optsRes = createResource({ url: 'crm.api.avm_training.options' })
const opts = computed(() => optsRes.data || {})
const draft = reactive({ title: '', leads: [], steps: [], set: '' })
const trial = reactive({})
const other = reactive({})
const full = computed(() => draft.leads.length >= (opts.value.max_leads || 25))

function resetTrial(team) {
  for (let n = 1; n <= 13; n++) {
    const c = team?.[String(n)] || {}
    trial[n] = { tool: c.tool || defaultTool(n), note: c.note || '', ...(n === 5 ? { size_band: c.size_band || 0.3 } : {}), ...(n === 7 ? { max_mi: c.max_mi || 2 } : {}) }
    other[n] = !['code', 'jev', ...MODELS.map((m) => 'model:' + m)].includes(trial[n].tool)
  }
}
resetTrial({})
watch(() => optsRes.data, (d) => d && resetTrial(d.setup))

const pickValue = (n) => (other[n] ? 'other' : trial[n].tool)
function setTool(n, v) {
  other[n] = v === 'other'
  trial[n].tool = v === 'other' ? 'model:' : v
}
function norm(c, n) {
  return { tool: c?.tool || defaultTool(n), note: (c?.note || '').trim(), size_band: n === 5 ? Number(c?.size_band || 0.3) : 0, max_mi: n === 7 ? Number(c?.max_mi || 2) : 0 }
}
const differs = (n) => JSON.stringify(norm(trial[n], n)) !== JSON.stringify(norm(opts.value.setup?.[String(n)], n))
watch(() => [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13].filter(differs), (ch, old) => {
  for (const n of ch) if (!(old || []).includes(n) && !draft.steps.includes(n)) draft.steps.push(n)
}, { deep: true })

function toggleNew() {
  creating.value = !creating.value
  if (creating.value && !optsRes.data) optsRes.fetch()
}
function pickSet() {
  const s = (opts.value.sets || []).find((x) => x.name === draft.set)
  if (!s) return
  const known = new Set((opts.value.leads || []).map((l) => l.lead))
  draft.leads = s.leads.filter((l) => known.has(l)).slice(0, opts.value.max_leads || 25)
  if (!draft.title) draft.title = s.title
  if (draft.leads.length < s.leads.length) toast.info(__('{0} leads in that set have no saved cash offer to compare to.', [s.leads.length - draft.leads.length]))
}
async function start() {
  starting.value = true
  try {
    const setup = {}
    for (let n = 1; n <= 13; n++) {
      const t = trial[n]
      const row = { tool: t.tool, note: t.note.trim() }
      if (n === 5) row.size_band = t.size_band
      if (n === 7) row.max_mi = t.max_mi
      setup[n] = row
    }
    const res = await call('crm.api.avm_training.start', { title: draft.title, leads: draft.leads, setup, steps: draft.steps })
    router.push({ name: 'AvmTrainingRun', params: { runId: res.name } })
  } catch (e) {
    toast.error(e.messages?.[0] || __('Could not start the run.'))
  } finally {
    starting.value = false
  }
}

// ---- one run
const runRes = createResource({ url: 'crm.api.avm_training.run', makeParams: () => ({ name: props.runId }) })
const detail = computed(() => runRes.data)
const shown = ref([])
const pending = computed(() => (detail.value?.rows || []).some((r) => r.status === 'queued' || r.status === 'running'))
const crumbs = computed(() => [{ label: __('AVM training'), route: { name: 'AvmTraining' } }, ...(props.runId ? [{ label: detail.value?.title || '…' }] : [])])
let timer = null

watch(() => props.runId, (id) => {
  clearTimeout(timer)
  if (id) {
    runRes.data = null
    runRes.fetch().then((d) => { shown.value = [...(d?.steps?.length ? d.steps : d?.differs || [])] })
  } else runsRes.fetch()
}, { immediate: true })
watch(pending, (p) => { clearTimeout(timer); if (p) poll() })
function poll() {
  timer = setTimeout(async () => { await runRes.reload(); if (pending.value) poll() }, 6000)
}
onBeforeUnmount(() => clearTimeout(timer))

const key = (r, s) => `${r.lead}|${s.n}`
const markOf = (r, s) => detail.value?.marks?.[key(r, s)]
const tally = (n) => { const m = detail.value?.summary?.marks?.[n]; return m ? `${m.right}✓ ${m.wrong}✗` : '' }
const wrongAt = ref('')
const wrongWhy = ref('')
function openWrong(r, s) {
  wrongAt.value = wrongAt.value === key(r, s) ? '' : key(r, s)
  wrongWhy.value = markOf(r, s)?.reason || ''
}
async function sendMark(r, s, right) {
  try {
    const marks = await call('crm.api.avm_training.mark', { name: props.runId, lead: r.lead, step: s.n, right: right ? 1 : 0, reason: right ? '' : wrongWhy.value })
    runRes.data = { ...detail.value, marks }
    wrongAt.value = ''
    runRes.reload()
  } catch (e) {
    toast.error(e.messages?.[0] || __('Could not save the mark.'))
  }
}

function setupText(setup, n) {
  const c = setup?.[String(n)] || {}
  const out = [toolLabel(c.tool || defaultTool(n))]
  if (n === 5 && c.size_band) out.push(__('within {0}%', [Math.round(c.size_band * 100)]))
  if (n === 7 && c.max_mi) out.push(__('up to {0} mi', [c.max_mi]))
  if (c.note) out.push(__('rule: {0}', [c.note]))
  return out.join(', ')
}
const applyWhy = ref('')
const applying = ref(false)
async function apply() {
  applying.value = true
  try {
    const res = await call('crm.api.avm_training.apply_setup', { name: props.runId, reason: applyWhy.value })
    toast.success(__('Team setup updated: {0} changes logged.', [res.lines.length]))
    applyWhy.value = ''
    runRes.reload()
  } catch (e) {
    toast.error(e.messages?.[0] || __('Could not apply the setup.'))
  } finally {
    applying.value = false
  }
}
</script>
