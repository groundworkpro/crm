<template>
  <Dialog
    v-model="show"
    :options="{ size: '7xl', title: item?.lead_name || __('Lead details') }"
  >
    <template #body>
      <!-- Nearly the whole window, and it grows with the browser. Dialog's
           own sizes stop at max-w-7xl (1280px) with my-8 chrome, which left a
           laptop looking at a column. :has() on the teleported panel is what
           actually claims the leftover space; the id is the hook. -->
      <div
        id="today-lead-modal"
        class="flex h-[calc(100vh-1rem)] max-h-[calc(100vh-1rem)] flex-col overflow-hidden bg-surface-modal"
      >
        <!-- One dense line. The header used to spend ~90px on a name and two
             badges, while the phone number and address — the two things a rep
             acts on — sat in a rail that is now collapsed on the comps pane.
             Those move up here; everything else keeps its old home. -->
        <div class="flex items-center gap-x-3 border-b px-5 py-2 sm:px-6">
          <div class="flex min-w-0 flex-1 flex-wrap items-center gap-x-3 gap-y-1">
            <h2 class="max-w-full truncate text-base font-semibold text-ink-gray-9">
              {{ item?.lead_name || __('Lead details') }}
            </h2>
            <Badge
              v-if="item?.lead_status"
              variant="subtle"
              theme="gray"
              :label="item.lead_status"
            />
            <Badge
              v-if="item?.non_refundable"
              variant="subtle"
              theme="gray"
              :label="__('No refund')"
              :title="item.non_refundable_reason || __('The provider will not refund this lead')"
            />
            <Badge
              v-if="item?.total_calls > 1"
              variant="subtle"
              theme="blue"
              :label="__('Call {0} of {1}', [item.call_number, item.total_calls])"
            />
            <a
              v-if="item?.mobile_no"
              href="#"
              class="whitespace-nowrap text-sm text-ink-blue-3 hover:underline"
              @click.prevent="clickToCall(item.mobile_no, { lead: item.lead, name: item.lead_name })"
            >
              {{ formatPhone(item.mobile_no) }}
            </a>
            <button
              v-if="item?.address"
              class="min-w-0 truncate text-left text-sm text-ink-gray-6 hover:text-ink-blue-3 hover:underline"
              :title="item.address"
              @click="emit('openAddress', item.address)"
            >
              {{ item.address }}
            </button>
            <DispoBuyerBadges
              v-if="leadDoc"
              fetch
              :city="leadDoc.property_city"
              :state="leadDoc.property_state"
              :county="leadDoc.property_county"
            />
          </div>
          <Button variant="ghost" icon="x" class="shrink-0" @click="show = false" />
        </div>
        <ZillowAddressMatch
          v-if="item?.lead && zillowMatch"
          class="mx-5 mt-2 sm:mx-6"
          :lead="item.lead"
          :address="item.address"
          :match="zillowMatch"
          @saved="onAddressSaved"
          @reran="onCompsReran"
        />

        <div class="flex min-h-0 flex-1 flex-col md:flex-row">
          <!-- Desktop rail. On a phone the 2×2 lives in the Activity pane
               (below) so it does not steal 42vh from the map or the timeline. -->
          <aside
            v-if="sidebarOpen"
            class="hidden shrink-0 overflow-y-auto border-r p-5 md:block md:w-72"
          >
            <div v-if="item?.email" class="flex flex-col gap-2 text-sm text-ink-gray-6">
              <a
                :href="`mailto:${item.email}`"
                class="w-fit hover:text-ink-gray-8 hover:underline"
              >
                {{ item.email }}
              </a>
            </div>

            <div
              v-if="item?.reason"
              class="mt-4 rounded-lg border border-outline-gray-1 bg-surface-gray-1 px-3 py-2 text-sm text-ink-gray-6"
            >
              <div class="mb-0.5 text-xs font-medium text-ink-gray-8">
                {{ __('Why today') }}
              </div>
              {{ item.reason }}
            </div>

            <!-- The exact same editable 2×2 used on the full Lead page. Keeping
                 one component means a Today answer immediately becomes the lead's
                 durable First-Call Read rather than modal-only state. -->
            <div
              v-if="leadDoc && item?.lead"
              class="-mx-4 sm:-mx-5"
              :class="item?.email || item?.reason ? 'mt-4' : ''"
            >
              <FirstCallReadCard
                :lead="item.lead"
                :motivated="leadDoc.first_call_motivated"
                :on-price="leadDoc.first_call_on_price"
                :set-by="leadDoc.first_call_by"
                :set-at="leadDoc.first_call_at"
                @saved="loadLead(true)"
              />
              <!-- Sequences: why a sequence paused, Resume / Move to Long-Term
                   right from Today (Exe, 2026-11-03) -->
              <SequenceStatusCard :lead="item.lead" />
            </div>
            <div
              v-else-if="leadLoading"
              class="mt-4 h-52 animate-pulse rounded-lg bg-surface-gray-2"
            />
          </aside>

          <!-- Activity and the comps MAP are peers here, not a strip stacked above
               a feed. The horizontal card list this replaced could show eight
               comps with no sense of where any of them were, which is the one
               thing that decides whether a sale is comparable; the map answers
               that first and carries the filters, recency fade, hide/use and
               photos with it. Both panes stay mounted, so switching costs neither
               a refetch nor the Activity scroll position, and Activity keeps the
               lead page's ACTUAL surface — calls, texts, to-dos and realtime
               behave exactly as they do there. -->
          <div class="flex min-h-0 flex-1 flex-col overflow-hidden bg-surface-white">
            <div class="flex shrink-0 items-center gap-4 border-b px-4 sm:px-6">
              <Button
                variant="ghost"
                class="-ml-2 hidden shrink-0 md:inline-flex"
                :title="sidebarOpen ? __('Hide lead panel') : __('Show lead panel')"
                @click="sidebarOpen = !sidebarOpen"
              >
                <FeatherIcon
                  :name="sidebarOpen ? 'chevrons-left' : 'chevrons-right'"
                  class="size-4"
                />
              </Button>
              <button
                v-for="p in panes"
                :key="p.value"
                class="-mb-px whitespace-nowrap border-b-2 py-2.5 text-base transition"
                :class="
                  pane === p.value
                    ? 'border-outline-gray-5 font-medium text-ink-gray-9'
                    : 'border-transparent text-ink-gray-5 hover:text-ink-gray-7'
                "
                @click="pane = p.value"
              >
                {{ p.label }}
              </button>
              <!-- The comps pane is the real comps PAGE (page-mode): cash-offer
                   calc, red/yellow pins, photo tray, underwriting. Open-in-tab
                   remains for the extra room. -->
              <Button
                v-if="pane === 'comps' && item?.lead"
                class="ml-auto"
                variant="ghost"
                :label="__('Open comps page')"
                @click="openCompsPage"
              >
                <template #suffix>
                  <FeatherIcon name="arrow-up-right" class="size-4" />
                </template>
              </Button>
            </div>

            <div
              v-show="pane === 'activity'"
              class="flex min-h-0 flex-1 flex-col overflow-hidden"
            >
              <div class="shrink-0 border-b px-4 py-3 md:hidden">
                <div
                  v-if="item?.reason"
                  class="mb-3 rounded-lg border border-outline-gray-1 bg-surface-gray-1 px-3 py-2 text-sm text-ink-gray-6"
                >
                  <div class="mb-0.5 text-xs font-medium text-ink-gray-8">
                    {{ __('Why today') }}
                  </div>
                  {{ item.reason }}
                </div>
                <FirstCallReadCard
                  v-if="leadDoc && item?.lead"
                  :lead="item.lead"
                  :motivated="leadDoc.first_call_motivated"
                  :on-price="leadDoc.first_call_on_price"
                  :set-by="leadDoc.first_call_by"
                  :set-at="leadDoc.first_call_at"
                  @saved="loadLead(true)"
                />
                <div
                  v-else-if="leadLoading"
                  class="h-40 animate-pulse rounded-lg bg-surface-gray-2"
                />
                <SequenceStatusCard v-if="item?.lead" :lead="item.lead" />
              </div>
              <Activities
                v-if="show && item?.lead"
                :key="item.lead"
                v-model:tabIndex="tabIndex"
                doctype="CRM Lead"
                :docname="item.lead"
                :tabs="tabs"
                :scroll-on-mount="false"
              />
            </div>

            <!-- Mounted only once the pane has actually been opened: comps cost a
                 server round trip and, on a lead we have not looked up before, a
                 billed Zillow lookup. Nobody should pay that for a lead they only
                 opened to read the timeline. -->
            <div
              v-if="compsOpened && show && item?.lead"
              v-show="pane === 'comps'"
              class="flex min-h-0 flex-1 flex-col overflow-hidden p-2 sm:p-4"
            >
              <CompsView
                :key="compsKey"
                :lead="item.lead"
                :address="item.address"
                page-mode
                hide-address-match
                @zillow-match="onZillowMatch"
              />
            </div>
          </div>
        </div>

        <div class="hidden justify-end gap-2 border-t px-5 py-3 sm:flex sm:px-6">
          <Button :label="__('Close')" @click="show = false" />
          <Button variant="solid" :label="__('Open full lead')" @click="openFullLead">
            <template #suffix><FeatherIcon name="arrow-up-right" class="size-4" /></template>
          </Button>
        </div>
      </div>
    </template>
  </Dialog>
