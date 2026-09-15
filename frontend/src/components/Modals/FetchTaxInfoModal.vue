<template>
  <Dialog v-model="show" :options="{ title: __('Fetch tax / liens') }">
    <template #body-content>
      <div class="flex flex-col gap-4 text-base">
        <!-- The charge confirmation — the whole point of this dialog. -->
        <div
          class="flex items-start gap-2.5 rounded-md bg-surface-amber-1 px-3 py-2.5 text-ink-amber-3"
        >
          <FeatherIcon name="dollar-sign" class="mt-0.5 size-4 shrink-0" />
          <div>
            <div class="font-medium text-ink-gray-8">
              {{ __('This will charge $0.22 to BatchData.') }}
            </div>
            <div class="mt-0.5 text-sm text-ink-gray-6">
              {{ __('Pulls owner, APN, tax history, deeds, mortgages, foreclosure and open liens.') }}
            </div>
          </div>
        </div>

        <div v-if="propertyAddress" class="flex flex-col gap-0.5">
          <div class="text-xs text-ink-gray-5">{{ __('Property') }}</div>
          <div class="text-ink-gray-8">{{ propertyAddress }}</div>
        </div>

        <div class="flex flex-col gap-0.5">
          <div class="text-xs text-ink-gray-5">{{ __('Requested by') }}</div>
          <div class="text-ink-gray-8">{{ currentUserName }}</div>
        </div>

        <!-- Re-pull warning: a prior pull exists, so this is an extra charge. -->
        <div
          v-if="lastPull"
          class="flex items-start gap-2 rounded-md bg-surface-gray-2 px-3 py-2 text-sm text-ink-gray-6"
        >
          <FeatherIcon name="rotate-ccw" class="mt-0.5 size-3.5 shrink-0" />
          <span>
            {{ __('Last pulled by') }}
            <span class="font-medium text-ink-gray-8">{{
              lastPull.pulled_by_name || lastPull.pulled_by
            }}</span>
            {{ __('on') }}
            {{ formatDate(lastPull.pulled_at || lastPull.creation, '', true) }}.
            {{ __('Fetching again will charge another $0.22.') }}
          </span>
        </div>

        <ErrorMessage :message="error" />
      </div>
    </template>
    <template #actions>
      <Button
        class="w-full"
        variant="solid"
        :label="lastPull ? __('Fetch again ($0.22)') : __('Fetch tax / liens ($0.22)')"
        :loading="loading"
        @click="fetchTaxInfo"
      />
    </template>
  </Dialog>
</template>

<script setup>
import { formatDate } from '@/utils'
import { usersStore } from '@/stores/users'
import {
  call,
  Dialog,
  Button,
  ErrorMessage,
  FeatherIcon,
  toast,
} from 'frappe-ui'
import { ref, computed, watch } from 'vue'

const props = defineProps({
  referenceDoc: { type: Object, default: () => ({}) },
  options: { type: Object, default: () => ({ afterPull: () => {} }) },
})

const show = defineModel({ type: Boolean })

const { getUser } = usersStore()

const loading = ref(false)
const error = ref(null)
const lastPull = ref(null)

const currentUserName = computed(() => {
  const u = getUser()
  return u?.full_name || u?.name || ''
})

const propertyAddress = computed(() => props.referenceDoc?.property_address || '')

function leadName() {
  return props.referenceDoc?.name || ''
}

// On open, surface the most-recent prior pull so the user sees a re-pull warning.
watch(
  show,
  async (open) => {
    if (!open) return
    error.value = null
    lastPull.value = null
    const lead = leadName()
    if (!lead) return
    try {
      const pulls = await call('crm.api.tax_info.get_tax_pulls', { lead })
      lastPull.value = pulls?.[0] || null
    } catch (e) {
      // non-fatal — the warning is a nicety, the pull still works
    }
  },
  { immediate: true },
)

async function fetchTaxInfo() {
  const lead = leadName()
  if (!lead || loading.value) return
  loading.value = true
  error.value = null
  try {
    await call('pull-tax-info', { lead })
    toast.success(__('Tax info fetched'))
    show.value = false
    props.options.afterPull?.()
  } catch (e) {
    error.value = e.messages?.[0] || e.message || __('Failed to fetch tax info')
  } finally {
    loading.value = false
  }
}
</script>
