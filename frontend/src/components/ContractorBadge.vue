<template>
  <!--
    Photos & Lockbox cards only: the server sends `_contractors` for no other
    column, so every other card renders nothing here.

    Green = someone on file covers this metro (click to see them and call).
    Amber = the property is in a metro nobody on file covers yet — the card
    that needs a contractor found. Gray = the county is outside every metro.
  -->
  <Popover v-if="data" placement="bottom-start">
    <template #target="{ togglePopover }">
      <button
        type="button"
        class="inline-flex max-w-full items-center gap-1 rounded-full px-1.5 py-0.5 text-[11px] font-semibold leading-none"
        :class="chipClass"
        :title="title"
        @click.stop.prevent="togglePopover()"
      >
        <HardHatIcon class="size-3 shrink-0" />
        <span class="truncate">{{ label }}</span>
      </button>
    </template>
    <template #body="{ close }">
      <div
        class="w-72 rounded-lg border border-outline-gray-1 bg-surface-white p-2 text-base shadow-xl"
        @click.stop.prevent
      >
        <div class="mb-1.5 px-1 text-xs text-ink-gray-5">
          {{ data.metro || __('Not in a metro area') }}
        </div>
        <div
          v-for="c in data.contractors"
          :key="c.name"
          class="flex items-center justify-between gap-2 rounded px-1 py-1.5 hover:bg-surface-gray-1"
        >
          <div class="min-w-0">
            <div class="truncate text-sm font-medium text-ink-gray-8">
              {{ c.contractor_name }}
              <span v-if="c.company" class="font-normal text-ink-gray-5">· {{ c.company }}</span>
            </div>
            <div class="truncate text-xs text-ink-gray-5">
              {{ (c.services || []).join(' + ') || __('No services set') }}
            </div>
          </div>
          <button
            v-if="c.phone"
            type="button"
            class="shrink-0 rounded px-1.5 py-1 text-xs text-ink-gray-7 hover:bg-surface-gray-2 hover:text-ink-blue-link"
            :title="__('Call')"
            @click.stop.prevent="call(c.phone, close)"
          >
            {{ formatPhone(c.phone) }}
          </button>
        </div>
        <div
          v-if="!data.contractors.length"
          class="px-1 py-1.5 text-sm text-ink-gray-6"
        >
          {{
            data.metro
              ? __('Nobody on file covers this metro yet.')
              : __("This county isn't in a metro area, so no contractor can match it.")
          }}
        </div>
        <button
          type="button"
          class="mt-1 w-full rounded px-1 py-1.5 text-left text-xs text-ink-gray-6 hover:bg-surface-gray-1 hover:text-ink-gray-8"
          @click.stop.prevent="openDirectory(close)"
        >
          {{ __('Open contractors') }} →
        </button>
      </div>
    </template>
  </Popover>
</template>

<script setup>
import HardHatIcon from '~icons/lucide/hard-hat'
import { Popover } from 'frappe-ui'
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import { formatPhone } from '@/utils/phoneFormat'
import { clickToCall } from '@/composables/clickToCall'

const props = defineProps({
  // Server `_contractors`: {metro, contractors: [...]}, or null off-column.
  value: { type: [Object, String], default: null },
  lead: { type: String, default: '' },
})

const router = useRouter()

const data = computed(() => {
  let v = props.value
  if (typeof v === 'string') {
    try {
      v = JSON.parse(v)
    } catch {
      return null
    }
  }
  if (!v || typeof v !== 'object') return null
  return { metro: v.metro || '', contractors: v.contractors || [] }
})

const count = computed(() => data.value?.contractors.length || 0)

const label = computed(() => {
  if (count.value === 1) return __('1 contractor')
  if (count.value) return __('{0} contractors', [String(count.value)])
  return data.value?.metro ? __('No contractor') : __('Not in a metro')
})

const title = computed(() => {
  const d = data.value
  if (!d) return ''
  if (count.value)
    return __('{0} on file for {1}', [label.value, d.metro])
  return d.metro
    ? __('No contractor on file for {0}', [d.metro])
    : __("This county isn't in a metro area")
})

const chipClass = computed(() => {
  if (count.value) return 'bg-surface-green-2 text-ink-green-3'
  if (data.value?.metro) return 'bg-surface-amber-2 text-ink-amber-3'
  return 'border border-outline-gray-2 text-ink-gray-5'
})

function call(phone, close) {
  close()
  clickToCall(phone, props.lead ? { lead: props.lead } : {})
}

function openDirectory(close) {
  close()
  router.push({
    name: 'Contractors',
    query: data.value?.metro ? { metro: data.value.metro } : {},
  })
}
</script>
