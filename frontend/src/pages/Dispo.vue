<template>
  <LayoutHeader>
    <template #left-header>
      <div class="flex items-center gap-2">
        <Breadcrumbs :items="[{ label: __('Dispo') }]" />
        <span class="text-ink-gray-4">/</span>
        <!-- property switcher (per-property buyer board) -->
        <Dropdown :options="propertyOptions" placement="left">
          <button
            class="flex items-center gap-1.5 rounded px-2 py-1 text-base font-medium text-ink-gray-8 hover:bg-surface-gray-2"
          >
            <span class="max-w-[22rem] truncate">
              {{ current?.label || __('Select a property') }}
            </span>
            <ChevronDownIcon class="size-4 text-ink-gray-5" />
          </button>
        </Dropdown>
        <Badge
          v-if="current?.il_status"
          :theme="statusTheme(current.il_status)"
          variant="subtle"
        >
          {{ current.il_status }}
        </Badge>
        <a
          v-if="current?.il_marketplace_url"
          :href="current.il_marketplace_url"
          target="_blank"
          class="flex items-center gap-1 text-base text-ink-blue-3 hover:underline"
          :title="__('Open the buyer-facing InvestorLift listing')"
        >
          <ExternalLinkIcon class="size-3.5" />
          {{ __('Public listing') }}
        </a>
      </div>
    </template>
    <template #right-header>
      <div
        v-if="selectedLead"
        class="flex items-center gap-0.5 rounded-md bg-surface-gray-2 p-0.5"
      >
        <button
          class="flex size-6 items-center justify-center rounded"
          :class="viewMode === 'board' ? 'bg-surface-white shadow-sm text-ink-gray-8' : 'text-ink-gray-5'"
          :title="__('Board view')"
          @click="setView('board')"
        >
          <BoardIcon class="size-4" />
        </button>
        <button
          class="flex size-6 items-center justify-center rounded"
          :class="viewMode === 'list' ? 'bg-surface-white shadow-sm text-ink-gray-8' : 'text-ink-gray-5'"
          :title="__('List view')"
          @click="setView('list')"
        >
          <ListIcon class="size-4" />
        </button>
      </div>
      <Button
        v-if="selectedLead"
        variant="ghost"
        :label="__('Text buyers')"
        iconLeft="message-circle"
        @click="openBulkText"
      />
      <Button
        v-if="selectedLead"
        variant="ghost"
        :label="__('Import buyers')"
        iconLeft="upload"
        @click="showImport = true"
      />
      <Button
        v-if="selectedLead"
        variant="solid"
        :label="__('Add buyer')"
        iconLeft="plus"
        @click="showAddBuyer = true"
      />
      <router-link
        v-if="selectedLead"
        :to="{ name: 'Lead', params: { leadId: selectedLead } }"
      >
        <Button variant="ghost" :label="__('Open lead')" iconLeft="external-link" />
      </router-link>
    </template>
  </LayoutHeader>

  <div
    v-if="hasLinkedProperties"
    class="flex min-h-12 items-center gap-2 border-b border-outline-gray-1 px-4 py-2"
  >
    <span class="mr-1 text-sm font-medium text-ink-gray-7">{{ __('InvestorLift') }}</span>
    <Button
      variant="subtle"
      :label="__('Sync property')"
      iconLeft="rotate-ccw"
      :loading="manualSyncing"
      :disabled="!current?.il_property_id || syncAllLoading"
      :tooltip="
        current?.il_property_id
          ? __('Sync only the selected property')
          : __('This property is not linked to InvestorLift')
      "
      @click="requestManualSync"
    />
    <Button
      variant="subtle"
      :label="__('Sync all')"
      iconLeft="refresh-cw"
      :loading="syncAllLoading"
      :disabled="manualSyncing"
      :tooltip="__('Sync every InvestorLift-linked property')"
      @click="requestAllSync"
    />
    <span
      v-if="!current?.il_property_id && !manualSyncing && !syncAllLoading && !syncNotice"
      class="ml-2 text-sm text-ink-gray-5"
    >
      {{ __('Selected property is not linked to InvestorLift.') }}
    </span>
    <div
      v-if="manualSyncing || syncAllLoading"
      class="ml-2 inline-flex items-center gap-2 rounded-full bg-surface-blue-1 px-3 py-1 text-sm font-medium text-ink-blue-3"
      role="status"
      aria-live="polite"
    >
      <span class="size-2 animate-pulse rounded-full bg-ink-blue-3" />
      <span>
        {{
          syncAllLoading
            ? __('Syncing all {0} linked properties…', [syncAllTotal])
            : __('Syncing selected property…')
        }}
      </span>
    </div>
    <div
      v-else-if="syncNotice"
      class="ml-2 inline-flex items-center gap-2 rounded-full bg-surface-green-1 px-3 py-1 text-sm font-medium text-ink-green-3"
      role="status"
      aria-live="polite"
    >
      <span class="size-2 rounded-full bg-ink-green-3" />
      {{ syncNotice }}
    </div>
  </div>

  <div class="flex flex-1 overflow-hidden">
    <DispoBoard
      v-if="selectedLead"
      :key="selectedLead + '-' + boardKey"
      :lead="selectedLead"
      :view="viewMode"
      class="w-full"
    />
    <div
      v-else
      class="flex flex-1 flex-col items-center justify-center gap-2 text-ink-gray-4"
    >
      <DispoIcon class="size-8" />
      <span class="text-base">
        {{
          list.length
            ? __('Select a property to see its buyer board.')
            : __('No properties are in disposition yet. A lead gets a buyer board once it reaches Photos & Lockbox In Progress.')
        }}
      </span>
    </div>
  </div>

  <AddBuyerToDealModal
    v-model="showAddBuyer"
    :lead="selectedLead || ''"
    @saved="boardKey++"
  />
  <ImportBuyersModal
    v-if="showImport"
    v-model="showImport"
    :lead="selectedLead || ''"
    @imported="onImported"
  />
  <BulkTextModal
    v-model="showBulkText"
    :recipients="bulkRecipients"
    :context-label="current?.label || ''"
  />
