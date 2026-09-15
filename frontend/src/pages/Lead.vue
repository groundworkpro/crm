<template>
  <LayoutHeader>
    <template #left-header>
      <Breadcrumbs :items="breadcrumbs">
        <template #prefix="{ item }">
          <Icon v-if="item.icon" :icon="item.icon" class="mr-2 h-4" />
        </template>
      </Breadcrumbs>
    </template>
    <template v-if="!errorTitle" #right-header>
      <CustomActions
        v-if="document._actions?.length"
        :actions="document._actions"
      />
      <CustomActions
        v-if="document.actions?.length"
        :actions="document.actions"
      />
      <AssignTo v-model="assignees.data" doctype="CRM Lead" :docname="leadId" />
      <Button v-if="workspace.isNext" variant="subtle" iconLeft="message-square" :label="__('Team chat')" @click="openLeadChat" />
      <Dropdown
        v-if="doc && document.statuses"
        :options="statuses"
        placement="right"
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
    </template>
  </LayoutHeader>
  <div v-if="doc.name" class="flex h-full overflow-hidden">
    <Tabs
      v-model="tabIndex"
      :tabs="tabs"
      class="flex flex-1 overflow-hidden flex-col [&_[role='tab']]:px-0 [&_[role='tab']]:shrink-0 [&_[role='tablist']]:px-5 [&_[role='tablist']::-webkit-scrollbar]:h-0 [&_[role='tablist']]:min-h-[45px] [&_[role='tablist']]:gap-7.5 [&_[role='tabpanel']:not([hidden])]:flex [&_[role='tabpanel']:not([hidden])]:grow"
    >
      <template #tab-panel>
        <Activities
          ref="activities"
          v-model:reload="reload"
          v-model:tabIndex="tabIndex"
          doctype="CRM Lead"
          :docname="leadId"
          :tabs="tabs"
          @beforeSave="beforeStatusChange"
          @afterSave="reloadResources"
        />
      </template>
    </Tabs>
    <Resizer class="flex flex-col justify-between border-l" side="right">
      <FileUploader
        :validateFile="validateIsImageFile"
        @success="(file) => updateField('image', file.file_url)"
      >
        <template #default="{ openFileSelector }">
          <div class="flex items-center justify-start gap-5 border-b p-5">
            <div class="group relative size-12">
              <Avatar
                size="3xl"
                class="size-12"
                :label="title"
                :image="doc.image"
              />
              <component
                :is="doc.image ? Dropdown : 'div'"
                v-bind="
                  doc.image
                    ? {
                        options: [
                          {
                            icon: 'upload',
                            label: doc.image
                              ? __('Change Image')
                              : __('Upload Image'),
                            onClick: openFileSelector,
                          },
                          {
                            icon: 'trash-2',
                            label: __('Remove Image'),
                            onClick: () => updateField('image', ''),
                          },
                        ],
                      }
                    : { onClick: openFileSelector }
                "
                class="!absolute bottom-0 left-0 right-0"
              >
                <div
                  class="z-1 absolute bottom-0.5 left-0 right-0.5 flex h-9 cursor-pointer items-center justify-center rounded-b-full bg-black bg-opacity-40 pt-3 opacity-0 duration-300 ease-in-out group-hover:opacity-100"
                  style="
                    -webkit-clip-path: inset(12px 0 0 0);
                    clip-path: inset(12px 0 0 0);
                  "
                >
                  <CameraIcon class="size-4 cursor-pointer text-white" />
                </div>
              </component>
            </div>
            <div class="flex flex-col gap-2.5 truncate">
              <!-- Inline name edit: hover shows a pencil, click swaps the name
                   for First/Last inputs (Tab moves between them; Enter or
                   clicking away saves; Esc cancels). -->
              <div
                v-if="editingName"
                ref="nameEditorRef"
                class="flex items-center gap-1.5"
                @focusout="onNameEditorFocusOut"
                @keydown.enter.prevent="saveName"
                @keydown.esc.prevent="cancelNameEdit"
              >
                <input
                  ref="firstNameRef"
                  v-model="nameDraft.first_name"
                  type="text"
                  :placeholder="__('First Name')"
                  class="w-1/2 min-w-0 rounded border border-outline-gray-2 bg-surface-white px-1.5 py-0.5 text-xl font-medium text-ink-gray-9 placeholder:font-normal focus:border-outline-gray-4 focus:outline-none focus:ring-0"
                />
                <input
                  v-model="nameDraft.last_name"
                  type="text"
                  :placeholder="__('Last Name')"
                  class="w-1/2 min-w-0 rounded border border-outline-gray-2 bg-surface-white px-1.5 py-0.5 text-xl font-medium text-ink-gray-9 placeholder:font-normal focus:border-outline-gray-4 focus:outline-none focus:ring-0"
                />
              </div>
              <Tooltip v-else :text="__('Click to edit name')">
                <div
                  class="group/name flex cursor-pointer items-center gap-1.5 truncate"
                  @click="startNameEdit"
                >
                  <div class="truncate text-2xl font-medium text-ink-gray-9">
                    {{ title }}
                  </div>
                  <EditIcon
                    class="size-4 shrink-0 text-ink-gray-5 opacity-0 duration-200 group-hover/name:opacity-100"
                  />
                </div>
              </Tooltip>
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
              <!-- Who already buys here: same chips as the Kanban. The parent
                   column is `truncate` (whitespace-nowrap), so wrap them or a
                   second badge clips. -->
              <DispoBuyerBadges
                fetch
                class="whitespace-normal"
                :city="doc.property_city"
                :state="doc.property_state"
                :county="doc.property_county"
              />
              <!-- Acq price: just a $ icon + amount (no label), digits only,
                   live thousand separators; Enter/blur saves. -->
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
              <!-- DD expiration: calendar icon + date (no label), mirrors the
                   acq-price row. Shows "(N days left)" beside the date (red
                   past / amber today); click opens the native date picker,
                   hover ✕ clears. -->
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
              <!-- Showing access: key icon + free-text access instructions
                   ("vacant", "lockbox 4127", …). Wraps to multiple lines;
                   Enter/blur saves, Esc reverts. -->
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
              <div class="flex gap-1.5">
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

                <!-- no mobile guard: the modal's To field is editable, so a
                     lead without a number can still be texted by typing one -->
                <Button
                  v-if="smsEnabled"
                  :tooltip="__('Send a Text')"
                  :icon="CommentIcon"
                  @click="activities?.sendText()"
                />

                <!-- One-click: comps + Zillow + Google Maps for this property.
                     Reuses the same Zillow slug / Maps query as the standalone
                     actions so the opened pages match what More → View on
                     Zillow and the address-row Maps link already produce. -->
                <Button
                  :tooltip="__('Open Research tabs')"
                  :icon="ExternalLinkIcon"
                  @click="openResearchTabs"
                />

                <!-- Comps on the header row, not in More: it is opened on
                     nearly every lead (Lance, 2026-09-10). -->
                <Button
                  :tooltip="__('View comps')"
                  icon="map-pin"
                  @click="
                    () =>
                      doc.property_address
                        ? openComps()
                        : toast.error(__('Set a property address to see comps'))
                  "
                />

                <!-- "Got a live one" -- pings the closer in Mattermost with a
                     link to this lead's comps. On the header row, not in More,
                     because the moment it is needed is mid-call. -->
                <Button
                  :tooltip="__('Got a live one? Alert the closer')"
                  icon="zap"
                  @click="showLiveOne = true"
                />

                <!-- Secondary actions live in a single menu so the row stays
                     uncluttered: email / website / attach / tax / agreement. -->
                <Dropdown :options="moreActions" placement="bottom-start">
                  <Button
                    :tooltip="__('More actions')"
                    icon="more-horizontal"
                  />
                </Dropdown>

                <Button
                  v-if="canDelete"
                  :tooltip="__('Delete')"
                  variant="subtle"
                  theme="red"
                  icon="trash-2"
                  @click="deleteLead"
                />
              </div>
              <ErrorMessage :message="__(error)" />
            </div>
          </div>
        </template>
      </FileUploader>
      <!-- Single scroll container for the sidebar body: our custom cards
           (First-Call Read / Tax / Agreements / Underwriting) used to sit
           between the header and the editable fields as non-scrolling
           siblings, pushing the Person/Property/Details fields below the fold
           with no way to scroll to them. Wrapping the cards + fields in one
           overflow-y-auto/min-h-0 region lets the whole body scroll while the
           header above and the lead-id footer below stay fixed. -->
      <div class="flex flex-1 flex-col overflow-y-auto min-h-0">
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
      <TaxInfoCard :lead="leadId" @fetch="activities?.fetchTaxInfo()" />
      <AgreementsCard :lead="leadId" @create="activities?.createAgreement()" />
      <UnderwritingCard
        :lead="leadId"
        @create="activities?.createUnderwriting()"
      />
      <InvestorLiftCard :lead="leadId" :address="doc.property_address" />
      <div v-if="sections.data" class="flex flex-col">
        <SidePanelLayout
          :sections="sidePanelWithoutPhones"
          doctype="CRM Lead"
          :docname="leadId"
          @reload="sections.reload"
          @beforeFieldChange="beforeStatusChange"
          @afterFieldChange="reloadResources"
        />
      </div>
      </div>
      <div
        class="flex cursor-copy items-center border-t px-5 py-2.5 text-sm text-ink-gray-5"
        @click="copyToClipboard(leadId)"
      >
        {{ __(leadId) }}
      </div>
    </Resizer>
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
    @after="
      () => {
        activities?.all_activities?.reload()
        changeTabTo('attachments')
      }
    "
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
import Resizer from '@/components/Resizer.vue'
import ActivityIcon from '@/components/Icons/ActivityIcon.vue'
import EmailIcon from '@/components/Icons/EmailIcon.vue'
import Email2Icon from '@/components/Icons/Email2Icon.vue'
import CommentIcon from '@/components/Icons/CommentIcon.vue'
import ExternalLinkIcon from '@/components/Icons/ExternalLinkIcon.vue'
import DetailsIcon from '@/components/Icons/DetailsIcon.vue'
import PhoneIcon from '@/components/Icons/PhoneIcon.vue'
import TaskIcon from '@/components/Icons/TaskIcon.vue'
import NoteIcon from '@/components/Icons/NoteIcon.vue'
import WhatsAppIcon from '@/components/Icons/WhatsAppIcon.vue'
import IndicatorIcon from '@/components/Icons/IndicatorIcon.vue'
import CameraIcon from '@/components/Icons/CameraIcon.vue'
import LinkIcon from '@/components/Icons/LinkIcon.vue'
import AttachmentIcon from '@/components/Icons/AttachmentIcon.vue'
import AddressIcon from '@/components/Icons/AddressIcon.vue'
import EditIcon from '@/components/Icons/EditIcon.vue'
import LostReasonModal from '@/components/Modals/LostReasonModal.vue'
import LayoutHeader from '@/components/LayoutHeader.vue'
import Activities from '@/components/Activities/Activities.vue'
import AssignTo from '@/components/AssignTo.vue'
import FilesUploader from '@/components/FilesUploader/FilesUploader.vue'
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
import MoneyIcon from '@/components/Icons/MoneyIcon.vue'
import CalendarIcon from '@/components/Icons/CalendarIcon.vue'
import {
  openWebsite,
  setupCustomizations,
  copyToClipboard,
  validateIsImageFile,
  isTranslatable,
  ddExpiration,
  parseColor,
} from '@/utils'
import { getView } from '@/utils/view'
import { formatPhone } from '@/utils/phoneFormat'
import { listLeadPhones, primaryLeadPhone } from '@/utils/leadPhones'
import { mapsUrl, zillowUrl } from '@/utils/propertyLinks'
import { clickToCall } from '@/composables/clickToCall'
import { getSettings } from '@/stores/settings'
import { globalStore } from '@/stores/global'
import { workspaceStore } from '@/stores/workspace'
import { statusesStore } from '@/stores/statuses'
import { getMeta } from '@/stores/meta'
import { useDocument } from '@/data/document'
import { whatsappEnabled, smsEnabled, callEnabled } from '@/composables/settings'
import {
  createResource,
  FileUploader,
  Dropdown,
  Tooltip,
  Avatar,
  Tabs,
  Breadcrumbs,
  call,
  usePageMeta,
  toast,
  FeatherIcon,
} from 'frappe-ui'
import {
  ref,
  reactive,
  computed,
  watch,
  nextTick,
  onMounted,
  onBeforeUnmount,
} from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { useActiveTabManager } from '@/composables/useActiveTabManager'

