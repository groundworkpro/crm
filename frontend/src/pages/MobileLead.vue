<template>
  <LayoutHeader>
    <header
      class="relative flex h-10.5 items-center justify-between gap-2 py-2.5 pl-2"
    >
      <Breadcrumbs :items="breadcrumbs">
        <template #prefix="{ item }">
          <Icon v-if="item.icon" :icon="item.icon" class="mr-2 h-4" />
        </template>
      </Breadcrumbs>
      <div class="absolute right-0">
        <Dropdown
          v-if="doc"
          :options="
            statusOptions(
              'lead',
              document.statuses?.length
                ? document.statuses
                : document._statuses,
              triggerStatusChange,
            )
          "
        >
          <template #default="{ open }">
            <Button
              v-if="doc.status"
              :label="statusLabel(doc.status)"
              :iconRight="open ? 'chevron-up' : 'chevron-down'"
            >
              <template #prefix>
                <IndicatorIcon :class="getLeadStatus(doc.status).color" />
              </template>
            </Button>
          </template>
        </Dropdown>
      </div>
    </header>
  </LayoutHeader>
  <div
    v-if="doc.name"
    class="flex h-12 items-center justify-between gap-2 border-b px-3 py-2.5"
  >
    <AssignTo v-model="assignees.data" doctype="CRM Lead" :docname="leadId" />
    <Button v-if="workspace.isNext" variant="subtle" size="sm" :label="__('Team chat')" @click="openLeadChat" />
    <div class="flex items-center gap-2">
      <CustomActions
        v-if="document._actions?.length"
        :actions="document._actions"
      />
      <CustomActions
        v-if="document.actions?.length"
        :actions="document.actions"
      />
      <Dropdown :options="moreActions" placement="bottom-end">
        <Button :tooltip="__('More actions')" icon="more-horizontal" />
      </Dropdown>
    </div>
  </div>
  <div v-if="doc.name" class="flex h-full overflow-hidden">
    <Tabs
      v-model="tabIndex"
      as="div"
      :tabs="tabs"
      class="flex flex-1 overflow-auto flex-col [&_[role='tab']]:px-0 [&_[role='tab']]:shrink-0 [&_[role='tablist']]:px-3 [&_[role='tablist']]:min-h-[45px] [&_[role='tablist']]:gap-7.5 [&_[role='tabpanel']:not([hidden])]:flex [&_[role='tabpanel']:not([hidden])]:grow"
    >
      <template #tab-panel="{ tab }">
        <div
          v-if="tab.name == 'Details'"
          class="flex flex-1 flex-col overflow-y-auto"
        >
          <!-- Compact header: avatar + name + address + the acq-price / DD
               fields (which live only in the desktop sidebar header) + call /
               text, so nothing from the desktop details is missing on mobile. -->
          <div class="flex items-start gap-4 border-b p-4">
            <Avatar size="2xl" class="shrink-0" :label="title" :image="doc.image" />
            <div class="flex min-w-0 flex-col gap-1.5">
              <div class="truncate text-lg font-medium text-ink-gray-9">
                {{ title }}
              </div>
              <a
                v-if="doc.property_address"
                :href="`https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(doc.property_address)}`"
                target="_blank"
                rel="noopener noreferrer"
                :title="doc.property_address"
                class="flex items-center gap-1.5 truncate text-sm text-ink-gray-7 hover:text-ink-gray-9 hover:underline"
              >
                <AddressIcon class="size-3.5 shrink-0" />
                <span class="truncate">{{ doc.property_address }}</span>
              </a>
              <DispoBuyerBadges
                fetch
                class="whitespace-normal"
                :city="doc.property_city"
                :state="doc.property_state"
                :county="doc.property_county"
              />
              <!-- Acq price (digits only, live thousand separators) -->
              <div
                class="flex items-center gap-1.5 text-sm text-ink-gray-7"
                :title="__('Acq Price')"
              >
                <MoneyIcon class="size-3.5 shrink-0" />
                <input
                  :value="acqPriceDraft"
                  type="text"
                  inputmode="numeric"
                  autocomplete="off"
                  :placeholder="__('Acq price')"
                  class="w-32 border-none bg-transparent p-0 text-sm text-ink-gray-7 placeholder:text-ink-gray-4 focus:text-ink-gray-9 focus:outline-none focus:ring-0"
                  @focus="acqPriceFocused = true"
                  @input="onAcqPriceInput"
                  @keydown.enter.prevent="$event.target.blur()"
                  @keydown.esc.prevent="
                    acqPriceDraft = formatAcqPrice(doc.acq_price);
                    $event.target.blur()
                  "
                  @blur="saveAcqPrice"
                />
              </div>
              <!-- DD expiration: date + "(N days left)" countdown -->
              <div
                class="group/dd flex items-center gap-1.5 text-sm text-ink-gray-7"
                :title="__('DD Expiration')"
              >
                <CalendarIcon class="size-3.5 shrink-0" />
                <div
                  class="relative flex cursor-pointer items-center"
                  @click="openDdPicker"
                >
                  <span
                    :class="
                      ddExp.color
                        ? parseColor(ddExp.color)
                        : doc.dd_expiration_date
                          ? 'text-ink-gray-7'
                          : 'text-ink-gray-4'
                    "
                  >
                    {{ ddExp.label || __('DD expiration') }}
                  </span>
                  <input
                    ref="ddDateInput"
                    type="date"
                    tabindex="-1"
                    :value="doc.dd_expiration_date || ''"
                    class="pointer-events-none absolute inset-0 h-full w-full opacity-0"
                    @change="saveDdExpiration"
                  />
                </div>
                <FeatherIcon
                  v-if="doc.dd_expiration_date"
                  name="x"
                  class="size-3.5 shrink-0 cursor-pointer text-ink-gray-5 opacity-0 duration-200 hover:text-ink-gray-8 group-hover/dd:opacity-100"
                  :title="__('Clear DD expiration')"
                  @click.stop="updateField('dd_expiration_date', '')"
                />
              </div>
              <!-- Showing access: free-text access instructions ("vacant",
                   "lockbox 4127", …); wraps, Enter/blur saves, Esc reverts. -->
              <div
                class="flex items-start gap-1.5 text-sm text-ink-gray-7"
                :title="__('Showing Access')"
              >
                <FeatherIcon name="key" class="mt-0.5 size-3.5 shrink-0" />
                <textarea
                  ref="showingAccessInput"
                  v-model="showingAccessDraft"
                  rows="1"
                  autocomplete="off"
                  :placeholder="__('Showing access (vacant, lockbox code…)')"
                  class="w-full resize-none overflow-hidden whitespace-pre-wrap border-none bg-transparent p-0 text-sm leading-5 text-ink-gray-7 placeholder:text-ink-gray-4 focus:text-ink-gray-9 focus:outline-none focus:ring-0"
                  @focus="showingAccessFocused = true"
                  @input="resizeShowingAccess"
                  @keydown.enter.prevent="$event.target.blur()"
                  @keydown.esc.prevent="
                    showingAccessDraft = doc.showing_access || '';
                    $event.target.blur()
                  "
                  @blur="saveShowingAccess"
                />
              </div>
              <div class="mt-1 flex gap-1.5">
                <Dropdown
                  v-if="leadPhones.length > 1"
                  :options="callPhoneOptions"
                  placement="bottom-start"
                >
                  <Button :tooltip="__('Call')" :icon="PhoneIcon" />
                </Dropdown>
                <Button
                  v-else
                  :tooltip="__('Call')"
                  :icon="PhoneIcon"
                  @click="
                    () =>
                      primaryPhone
                        ? dialNumber(primaryPhone)
                        : toast.error(__('Please set a mobile number to call'))
                  "
                />
                <Button
                  v-if="smsEnabled"
                  :tooltip="__('Send a Text')"
                  :icon="CommentIcon"
                  @click="detailModals?.sendText()"
                />
                <Button
                  :tooltip="__('View comps')"
                  icon="map-pin"
                  @click="openComps()"
                />
                <Button
                  :tooltip="__('Got a live one? Alert the closer')"
                  icon="zap"
                  @click="showLiveOne = true"
                />
              </div>
            </div>
          </div>

          <SLASection
            v-if="doc.sla_status"
            v-model="doc"
            @updateField="updateField"
          />
          <LeadPhonesCard
            :lead="leadId"
            :doc="doc"
            @saved="onPhonesSaved"
            @dial="dialNumber"
          />
          <FirstCallReadCard
            :lead="leadId"
            :motivated="doc.first_call_motivated"
            :onPrice="doc.first_call_on_price"
            :setBy="doc.first_call_by"
            :setAt="doc.first_call_at"
            @saved="document.reload()"
          />
          <RefundStatusCard
            :lead="leadId"
            :refundable="doc.custom_refundable"
            :notInProvider="doc.custom_refund_not_in_provider"
            :manualTicket="doc.custom_refund_manual_ticket"
            :status="doc.custom_refund_status"
            :nonRefundable="doc.custom_non_refundable"
            :nonRefundableReason="doc.custom_non_refundable_reason"
            @saved="document.reload()"
          />
          <PhotosCard :lead="leadId" @open="showPhotoGallery = true" />
          <LinkedPropertiesCard :lead="leadId" />
          <TaxInfoCard :lead="leadId" @fetch="detailModals?.fetchTaxInfo()" />
          <AgreementsCard
            :lead="leadId"
            @create="detailModals?.createAgreement()"
          />
          <UnderwritingCard
            :lead="leadId"
            @create="detailModals?.createUnderwriting()"
          />
          <InvestorLiftCard :lead="leadId" :address="doc.property_address" />
          <div v-if="sections.data" class="flex flex-col">
            <SidePanelLayout
              :sections="sidePanelWithoutPhones"
              doctype="CRM Lead"
              :docname="leadId"
              @reload="sections.reload"
              @beforeFieldChange="beforeStatusChange"
              @afterFieldChange="reloadAssignees"
            />
          </div>
          <!-- Mobile Details has no always-mounted Activities (tabs are
               mutually exclusive), so it hosts its own modals for the card
               actions. `reloader` stands in for the activities reload target. -->
          <AllModals
            ref="detailModals"
            doctype="CRM Lead"
            :model-value="reloader"
            :doc="doc"
          />
        </div>
        <Activities
          v-else
          ref="activities"
          v-model:reload="reload"
          v-model:tabIndex="tabIndex"
          doctype="CRM Lead"
          :docname="leadId"
          :tabs="tabs"
          @beforeSave="beforeStatusChange"
          @afterSave="reloadAssignees"
        />
      </template>
    </Tabs>
  </div>
  <ErrorPage
    v-else-if="errorTitle"
    :errorTitle="errorTitle"
    :errorMessage="errorMessage"
  />
  <FilesUploader
    v-model="showFilesUploader"
    doctype="CRM Lead"
    :docname="leadId"
    @after="afterAttach"
  />
  <DeleteLinkedDocModal
    v-if="showDeleteLinkedDocModal"
    v-model="showDeleteLinkedDocModal"
    :doctype="'CRM Lead'"
    :docname="leadId"
    name="Leads"
  />
  <LostReasonModal
    v-if="showLostReasonModal"
    v-model="showLostReasonModal"
    doctype="CRM Lead"
    :document="document"
  />
  <PhotoGalleryModal
    v-if="showPhotoGallery"
    v-model="showPhotoGallery"
    :lead="leadId"
    :address="doc.property_address"
  />
  <LiveOneModal
    v-model="showLiveOne"
    :lead="leadId"
    :leadName="doc.lead_name"
    :address="doc.property_address"
  />