</template>

<script setup>
import LayoutHeader from '@/components/LayoutHeader.vue'
import DispoBoard from '@/components/Activities/DispoBoard.vue'
import { globalStore } from '@/stores/global'
import AddBuyerToDealModal from '@/components/Modals/AddBuyerToDealModal.vue'
import ImportBuyersModal from '@/components/Modals/ImportBuyersModal.vue'
import BulkTextModal from '@/components/Modals/BulkTextModal.vue'
import DispoIcon from '~icons/lucide/columns-3'
import BoardIcon from '~icons/lucide/columns-3'
import ListIcon from '~icons/lucide/list'
import ChevronDownIcon from '~icons/lucide/chevron-down'
import ExternalLinkIcon from '~icons/lucide/external-link'
import {
  Breadcrumbs,
  Button,
  Badge,
  Dropdown,
  call,
  createResource,
  toast,
  usePageMeta,
} from 'frappe-ui'
import { ref, computed, watch, onMounted, onBeforeUnmount } from 'vue'
import { useRoute, useRouter } from 'vue-router'

const route = useRoute()
const router = useRouter()
const { $socket } = globalStore()

const properties = createResource({
  url: 'crm.api.investorlift_ingest.get_dispo_properties',
  auto: true,
})
const list = computed(() => properties.data || [])
const hasLinkedProperties = computed(() => list.value.some((p) => p.il_property_id))

const selectedLead = ref(route.params.leadId || null)
const showAddBuyer = ref(false)
const showBulkText = ref(false)
const showImport = ref(false)
const boardKey = ref(0)
const manualSyncing = ref(false)
const syncAllLoading = ref(false)
const syncAllTotal = ref(0)
const syncNotice = ref('')
const pendingSyncIds = new Set()
let syncErrors = []
let manualSyncTimeout = null

function onImported() {
  // remount the board (new CRM Lead Buyer rows) and refresh the switcher's
  // per-property buyer counts
  boardKey.value++
  properties.reload()
}

// board vs list view (persisted per-user across visits)
const viewMode = ref(localStorage.getItem('dispoView') === 'list' ? 'list' : 'board')
function setView(v) {
  viewMode.value = v
  localStorage.setItem('dispoView', v)
}

// buyers on the current deal's board — fed to the bulk-text modal. Fetched fresh
// when the button is clicked (deduped to one row per buyer).
const dealBuyers = createResource({
  url: 'crm.api.investorlift_ingest.get_deal_buyers',
  makeParams: () => ({ lead: selectedLead.value }),
})
const bulkRecipients = computed(() => {
  const seen = new Set()
  const out = []
  for (const b of dealBuyers.data || []) {
    if (b.buyer && !seen.has(b.buyer)) {
      seen.add(b.buyer)
      out.push({
        name: b.buyer,
        buyer_name: b.buyer_name,
        phone: b.phone,
        stage: b.interest_stage,
        do_not_contact: b.do_not_contact, // asked to be removed — modal drops them
      })
    }
  }
  return out
})
async function openBulkText() {
  if (!selectedLead.value) return
  await dealBuyers.reload()
  showBulkText.value = true
}