</template>

<script setup>
import Activities from '@/components/Activities/Activities.vue'
import CompsView from '@/components/CompsView.vue'
import FirstCallReadCard from '@/components/FirstCallReadCard.vue'
import SequenceStatusCard from '@/components/SequenceStatusCard.vue'
import ZillowAddressMatch from '@/components/ZillowAddressMatch.vue'
import DispoBuyerBadges from '@/components/DispoBuyerBadges.vue'
import { formatPhone } from '@/utils/phoneFormat'
import { clickToCall } from '@/composables/clickToCall'
import { Badge, Button, Dialog, FeatherIcon, call } from 'frappe-ui'
import { computed, ref, watch } from 'vue'
import { useRouter } from 'vue-router'

const props = defineProps({
  item: { type: Object, default: null },
})
const emit = defineEmits(['openAddress', 'addressUpdated'])

const show = defineModel({ type: Boolean })
const router = useRouter()
const tabIndex = ref(0)
const tabs = [{ name: 'Activity', label: __('Activity') }]

// Which pane is open. Deliberately NOT reset per lead, unlike the Activity tab
// below: a rep comping a run of leads is in comping mode, and dropping them back
// on the timeline at every card would make them re-click it every time.
const pane = ref('activity')
const panes = computed(() => [
  { value: 'activity', label: __('Activity') },
  { value: 'comps', label: __('Comps') },
])
const compsOpened = ref(false)
const compsGeneration = ref(0)
const compsKey = computed(() => `${props.item?.lead || ''}:${compsGeneration.value}`)
const compsMatch = ref(null)
const leadDoc = ref(null)
const zillowMatch = computed(() => compsMatch.value || matchFromLead(leadDoc.value))
// The rail follows the PANE rather than being remembered: comps wants the room,
// activity does not. A manual toggle wins until the pane changes again, so a rep
// who wants the 2×2 while comping can have it without it sticking for every lead.
const sidebarOpen = ref(true)
watch(pane, (v) => {
  if (v === 'comps') compsOpened.value = true
  sidebarOpen.value = v !== 'comps'
})
const leadLoading = ref(false)
const leadCache = new Map()
let leadRequestToken = 0