</template>
<script setup>
import DeleteLinkedDocModal from '@/components/DeleteLinkedDocModal.vue'
import ErrorPage from '@/components/ErrorPage.vue'
import Icon from '@/components/Icon.vue'
import DetailsIcon from '@/components/Icons/DetailsIcon.vue'
import ActivityIcon from '@/components/Icons/ActivityIcon.vue'
import EmailIcon from '@/components/Icons/EmailIcon.vue'
import CommentIcon from '@/components/Icons/CommentIcon.vue'
import PhoneIcon from '@/components/Icons/PhoneIcon.vue'
import TaskIcon from '@/components/Icons/TaskIcon.vue'
import NoteIcon from '@/components/Icons/NoteIcon.vue'
import AttachmentIcon from '@/components/Icons/AttachmentIcon.vue'
import WhatsAppIcon from '@/components/Icons/WhatsAppIcon.vue'
import IndicatorIcon from '@/components/Icons/IndicatorIcon.vue'
import LostReasonModal from '@/components/Modals/LostReasonModal.vue'
import AddressIcon from '@/components/Icons/AddressIcon.vue'
import MoneyIcon from '@/components/Icons/MoneyIcon.vue'
import CalendarIcon from '@/components/Icons/CalendarIcon.vue'
import LayoutHeader from '@/components/LayoutHeader.vue'
import Activities from '@/components/Activities/Activities.vue'
import AllModals from '@/components/Activities/AllModals.vue'
import AssignTo from '@/components/AssignTo.vue'
import SidePanelLayout from '@/components/SidePanelLayout.vue'
import SLASection from '@/components/SLASection.vue'
import CustomActions from '@/components/CustomActions.vue'
import FirstCallReadCard from '@/components/FirstCallReadCard.vue'
import RefundStatusCard from '@/components/RefundStatusCard.vue'
import LeadPhonesCard from '@/components/LeadPhonesCard.vue'
import PhotosCard from '@/components/PhotosCard.vue'
import LinkedPropertiesCard from '@/components/LinkedPropertiesCard.vue'
import PhotoGalleryModal from '@/components/Modals/PhotoGalleryModal.vue'
import LiveOneModal from '@/components/Modals/LiveOneModal.vue'
import TaxInfoCard from '@/components/TaxInfoCard.vue'
import AgreementsCard from '@/components/AgreementsCard.vue'
import UnderwritingCard from '@/components/UnderwritingCard.vue'
import InvestorLiftCard from '@/components/InvestorLiftCard.vue'
import DispoBuyerBadges from '@/components/DispoBuyerBadges.vue'
import { setupCustomizations, isTranslatable, ddExpiration, parseColor, openWebsite } from '@/utils'
import { getView } from '@/utils/view'
import { formatPhone } from '@/utils/phoneFormat'
import { listLeadPhones, primaryLeadPhone } from '@/utils/leadPhones'
import { clickToCall } from '@/composables/clickToCall'
import { getSettings } from '@/stores/settings'
import { globalStore } from '@/stores/global'
import { workspaceStore } from '@/stores/workspace'
import { statusesStore } from '@/stores/statuses'
import { getMeta } from '@/stores/meta'
import { useDocument } from '@/data/document'
import { whatsappEnabled, smsEnabled, isMobileView } from '@/composables/settings'
import { useActiveTabManager } from '@/composables/useActiveTabManager'
import {
  createResource,
  Avatar,
  Dropdown,
  Tabs,
  Breadcrumbs,
  FeatherIcon,
  call,
  usePageMeta,
  toast,
} from 'frappe-ui'
import {
  ref,
  computed,
  watch,
  nextTick,
  onMounted,
  onBeforeUnmount,
} from 'vue'
import { useRouter, useRoute } from 'vue-router'
import FilesUploader from '@/components/FilesUploader/FilesUploader.vue'
import { mapsUrl, zillowUrl } from '@/utils/propertyLinks'

