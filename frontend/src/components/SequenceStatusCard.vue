<template>
  <!-- Which sequences this lead is in, with Resume / Move on / Stop right here.
       The only other place is the sequence's Enrollments table, which a rep
       can't find one lead in (Manuel Lago, 2026-11-03). Hidden when the lead
       was never enrolled. -->
  <div v-if="rows.length" class="border-t px-5 py-4">
    <div class="flex items-center gap-2 text-base font-medium text-ink-gray-8">
      <FeatherIcon name="repeat" class="size-4 text-ink-gray-7" />
      {{ __('Sequences') }}
    </div>

    <div v-for="r in live" :key="r.name" class="mt-3">
      <div class="flex items-center justify-between gap-2">
        <router-link
          :to="{ name: 'Sequence', params: { sequenceId: r.sequence } }"
          class="truncate text-sm font-medium text-ink-gray-8 hover:underline"
        >
          {{ r.sequence }}
        </router-link>
        <Badge variant="subtle" :theme="theme(r.status)" :label="__(r.status)" />
      </div>
      <div class="mt-0.5 text-xs leading-relaxed text-ink-gray-5">
        {{ progressLabel(r) }}
        <template v-if="r.status === 'Active' && r.next_run"> · {{ __('next') }} {{ when(r.next_run) }}</template>
      </div>
      <p
        v-if="r.status === 'Paused'"
        class="mt-1.5 rounded-md bg-surface-amber-1 px-2.5 py-1.5 text-xs leading-relaxed text-ink-amber-3"
        :title="r.last_log"
      >
        {{ __('Paused') }}{{ r.reason ? ` — ${r.reason}` : '' }}.
        {{ __('It stays paused until someone resumes or moves it.') }}
      </p>
      <div v-if="r.actions.length" class="mt-2 flex flex-wrap gap-1.5">
        <Button
          v-for="a in r.actions"
          :key="a"
          size="sm"
          :variant="a === r.actions[0] ? 'solid' : 'subtle'"
          :label="actionLabel(a, r)"
          :loading="busy === r.name + a"
          :disabled="!!busy"
          @click="act(r, a)"
        />
      </div>
    </div>

    <div v-if="past.length" class="mt-3 flex flex-col gap-1">
      <div
        v-for="r in past"
        :key="r.name"
        class="flex items-center justify-between gap-2 text-xs text-ink-gray-5"
        :title="r.last_log"
      >
        <span class="truncate">{{ r.sequence }} · {{ progressLabel(r) }}</span>
        <span class="shrink-0">{{ __(r.status) }}</span>
      </div>
    </div>
  </div>
</template>

<script setup>
import { Badge, Button, FeatherIcon, call, toast } from 'frappe-ui'
import { computed, ref, watch } from 'vue'
import { formatDate } from '@/utils'

const props = defineProps({
  lead: { type: String, required: true },
})

const rows = ref([])
const busy = ref('')

const live = computed(() => rows.value.filter((r) => ['Active', 'Paused'].includes(r.status)))
const past = computed(() => rows.value.filter((r) => !['Active', 'Paused'].includes(r.status)))

async function load() {
  try {
    rows.value = (await call('crm.api.sequence_lead.for_lead', { lead: props.lead })) || []
  } catch (e) {
    rows.value = []
  }
}
watch(() => props.lead, load, { immediate: true })

function theme(status) {
  return { Active: 'green', Paused: 'orange', Completed: 'blue', Stopped: 'gray' }[status] || 'gray'
}

function progressLabel(r) {
  if (!r.total) return ''
  return __('Step {0} of {1} done', [r.done, r.total])
}

function when(d) {
  return formatDate(d, 'MMM D, h:mm a')
}

function actionLabel(a, r) {
  return {
    move_next: __('Move to {0}', [r.then_enroll]),
    resume: __('Resume'),
    pause: __('Pause'),
    stop: __('Stop'),
  }[a]
}

async function act(r, a) {
  if (a === 'stop' && !window.confirm(__('Stop {0} for this lead? It can\'t be restarted.', [r.sequence]))) return
  busy.value = r.name + a
  try {
    rows.value = await call('crm.api.sequence_lead.set_state', { enrollment: r.name, action: a })
    toast.success(a === 'move_next' ? __('Moved to {0}', [r.then_enroll]) : __('Sequence updated'))
  } catch (e) {
    toast.error(e.messages?.[0] || __('Could not update the sequence'))
  } finally {
    busy.value = ''
  }
}
</script>