function clearSyncNotice() {
  syncNotice.value = ''
}

function showSyncNotice(message) {
  syncNotice.value = message
}

function stopManualSyncWait() {
  manualSyncing.value = false
  syncAllLoading.value = false
  pendingSyncIds.clear()
  syncErrors = []
  if (manualSyncTimeout) clearTimeout(manualSyncTimeout)
  manualSyncTimeout = null
}

function waitForSync(requests, mode) {
  clearSyncNotice()
  pendingSyncIds.clear()
  for (const request of requests) pendingSyncIds.add(request.request_id)
  syncErrors = []
  syncAllTotal.value = requests.length
  manualSyncing.value = mode === 'property'
  syncAllLoading.value = mode === 'all'
  manualSyncTimeout = setTimeout(() => {
    stopManualSyncWait()
    showSyncNotice(__('InvestorLift sync is taking longer than expected.'))
    toast.error(__('InvestorLift sync is taking longer than expected.'))
  }, 6 * 60 * 1000)
}

async function requestManualSync() {
  if (!selectedLead.value || manualSyncing.value || syncAllLoading.value) return
  manualSyncing.value = true
  try {
    const request = await call('crm.api.investorlift_ingest.request_deal_sync', {
      lead: selectedLead.value,
    })
    waitForSync([request], 'property')
    toast.success(__('InvestorLift property sync queued'))
  } catch (error) {
    stopManualSyncWait()
    toast.error(error?.messages?.[0] || __('Could not queue the InvestorLift sync.'))
  }
}

async function requestAllSync() {
  if (manualSyncing.value || syncAllLoading.value) return
  syncAllLoading.value = true
  try {
    const result = await call('crm.api.investorlift_ingest.request_all_deals_sync')
    waitForSync(result.requests || [], 'all')
    toast.success(__('Queued {0} InvestorLift properties', [result.total]))
  } catch (error) {
    stopManualSyncWait()
    toast.error(error?.messages?.[0] || __('Could not queue all InvestorLift properties.'))
  }
}

function onBuyerSync(data) {
  if (data.reference_docname === selectedLead.value) {
    boardKey.value++
    properties.reload()
  }
}

function onManualSyncComplete(data) {
  if (!pendingSyncIds.has(data.request_id)) return
  pendingSyncIds.delete(data.request_id)
  if (data.reference_docname === selectedLead.value) boardKey.value++
  if (data.status !== 'done') {
    syncErrors.push(data.summary?.errors?.[0] || __('InvestorLift sync failed.'))
  }
  if (pendingSyncIds.size) return

  const errors = [...syncErrors]
  const completedAll = syncAllLoading.value
  const completedTotal = syncAllTotal.value
  stopManualSyncWait()
  properties.reload()
  if (errors.length) {
    showSyncNotice(__('InvestorLift sync failed'))
    toast.error(errors[0])
  } else {
    showSyncNotice(
      completedAll
        ? __('Synced all {0} linked properties', [completedTotal])
        : __('Selected property synced'),
    )
    toast.success(__('InvestorLift sync complete'))
  }
}

onMounted(() => {
  $socket.on('crm_il_buyers', onBuyerSync)
  $socket.on('crm_il_sync_complete', onManualSyncComplete)
})
onBeforeUnmount(() => {
  $socket.off('crm_il_buyers', onBuyerSync)
  $socket.off('crm_il_sync_complete', onManualSyncComplete)
  if (manualSyncTimeout) clearTimeout(manualSyncTimeout)
})

function selectProperty(p, push = true) {
  selectedLead.value = p.lead
  if (push) router.replace({ name: 'Dispo', params: { leadId: p.lead } })
}

// deep-link / nav sync
watch(
  () => route.params.leadId,
  (v) => {
    if (v) selectedLead.value = v
  },
)
// default to the most-recent property when none is chosen
watch(list, (l) => {
  if (!selectedLead.value && l.length) selectProperty(l[0], false)
})

const current = computed(() => list.value.find((p) => p.lead === selectedLead.value))

const propertyOptions = computed(() =>
  list.value.map((p) => ({
    label: p.buyer_count ? `${p.label}  ·  ${p.buyer_count}` : p.label,
    onClick: () => selectProperty(p),
  })),
)

function statusTheme(status) {
  const s = (status || '').toLowerCase()
  if (s === 'sold') return 'green'
  if (s === 'pending') return 'orange'
  if (s === 'available') return 'blue'
  return 'gray'
}

usePageMeta(() => ({ title: 'Dispo' }))
</script>