const { brand } = getSettings()
const { $dialog, $socket } = globalStore()
const workspace = workspaceStore()
function openLeadChat() {
  router.push({ name: 'Talk', params: { kind: 'lead', id: props.leadId } })
}
const { statusOptions, getLeadStatus } = statusesStore()
const { doctypeMeta } = getMeta('CRM Lead')

const route = useRoute()
const router = useRouter()

const props = defineProps({
  leadId: { type: String, required: true },
})

const errorTitle = ref('')
const errorMessage = ref('')
const showDeleteLinkedDocModal = ref(false)
// Property photos (shared Google Drive). Mobile Details renders its own copy of
// the sidebar cards, so the gallery modal is mounted here too — see Lead.vue.
const showPhotoGallery = ref(false)
const showLiveOne = ref(false)
// Same as desktop: comps is a page, opened in a new tab.
const activities = ref(null)
const showFilesUploader = ref(false)

function openComps() {
  if (!doc.value.property_address) {
    toast.error(__('Set a property address to see comps'))
    return
  }
  const url = `/crm/leads/${props.leadId}/comps`
  // NOT window.open(url, '_blank', 'noopener'): passing `noopener` makes the
  // call return null BY SPEC, so a blocked popup and a successful one are
  // indistinguishable -- and a blocked one looks exactly like a dead button.
  // Opening plainly and severing `opener` afterwards gives the same isolation
  // while still letting us detect the block and fall back to navigating here.
  const win = window.open(url, '_blank')
  if (win) win.opener = null
  else router.push({ name: 'Comps', params: { leadId: props.leadId } })
}