function matchFromLead(doc) {
  if (!doc?.zillow_fetched_at && !doc?.zillow_zpid) return null
  let queried = ''
  if (doc.zillow_facts) {
    try {
      queried = JSON.parse(doc.zillow_facts)?._queried_address || ''
    } catch {
      queried = ''
    }
  }
  return {
    tried: true,
    matched: Boolean(doc.zillow_zpid),
    zpid: doc.zillow_zpid || '',
    queried_address: queried,
  }
}

watch(
  () => props.item?.lead,
  (lead) => {
    tabIndex.value = 0
    compsMatch.value = null
    // Never render the previous lead's answers against the new lead id while its
    // document is loading — a click in that window would save the wrong state.
    leadDoc.value = leadCache.get(lead) || null
  },
)
watch(
  () => [show.value, props.item?.lead],
  ([open]) => {
    if (open && props.item?.lead) loadLead()
  },
  { immediate: true },
)

async function loadLead(force = false) {
  const lead = props.item?.lead
  if (!lead) return
  if (!force && leadCache.has(lead)) {
    leadDoc.value = leadCache.get(lead)
    return
  }
  const token = ++leadRequestToken
  leadLoading.value = true
  try {
    const doc = await call('frappe.client.get', { doctype: 'CRM Lead', name: lead })
    if (token !== leadRequestToken) return
    leadDoc.value = doc
    leadCache.set(lead, doc)
  } catch {
    if (token === leadRequestToken && !force) leadDoc.value = null
  } finally {
    if (token === leadRequestToken) leadLoading.value = false
  }
}

function onZillowMatch(match) {
  compsMatch.value = match || null
}

function onAddressSaved(address) {
  emit('addressUpdated', { lead: props.item?.lead, address })
}

function onCompsReran(res) {
  compsMatch.value = {
    tried: true,
    matched: Boolean(res?.matched),
    zpid: res?.zpid || '',
    queried_address: res?.queried_address || res?.address || '',
  }
  compsGeneration.value += 1
  compsOpened.value = true
  pane.value = 'comps'
  loadLead(true)
  emit('addressUpdated', {
    lead: props.item?.lead,
    address: res?.address || props.item?.address,
    zillow_unresolved: !res?.matched,
  })
}

function openFullLead() {
  if (!props.item?.lead) return
  show.value = false
  router.push({ name: 'Lead', params: { leadId: props.item.lead } })
}

// A new tab, matching both Lead pages: the comps page is something a rep leaves
// open beside the board, and navigating away would close the card they are on.
function openCompsPage() {
  if (!props.item?.lead) return
  const win = window.open(`/crm/leads/${props.item.lead}/comps`, '_blank')
  if (win) win.opener = null
  else router.push({ name: 'Comps', params: { leadId: props.item.lead } })
}
</script>

<!-- Unscoped: Dialog teleports to <body>, so a scoped rule never reaches it. -->
<style>
.dialog-overlay:has(#today-lead-modal) > div {
  padding: 0.5rem !important;
}
.dialog-content:has(#today-lead-modal) {
  max-width: calc(100vw - 1rem) !important;
  width: calc(100vw - 1rem);
  margin-top: 0 !important;
  margin-bottom: 0 !important;
}
</style>
