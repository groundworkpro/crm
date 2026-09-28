<template>
  <LayoutHeader>
    <template #left-header>
      <Breadcrumbs :items="[{ label: __('Contractors') }]" />
    </template>
    <template #right-header>
      <Button
        variant="solid"
        :label="__('New contractor')"
        iconLeft="plus"
        @click="openNew"
      />
    </template>
  </LayoutHeader>

  <div class="flex flex-1 flex-col overflow-hidden">
    <!-- toolbar -->
    <div class="flex items-center gap-2 border-b px-4 py-2.5 sm:px-5">
      <TextInput
        v-model="search"
        type="text"
        class="w-64"
        :placeholder="__('Search name, company, phone…')"
        :debounce="300"
      >
        <template #prefix>
          <SearchIcon class="size-4 text-ink-gray-5" />
        </template>
      </TextInput>
      <Autocomplete
        :options="metroOptions"
        :modelValue="metroFilter"
        :placeholder="__('All metros')"
        @update:modelValue="(v) => (metroFilter = v?.value || '')"
      >
        <template #target="{ togglePopover }">
          <Button variant="outline" iconRight="chevron-down" @click="togglePopover()">
            <span class="max-w-56 truncate">{{ metroFilter || __('All metros') }}</span>
          </Button>
        </template>
        <template #footer="{ close }">
          <Button
            v-if="metroFilter"
            variant="ghost"
            class="w-full !justify-start"
            :label="__('Clear filter')"
            iconLeft="x"
            @click="metroFilter = ''; close()"
          />
        </template>
      </Autocomplete>
      <FormControl
        class="shrink-0 whitespace-nowrap"
        v-model="showInactive"
        type="checkbox"
        :label="__('Show inactive')"
      />
      <div class="flex-1" />
      <span class="shrink-0 whitespace-nowrap text-sm text-ink-gray-5">
        {{ rows.length }} {{ rows.length === 1 ? __('contractor') : __('contractors') }}
      </span>
    </div>

    <!-- list -->
    <div class="flex-1 overflow-y-auto">
      <div
        class="grid items-center gap-3 border-b px-4 py-2 text-xs font-medium uppercase text-ink-gray-5 sm:px-5"
        :style="gridCols"
      >
        <span>{{ __('Name') }}</span>
        <span>{{ __('Phone') }}</span>
        <span>{{ __('Does') }}</span>
        <span>{{ __('Metro areas') }}</span>
        <span>{{ __('Notes') }}</span>
      </div>

      <div
        v-for="c in rows"
        :key="c.name"
        class="grid cursor-pointer items-center gap-3 border-b border-outline-gray-1 px-4 py-2.5 text-sm text-ink-gray-8 hover:bg-surface-gray-1 sm:px-5"
        :class="{ 'opacity-50': !c.active }"
        :style="gridCols"
        @click="openEdit(c)"
      >
        <span class="min-w-0">
          <span class="block truncate font-medium">{{ c.contractor_name }}</span>
          <span v-if="c.company || !c.active" class="block truncate text-xs text-ink-gray-5">
            {{ [c.company, !c.active ? __('Inactive') : ''].filter(Boolean).join(' · ') }}
          </span>
        </span>
        <span class="min-w-0">
          <button
            v-if="c.phone"
            type="button"
            class="truncate text-ink-gray-6 hover:text-ink-blue-link hover:underline"
            @click.stop="clickToCall(c.phone)"
          >
            {{ formatPhone(c.phone) }}
          </button>
          <span v-else class="text-ink-gray-4">—</span>
          <span v-if="c.email" class="block truncate text-xs text-ink-gray-5">{{ c.email }}</span>
        </span>
        <span class="flex min-w-0 flex-wrap gap-1">
          <span
            v-for="s in c.services"
            :key="s"
            class="rounded bg-surface-gray-2 px-1.5 py-0.5 text-xs text-ink-gray-7"
          >
            {{ s }}
          </span>
          <span v-if="!c.services.length" class="text-ink-gray-4">—</span>
        </span>
        <span class="truncate text-ink-gray-6" :title="c.metros.join(' · ')">
          {{ c.metros.join(' · ') || '—' }}
        </span>
        <span class="truncate text-ink-gray-5" :title="c.notes || ''">
          {{ c.notes || '' }}
        </span>
      </div>

      <div
        v-if="!rows.length && !contractors.loading"
        class="flex flex-col items-center justify-center gap-2 py-16 text-ink-gray-4"
      >
        <HardHatIcon class="size-8" />
        <span class="text-base">
          {{
            metroFilter
              ? __('Nobody on file covers {0} yet.', [metroFilter])
              : search
                ? __('No contractors match.')
                : __('No contractors yet.')
          }}
        </span>
        <Button
          v-if="!search"
          variant="subtle"
          :label="__('Add a contractor')"
          iconLeft="plus"
          @click="openNew"
        />
      </div>
    </div>
  </div>

  <ContractorModal
    v-model="showModal"
    :contractor="editing"
    :metro="metroFilter"
    @saved="contractors.reload()"
  />
</template>

<script setup>
import LayoutHeader from '@/components/LayoutHeader.vue'
import ContractorModal from '@/components/Modals/ContractorModal.vue'
import Autocomplete from '@/components/frappe-ui/Autocomplete.vue'
import SearchIcon from '~icons/lucide/search'
import HardHatIcon from '~icons/lucide/hard-hat'
import { formatPhone } from '@/utils/phoneFormat'
import { clickToCall } from '@/composables/clickToCall'
import {
  Breadcrumbs,
  Button,
  FormControl,
  TextInput,
  createResource,
  usePageMeta,
} from 'frappe-ui'
import { ref, computed, watch } from 'vue'
import { useRoute } from 'vue-router'

const route = useRoute()

const search = ref('')
// seedable via /contractors?metro=… (a board card's "Open contractors")
const metroFilter = ref(String(route.query.metro || ''))
const showInactive = ref(false)
const showModal = ref(false)
const editing = ref(null)

const gridCols = {
  gridTemplateColumns:
    'minmax(10rem,1.2fr) minmax(8rem,10rem) minmax(7rem,9rem) minmax(10rem,1.6fr) minmax(8rem,1.2fr)',
}

const contractors = createResource({
  url: 'crm.api.contractors.get_contractors',
  makeParams: () => ({
    search: search.value || null,
    metro: metroFilter.value || null,
    include_inactive: showInactive.value ? 1 : 0,
  }),
  auto: true,
})
const rows = computed(() => contractors.data || [])

watch([search, metroFilter, showInactive], () => contractors.reload())
watch(
  () => route.query.metro,
  (m) => {
    if (m !== undefined) metroFilter.value = String(m || '')
  },
)

const metros = createResource({ url: 'crm.api.buyers.get_metro_areas', auto: true })
const metroOptions = computed(() =>
  (metros.data || []).map((m) => ({ label: m.metro_name, value: m.name })),
)

function openNew() {
  editing.value = null
  showModal.value = true
}

function openEdit(c) {
  editing.value = c
  showModal.value = true
}

usePageMeta(() => ({ title: 'Contractors' }))
</script>