const {
  triggerOnChange,
  triggerOnRender,
  assignees,
  document,
  scripts,
  error,
} = useDocument('CRM Lead', props.leadId)

const doc = computed(() => document.doc || {})

onMounted(async () => {
  if (document.doc) await triggerOnRender()
})

watch(error, (err) => {
  if (err) {
    errorTitle.value = __(
      err.exc_type == 'DoesNotExistError'
        ? __('Document Not Found')
        : __('Error Occurred'),
    )
    errorMessage.value = __(err.messages?.[0] || 'An Error Occurred')
  } else {
    errorTitle.value = ''
    errorMessage.value = ''
  }
})

watch(
  () => document.doc,
  async (_doc) => {
    if (scripts.data?.length) {
      let s = await setupCustomizations(scripts.data, {
        doc: _doc,
        $dialog,
        $socket,
        router,
        toast,
        updateField,
        createToast: toast.create,
        deleteDoc: deleteLead,
        call,
      })
      document._actions = s.actions || []
      document._statuses = s.statuses || []
    }
  },
  { once: true },
)

const reload = ref(false)

const breadcrumbs = computed(() => {
  let items = [{ label: __('Leads'), route: { name: 'Leads' } }]

  if (route.query.view || route.query.viewType) {
    let view = getView(route.query.view, route.query.viewType, 'CRM Lead')
    if (view) {
      items.push({
        label: __(view.label),
        icon: view.icon,
        route: {
          name: 'Leads',
          params: { viewType: route.query.viewType },
          query: { view: route.query.view },
        },
      })
    }
  }

  items.push({
    label: title.value,
    route: { name: 'Lead', params: { leadId: props.leadId } },
  })
  return items
})

