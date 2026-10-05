<template>
  <div ref="rootEl">
    <div class="mb-1 flex items-center justify-stretch gap-2 py-1 text-xs">
      <div class="inline-flex items-center flex-wrap gap-1 text-ink-gray-5">
        <span class="font-medium text-ink-gray-8">
          {{ formatPhone(call._caller.label) }}
        </span>
        <span>{{
          call.type == 'Incoming' ? __('called in') : __('made a call')
        }}</span>
      </div>
      <div class="ml-auto whitespace-nowrap">
        <Tooltip :text="formatDate(call.creation)">
          <div class="text-xs text-ink-gray-5">
            {{ formatDate(call.creation, 'h:mm a') }}
          </div>
        </Tooltip>
      </div>
    </div>
    <div
      class="flex flex-col gap-2 border cursor-pointer border-outline-gray-modals rounded-md bg-surface-cards px-3 py-2.5 text-ink-gray-9"
      @click="showCallLogDetailModal = true"
    >
      <div class="flex items-center justify-between">
        <div class="inline-flex gap-2 items-center text-sm font-medium">
          <div>
            {{
              call.type == 'Incoming' ? __('Inbound Call') : __('Outbound Call')
            }}
          </div>
        </div>
        <div>
          <MultipleAvatar
            :avatars="[
              {
                image: call._caller.image,
                label: formatPhone(call._caller.label),
                name: formatPhone(call._caller.label),
              },
              {
                image: call._receiver.image,
                label: formatPhone(call._receiver.label),
                name: formatPhone(call._receiver.label),
              },
            ]"
            size="sm"
          />
        </div>
      </div>
      <div class="flex flex-wrap items-center gap-x-2 gap-y-1.5 sm:gap-y-2">
        <Badge :label="formatDate(call.creation, 'MMM D, dddd')">
          <template #prefix>
            <CalendarIcon class="size-3" />
          </template>
        </Badge>
        <Badge v-if="call.status == 'Completed'" :label="call._duration">
          <template #prefix>
            <DurationIcon class="size-3" />
          </template>
        </Badge>
        <Badge
          v-if="call.recording_url"
          :label="call.show_transcript ? __('Hide Playback') : __('Playback')"
          class="cursor-pointer"
          @click.stop="call.show_transcript = !call.show_transcript"
        >
          <template #prefix>
            <PlayIcon class="size-3" />
          </template>
        </Badge>
        <a
          v-if="call.recording_url"
          :href="call.recording_url"
          target="_blank"
          rel="noopener"
          @click.stop
        >
          <Badge :label="__('Recording')" class="cursor-pointer">
            <template #prefix>
              <ExternalLinkIcon class="size-3" />
            </template>
          </Badge>
        </a>
        <Badge
          :label="statusLabelMap[call.status]"
          :theme="statusColorMap[call.status]"
        />
        <!-- click.stop wrapper: keeps the card's open-detail-modal click away.
             The slot root must be a plain element — frappe-ui Dropdown uses
             reka-ui's DropdownMenuTrigger as-child, which binds its open/close
             handlers onto the slot's root; a Tooltip root swallows them. -->
        <div @click.stop>
          <Dropdown v-if="callLog.data" :options="classOptions">
            <button
              type="button"
              class="flex items-center leading-none"
              :title="classTooltip"
            >
              <Badge
                :label="callClass || __('Classify')"
                :theme="callClassTheme"
                class="cursor-pointer"
              >
                <template #suffix>
                  <FeatherIcon name="chevron-down" class="size-3" />
                </template>
              </Badge>
            </button>
          </Dropdown>
        </div>
      </div>
      <div
        v-if="call.show_transcript && call.recording_url"
        class="border-t border-outline-gray-modals pt-2"
        @click.stop
      >
        <CallTranscript :call-log-name="call.name" :seek-to="seekTo" />
      </div>
      <div
        v-if="callLog?.data?.custom_ai_summary"
        class="flex flex-col gap-1 border-t border-outline-gray-modals pt-2"
        @click.stop
      >
        <div class="flex items-center gap-1 text-xs font-medium text-ink-gray-5">
          <SparkleIcon class="size-3.5" />
          {{ __('AI Summary') }}
        </div>
        <FadedScrollableDiv class="max-h-40 overflow-y-auto">
          <div class="whitespace-pre-line text-p-sm text-ink-gray-7">
            {{ callLog.data.custom_ai_summary }}
          </div>
        </FadedScrollableDiv>
      </div>
    </div>
    <CallLogDetailModal
      v-model="showCallLogDetailModal"
      v-model:callLogModal="showCallLogModal"
      v-model:callLog="callLog"
    />
    <CallLogModal
      v-if="showCallLogModal"
      v-model="showCallLogModal"
      :data="callLog.data"
    />
  </div>