const { brand } = getSettings()
const { $dialog, $socket, makeCall } = globalStore()
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

const reload = ref(false)
const activities = ref(null)
const errorTitle = ref('')
const errorMessage = ref('')
const showDeleteLinkedDocModal = ref(false)
const showFilesUploader = ref(false)
const showLiveOne = ref(false)

const {
  triggerOnChange,
  triggerOnRender,
  assignees,
  permissions,
  document,
  scripts,
  error,
} = useDocument('CRM Lead', props.leadId)

const canDelete = computed(() => permissions.data?.permissions?.delete || false)

const doc = computed(() => document.doc || {})

// Open comps + 1 Zillow tab + 1 Google Maps tab for this lead's property. All three
// window.opens stay in the same click gesture so the popup blocker allows them.
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

// Secondary header actions, collapsed into the "More" menu next to the name.
const moreActions = computed(() => {
  const d = doc.value
  const items = []
  // Parked bulk-import lead: promotion to the main board is manual and
  // per-lead, so the caller decides when this one is actually ready.
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
  if (callEnabled.value) {
    items.push({
      label: __('Make a Call'),
      icon: 'phone',
      onClick: () =>
        primaryLeadPhone(d)
          ? makeCall(primaryLeadPhone(d))
          : toast.error(__('Please set a mobile number to make calls')),
    })
  }
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
    label: __('View on Zillow'),
    icon: 'home',
    onClick: () =>
      d.property_address
        ? window.open(zillowUrl(d.property_address), '_blank', 'noopener')
        : toast.error(__('Set a property address to view on Zillow')),
  })
  items.push({
    label: __('Fetch tax / liens ($0.22)'),
    icon: 'dollar-sign',
    onClick: () =>
      d.property_address
        ? activities.value?.fetchTaxInfo()
        : toast.error(__('Set a property address to fetch tax info')),
  })
  items.push({
    label: __('Create Agreement'),
    icon: 'file-text',
    onClick: () =>
      d.property_address
        ? activities.value?.createAgreement()
        : toast.error(__('Set a property address to create an agreement')),
  })
  items.push({
    label: __('Create Underwriting Sheet'),
    icon: 'grid',
    onClick: () =>
      d.property_address
        ? activities.value?.createUnderwriting()
        : toast.error(__('Set a property address to create an underwriting sheet')),
  })
  items.push({
    label: __('Photos'),
    icon: 'image',
    onClick: () =>
      d.property_address
        ? (showPhotoGallery.value = true)
        : toast.error(__('Set a property address to add photos')),
  })
  return items
})