const title = computed(() => {
  let t = doctypeMeta.value?.title_field || 'name'
  return doc.value?.[t] || props.leadId
})

usePageMeta(() => {
  return {
    title: title.value,
    icon: brand.favicon,
  }
})

const tabs = computed(() => {
  let tabOptions = [
    {
      name: 'Details',
      label: __('Details'),
      icon: DetailsIcon,
      condition: () => isMobileView.value,
    },
    {
      name: 'Activity',
      label: __('Activity'),
      icon: ActivityIcon,
    },
    {
      name: 'Emails',
      label: __('Emails'),
      icon: EmailIcon,
    },
    {
      name: 'Comments',
      label: __('Comments'),
      icon: CommentIcon,
    },
    {
      name: 'Data',
      label: __('Data'),
      icon: DetailsIcon,
    },
    {
      name: 'Calls',
      label: __('Calls'),
      icon: PhoneIcon,
    },
    {
      name: 'Tasks',
      label: __('Tasks'),
      icon: TaskIcon,
    },
    {
      name: 'Notes',
      label: __('Notes'),
      icon: NoteIcon,
    },
    {
      name: 'Attachments',
      label: __('Attachments'),
      icon: AttachmentIcon,
    },
    {
      name: 'WhatsApp',
      label: __('WhatsApp'),
      icon: WhatsAppIcon,
      condition: () => whatsappEnabled.value,
    },
  ]
  return tabOptions.filter((tab) => (tab.condition ? tab.condition() : true))
})

const { tabIndex } = useActiveTabManager(tabs, 'lastLeadTab')

const sections = createResource({
  url: 'crm.fcrm.doctype.crm_fields_layout.crm_fields_layout.get_sidepanel_sections',
  cache: ['sidePanelSections', 'CRM Lead'],
  params: { doctype: 'CRM Lead' },
  auto: true,
})

// Details-tab modal host (tax / agreement / underwriting / text). Its reload
// target just refreshes the doc + sidebar sections; the cards themselves also
// self-refresh via their realtime events.
const detailModals = ref(null)
const reloader = {
  reload: () => {
    document.reload()
    sections.reload()
  },
}

// Acq price inline editor (digits only, live thousand separators) — mirrors the
// desktop Lead sidebar header.
const acqPriceDraft = ref('')
const acqPriceFocused = ref(false)

function formatAcqPrice(n) {
  return n ? Math.round(n).toLocaleString('en-US') : ''
}

watch(
  () => doc.value?.acq_price,
  (v) => {
    if (!acqPriceFocused.value) acqPriceDraft.value = formatAcqPrice(v)
  },
  { immediate: true },
)