</template>
<script setup>
import PlayIcon from '@/components/Icons/PlayIcon.vue'
import CalendarIcon from '@/components/Icons/CalendarIcon.vue'
import DurationIcon from '@/components/Icons/DurationIcon.vue'
import ExternalLinkIcon from '@/components/Icons/ExternalLinkIcon.vue'
import SparkleIcon from '@/components/Icons/SparkleIcon.vue'
import MultipleAvatar from '@/components/MultipleAvatar.vue'
import CallTranscript from '@/components/Activities/CallTranscript.vue'
import FadedScrollableDiv from '@/components/FadedScrollableDiv.vue'
import CallLogDetailModal from '@/components/Modals/CallLogDetailModal.vue'
import CallLogModal from '@/components/Modals/CallLogModal.vue'
import { statusLabelMap, statusColorMap } from '@/utils/callLog.js'
import { formatDate } from '@/utils'
import { formatPhone } from '@/utils/phoneFormat'
import {
  Badge,
  Tooltip,
  Dropdown,
  FeatherIcon,
  createResource,
  call as apiCall,
} from 'frappe-ui' // Tooltip still used by the timestamp header
import { reactive, ref, computed, watch, onMounted, nextTick } from 'vue'
import { useRoute } from 'vue-router'

const props = defineProps({
  activity: { type: Object, default: () => ({}) },
})

const call = reactive(props.activity)

// deep link (?call=<id>&t=<sec>): if this is the targeted call, auto-open its
// transcript, hand the timestamp to <CallTranscript>, and scroll it into view
const route = useRoute()
const rootEl = ref(null)
const isTarget = computed(
  () => route.query.call && String(route.query.call) === String(call.name),
)
const seekTo = computed(() =>
  isTarget.value && route.query.t != null ? Number(route.query.t) : null,
)
function revealIfTarget() {
  if (!isTarget.value) return
  call.show_transcript = true
  // the activity feed keeps loading/re-laying-out after this card mounts (and may
  // scroll itself back to top), so a single scrollIntoView gets lost. Re-scroll on
  // an interval until the card top is actually parked near the top of the viewport.
  let tries = 0
  const tick = () => {
    const el = rootEl.value
    if (!el) return
    el.scrollIntoView({ block: 'start' })
    const top = el.getBoundingClientRect().top
    const parked = top >= 0 && top < window.innerHeight * 0.4
    if (!parked && ++tries < 14) setTimeout(tick, 300)
  }
  nextTick(tick)
}
onMounted(revealIfTarget)
watch(isTarget, revealIfTarget)

const callLog = createResource({
  url: 'crm.fcrm.doctype.crm_call_log.crm_call_log.get_call_log',
  params: { name: call.name },
  cache: ['call_log', call.name],
  auto: true,
})
const showCallLogDetailModal = ref(false)
const showCallLogModal = ref(false)

// call classification badge (fields written by the classify-crm-calls
// workflow; picking a value here stamps source=human so re-runs never
// overwrite the correction)
const CALL_CLASSES = [
  'Connected',
  'Voicemail Left',
  'Greeting Hangup',
  'Screener - No Contact',
  'IVR / Robot',
  'No Answer',
  'Phantom',
  'No Transcript',
]
const classThemes = {
  Connected: 'green',
  'Voicemail Left': 'blue',
  'Greeting Hangup': 'gray',
  'Screener - No Contact': 'orange',
  'IVR / Robot': 'orange',
  'No Answer': 'gray',
  Phantom: 'red',
  'No Transcript': 'gray',
}
const callClass = computed(() => callLog.data?.custom_call_class || '')
const callClassTheme = computed(
  () => classThemes[callClass.value] || 'gray',
)
const classTooltip = computed(() => {
  if (!callClass.value) return __('Set call classification')
  const src = callLog.data?.custom_call_class_source
  const note = callLog.data?.custom_call_note
  let t = __('Classified by {0}', [src || 'unknown'])
  if (note) t += ` — ${note}`
  return t
})
const classOptions = computed(() =>
  CALL_CLASSES.map((c) => ({
    label: c,
    onClick: () => setCallClass(c),
  })),
)
async function setCallClass(c) {
  await apiCall('crm.api.call_class.set_call_class', {
    call: call.name,
    call_class: c,
  })
  callLog.data.custom_call_class = c
  callLog.data.custom_call_class_source = 'human'
}
</script>