// Property photos live in the shared Wholesaling Drive, not in Frappe — the
// gallery modal is mounted here rather than threaded through AllModals because
// nothing else needs to open it.
const showPhotoGallery = ref(false)
// Comps opens in its own TAB rather than a modal: a rep underwrites from it,
// cross-referencing the lead, so it needs to sit beside the lead rather than on
// top of it. noopener because this is a user-initiated window.open.
function openComps() {
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

onMounted(async () => {
  if (document.doc) await triggerOnRender()
})

// A tax-info pull writes owner/APN/tax fields back onto the lead — refresh the
// doc + sidebar fields when one lands for this lead (site-wide realtime event).
function onTaxPull(data) {
  if (
    data.reference_doctype === 'CRM Lead' &&
    data.reference_docname === props.leadId
  ) {
    document.reload()
    sections.reload()
  }
}
onMounted(() => $socket.on('crm_tax_pull', onTaxPull))
onBeforeUnmount(() => $socket.off('crm_tax_pull', onTaxPull))

watch(error, (err) => {
  if (err) {
    errorTitle.value = __(
      err.exc_type == 'DoesNotExistError'
        ? 'Document not found'
        : 'Error occurred',
    )
    errorMessage.value = __(err.messages?.[0] || 'An error occurred')
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

const statuses = computed(() => {
  let customStatuses = document.statuses?.length
    ? document.statuses
    : document._statuses || []
  return statusOptions('lead', customStatuses, triggerStatusChange)
})

usePageMeta(() => {
  return { title: title.value, icon: brand.favicon }
})

const tabs = computed(() => {
  let tabOptions = [
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
      name: 'SMS',
      label: __('Text Messages'),
      icon: CommentIcon,
      condition: () => smsEnabled.value,
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

const { tabIndex, changeTabTo } = useActiveTabManager(tabs, 'lastLeadTab')

const sections = createResource({
  url: 'crm.fcrm.doctype.crm_fields_layout.crm_fields_layout.get_sidepanel_sections',
  cache: ['sidePanelSections', 'CRM Lead'],
  params: { doctype: 'CRM Lead' },
  auto: true,
})

// Acq price inline editor in the header (under the name, $ icon only):
// digits-only with live thousand separators; saves on Enter/blur.
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
  // the reformat can leave the ref unchanged (e.g. typing a letter), so sync
  // the DOM input directly too
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

// DD expiration inline editor in the header (under acq price, calendar icon
// only): the visible text is the formatted date + "(N days left)"; a hidden
// native date input supplies the picker (showPicker on click) and fires the
// save on change.
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

// Showing access inline editor in the header (under DD expiration, key icon
// only): a borderless auto-growing textarea so instructions like
// "lockbox 4127, dogs in yard" wrap instead of truncating.
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

// The height is a function of the WIDTH: measured while the sidebar is
// collapsed (~0px wide) the placeholder wraps one character per line and
// scrollHeight comes out at hundreds of px, and that stale value used to
// survive the panel being expanded again -- a huge blank gap between the key
// row and the action buttons. Re-measure whenever the textarea's box changes.
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

async function triggerStatusChange(value) {
  await triggerOnChange('status', value)
  setLostReason()
}

// Inline editing of the lead's name from the sidebar header.
const editingName = ref(false)
const nameEditorRef = ref(null)
const firstNameRef = ref(null)
const nameDraft = reactive({ first_name: '', last_name: '' })

function startNameEdit() {
  nameDraft.first_name = doc.value.first_name || ''
  nameDraft.last_name = doc.value.last_name || ''
  editingName.value = true
  nextTick(() => firstNameRef.value?.focus())
}

function cancelNameEdit() {
  editingName.value = false
}

function onNameEditorFocusOut(e) {
  // Tabbing from First to Last also fires focusout — only save when focus
  // actually leaves the editor.
  if (nameEditorRef.value?.contains(e.relatedTarget)) return
  saveName()
}

function saveName() {
  if (!editingName.value) return
  editingName.value = false
  const first = nameDraft.first_name.trim()
  const last = nameDraft.last_name.trim()
  if (!first) {
    toast.error(__('First name is required'))
    return
  }
  if (
    first == (doc.value.first_name || '') &&
    last == (doc.value.last_name || '')
  ) {
    return
  }
  const old = {
    first_name: doc.value.first_name,
    last_name: doc.value.last_name,
    lead_name: doc.value.lead_name,
  }
  doc.value.first_name = first
  doc.value.last_name = last
  // validate() rebuilds lead_name server-side; mirror it optimistically so
  // the header doesn't show the stale name while the save round-trips.
  doc.value.lead_name = [first, doc.value.middle_name, last]
    .filter(Boolean)
    .join(' ')
  document.save.submit(null, {
    onSuccess: () => (reload.value = true),
    onError: (err) => {
      Object.assign(doc.value, old)
      toast.error(err.messages?.[0] || __('Error updating name'))
    },
  })
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

function openEmailBox() {
  let currentTab = tabs.value[tabIndex.value]
  if (!['Emails', 'Comments', 'Activities'].includes(currentTab.name)) {
    activities.value.changeTabTo('emails')
  }
  nextTick(() => (activities.value.emailBox.show = true))
}

function statusLabel(status) {
  if (isTranslatable('CRM Lead Status')) return __(status)
  return status
}

const showLostReasonModal = ref(false)

function setLostReason() {
  if (
    getLeadStatus(document.doc.status).type !== 'Lost' ||
    (document.doc.lost_reason && document.doc.lost_reason !== 'Other') ||
    (document.doc.lost_reason === 'Other' && document.doc.lost_notes)
  ) {
    document.save.submit(null, {
      onSuccess: () => sections.reload(),
    })
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
      onSuccess: () => reloadResources(data),
    })
  }
}

function reloadResources(data) {
  if (Object.hasOwn(data ?? {}, 'lead_owner')) {
    assignees.reload()
  }
  if (
    Object.hasOwn(data ?? {}, 'status') &&
    getLeadStatus(data.status).type != 'Lost'
  ) {
    sections.reload()
  }
}
</script>