function onAcqPriceInput(e) {
  const digits = e.target.value.replace(/\D/g, '').slice(0, 12)
  const formatted = digits ? Number(digits).toLocaleString('en-US') : ''
  acqPriceDraft.value = formatted
  e.target.value = formatted
}

function saveAcqPrice() {
  acqPriceFocused.value = false
  const numeric = Number(acqPriceDraft.value.replace(/\D/g, '')) || 0
  if (numeric === Math.round(doc.value.acq_price || 0)) {
    acqPriceDraft.value = formatAcqPrice(doc.value.acq_price)
    return
  }
  updateField('acq_price', numeric)
}

// DD expiration inline editor (native date picker + countdown).
const ddDateInput = ref(null)
const ddExp = computed(() => ddExpiration(doc.value?.dd_expiration_date))

function openDdPicker() {
  const el = ddDateInput.value
  if (!el) return
  try {
    el.showPicker()
  } catch {
    el.focus()
  }
}

function saveDdExpiration(e) {
  const value = e.target.value || ''
  if (value === (doc.value.dd_expiration_date || '')) return
  updateField('dd_expiration_date', value)
}

// Showing access inline editor (auto-growing textarea) — mirrors the desktop
// Lead sidebar header.
const showingAccessInput = ref(null)
const showingAccessDraft = ref('')
const showingAccessFocused = ref(false)

watch(
  () => doc.value?.showing_access,
  (v) => {
    if (showingAccessFocused.value) return
    showingAccessDraft.value = v || ''
    nextTick(resizeShowingAccess)
  },
  { immediate: true },
)

function resizeShowingAccess() {
  const el = showingAccessInput.value
  if (!el) return
  el.style.height = 'auto'
  el.style.height = `${el.scrollHeight}px`
}

// Height depends on width (see Lead.vue): re-measure whenever the textarea's
// box changes, or a value measured while hidden/narrow sticks as a tall gap.
let showingAccessRO = null
watch(showingAccessInput, (el) => {
  showingAccessRO?.disconnect()
  showingAccessRO = null
  if (!el || typeof ResizeObserver === 'undefined') return
  showingAccessRO = new ResizeObserver(() => {
    requestAnimationFrame(resizeShowingAccess)
  })
  showingAccessRO.observe(el)
})
onBeforeUnmount(() => showingAccessRO?.disconnect())

function saveShowingAccess() {
  showingAccessFocused.value = false
  const value = showingAccessDraft.value.trim()
  showingAccessDraft.value = value
  nextTick(resizeShowingAccess)
  if (value === (doc.value.showing_access || '')) return
  updateField('showing_access', value)
}

const leadPhones = computed(() => listLeadPhones(doc.value))
const primaryPhone = computed(() => primaryLeadPhone(doc.value))
const callPhoneOptions = computed(() =>
  leadPhones.value.map((p) => ({
    label: formatPhone(p.number) + (p.primary ? ` (${__('primary')})` : ''),
    icon: 'phone',
    onClick: () => dialNumber(p.number),
  })),
)
const sidePanelWithoutPhones = computed(() =>
  (sections.data || []).map((section) => ({
    ...section,
    columns: (section.columns || []).map((col) => ({
      ...col,
      fields: (col.fields || []).filter(
        (f) => !['mobile_no', 'phone'].includes(f.fieldname),
      ),
    })),
  })),
)

function onPhonesSaved() {
  document.reload()
  reload.value = true
}

function dialNumber(number) {
  clickToCall(number, { lead: props.leadId, name: doc.value.lead_name || doc.value.first_name })
}

function updateField(name, value) {
  value = Array.isArray(name) ? '' : value
  let oldValues = Array.isArray(name) ? {} : doc.value[name]

  if (Array.isArray(name)) {
    name.forEach((field) => (doc.value[field] = value))
  } else {
    doc.value[name] = value
  }

  document.save.submit(null, {
    onSuccess: () => (reload.value = true),
    onError: (err) => {
      if (Array.isArray(name)) {
        name.forEach((field) => (doc.value[field] = oldValues[field]))
      } else {
        doc.value[name] = oldValues
      }
      toast.error(err.messages?.[0] || __('Error updating field'))
    },
  })
}

function deleteLead() {
  showDeleteLinkedDocModal.value = true
}

function afterAttach() {
  const i = tabs.value.findIndex((t) => t.name === 'Attachments')
  if (i >= 0) tabIndex.value = i
}

function openEmailBox() {
  const i = tabs.value.findIndex((t) => t.name === 'Emails')
  if (i >= 0) tabIndex.value = i
  nextTick(() => {
    if (activities.value?.emailBox) activities.value.emailBox.show = true
  })
}

function openResearchTabs() {
  const address = doc.value.property_address
  if (!address) {
    toast.error(__('Set a property address to open research tabs'))
    return
  }
  // Comps + one Zillow + Maps (Dennis, 2026-09-10: Zillow is a reference
  // point now, not the comping tool, so the second Zillow tab is gone).
  window.open(`/crm/leads/${props.leadId}/comps`, '_blank', 'noopener')
  window.open(zillowUrl(address), '_blank', 'noopener')
  window.open(mapsUrl(address), '_blank', 'noopener')
}

const moreActions = computed(() => {
  const d = doc.value
  const items = []
  if (d?.import_hidden) {
    items.push({
      label: __('Add to main board'),
      icon: 'eye',
      onClick: async () => {
        await call('crm.api.lead_import.unhide_leads', {
          leads: JSON.stringify([d.name]),
        })
        toast.success(__('Added to the main board'))
        document.reload()
      },
    })
  }
  items.push({
    label: __('View comps'),
    icon: 'map-pin',
    onClick: openComps,
  })
  items.push({
    label: __('Open Research tabs'),
    icon: 'external-link',
    onClick: openResearchTabs,
  })
  items.push({
    label: __('View on Zillow'),
    icon: 'home',
    onClick: () =>
      d.property_address
        ? window.open(zillowUrl(d.property_address), '_blank', 'noopener')
        : toast.error(__('Set a property address to view on Zillow')),
  })
  items.push({
    label: __('Send an Email'),
    icon: 'mail',
    onClick: () =>
      d.email
        ? openEmailBox()
        : toast.error(__('Please set an email address to send emails')),
  })
  items.push({
    label: __('Go to Website'),
    icon: 'link',
    onClick: () =>
      d.website
        ? openWebsite(d.website)
        : toast.error(__('Please set a website to visit')),
  })
  items.push({
    label: __('Attach a File'),
    icon: 'paperclip',
    onClick: () => {
      showFilesUploader.value = true
    },
  })
  items.push({
    label: __('Fetch tax / liens ($0.22)'),
    icon: 'dollar-sign',
    onClick: () =>
      d.property_address
        ? detailModals.value?.fetchTaxInfo()
        : toast.error(__('Set a property address to fetch tax info')),
  })
  items.push({
    label: __('Create Agreement'),
    icon: 'file-text',
    onClick: () =>
      d.property_address
        ? detailModals.value?.createAgreement()
        : toast.error(__('Set a property address to create an agreement')),
  })
  items.push({
    label: __('Create Underwriting Sheet'),
    icon: 'grid',
    onClick: () =>
      d.property_address
        ? detailModals.value?.createUnderwriting()
        : toast.error(
            __('Set a property address to create an underwriting sheet'),
          ),
  })
  items.push({
    label: __('Photos'),
    icon: 'image',
    onClick: () => {
      showPhotoGallery.value = true
    },
  })
  return items
})

function statusLabel(status) {
  if (isTranslatable('CRM Lead Status')) return __(status)
  return status
}

async function triggerStatusChange(value) {
  await triggerOnChange('status', value)
  setLostReason()
}

const showLostReasonModal = ref(false)

function setLostReason() {
  if (
    getLeadStatus(doc.value.status).type !== 'Lost' ||
    (doc.value.lost_reason && doc.value.lost_reason !== 'Other') ||
    (doc.value.lost_reason === 'Other' && doc.value.lost_notes)
  ) {
    document.save.submit()
    return
  }

  showLostReasonModal.value = true
}

function beforeStatusChange(data) {
  if (
    Object.hasOwn(data ?? {}, 'status') &&
    getLeadStatus(data.status).type == 'Lost'
  ) {
    setLostReason()
  } else {
    document.save.submit(null, {
      onSuccess: () => reloadAssignees(data),
    })
  }
}
function reloadAssignees(data) {
  if (Object.hasOwn(data ?? {}, 'lead_owner')) {
    assignees.reload()
  }
}
</script>
