<template>
  <LayoutHeader>
    <template #left-header>
      <ViewBreadcrumbs v-model="viewControls" routeName="Leads" />
    </template>
    <template #right-header>
      <CustomActions
        v-if="leadsListView?.customListActions"
        :actions="leadsListView.customListActions"
      />
      <Button
        variant="solid"
        :label="__('Create')"
        iconLeft="plus"
        @click="showLeadModal = true"
      />
      <Dropdown :options="leadListActions" placement="right">
        <Button variant="ghost" :tooltip="__('More')">
          <template #icon>
            <LucideMoreHorizontal class="size-4" />
          </template>
        </Button>
      </Dropdown>
    </template>
  </LayoutHeader>
  <div
    v-if="drilldown.active"
    class="mx-3 mt-2 flex items-center gap-2 rounded bg-surface-gray-2 px-3 py-2 text-sm"
  >
    <LucideFilter class="size-4 text-ink-gray-6 shrink-0" />
    <span class="font-medium text-ink-gray-8">{{ drilldown.label }}</span>
    <span v-if="drilldown.sub" class="text-ink-gray-5"
      >· {{ drilldown.sub }}</span
    >
    <span class="text-ink-gray-5">
      · {{ drilldown.names.length }}
      {{ drilldown.names.length === 1 ? __('lead') : __('leads') }}
    </span>
    <span v-if="drilldown.truncated" class="text-ink-red-3">
      ({{ __('capped') }})
    </span>
    <Button
      class="ml-auto"
      variant="ghost"
      :label="__('Clear')"
      iconLeft="x"
      @click="clearDrill"
    />
  </div>
  <div
    v-if="importList.parked > 0"
    class="mx-3 mt-2 flex items-center gap-2 rounded bg-surface-amber-1 px-3 py-2 text-sm"
  >
    <LucideEyeOff class="size-4 shrink-0 text-ink-amber-3" />
    <span class="text-ink-gray-8">
      <span class="font-medium">{{ importList.parked }}</span>
      {{
        importList.parked === 1
          ? __('lead in this list is parked off the main board')
          : __('leads in this list are parked off the main board')
      }}
    </span>
    <span class="text-ink-gray-5">
      · {{ __('work them here; move them over when they’re ready') }}
    </span>
    <Button
      class="ml-auto"
      variant="subtle"
      :label="__('Add all to main board')"
      :loading="promoting"
      @click="promoteList"
    />
  </div>
  <!--
    A filter you forgot is the most expensive kind. Typing in the "Full Name" box
    writes a `lead_name LIKE %…%` into your saved view and leaves it there, and on
    2026-08-14 both setters were working a board showing ONE of 353 leads —
    German's since the previous morning. Two signals already existed (the text
    sitting in the box, the count badge on the Filter button) and both were
    missed, because both say what the STATE is and neither says what it COSTS.
    This one says the consequence out loud, names the filter, and clears it in
    one click.

    Personal standard views only: a named/public view like the ISTL LeadPack
    board is filtered on purpose and says so in the breadcrumb.
  -->
  <div
    v-if="activeFilters.length"
    class="mx-3 mt-2 flex flex-wrap items-center gap-x-2 gap-y-1 rounded bg-surface-amber-1 px-3 py-2 text-sm"
  >
    <LucideFilter class="size-4 shrink-0 text-ink-amber-3" />
    <span class="text-ink-gray-8">
      {{ __('This board is filtered') }} —
      <span class="font-medium">
        {{
          leads.data?.total_count === 1
            ? __('showing 1 lead')
            : __('showing {0} leads', [leads.data?.total_count ?? 0])
        }}
      </span>
    </span>
    <span v-for="f in activeFilters" :key="f.key" class="text-ink-gray-6">
      · {{ f.label }} {{ f.op }}
      <span class="font-medium">{{ f.value }}</span>
    </span>
    <Button
      class="ml-auto"
      variant="subtle"
      :label="__('Clear filters')"
      iconLeft="x"
      @click="viewControls.clearFilters()"
    />
  </div>
  <ViewControls
    ref="viewControls"
    v-model="leads"
    v-model:loadMore="loadMore"
    v-model:resizeColumn="triggerResize"
    v-model:updatedPageCount="updatedPageCount"
    doctype="CRM Lead"
    :filters="listFilters"
    :options="{
      allowedViews: ['list', 'group_by', 'kanban'],
    }"
  >
    <template #actions>
      <Dropdown
        v-if="route.params.viewType === 'kanban'"
        :options="taskSortOptions"
      >
        <Button
          :label="taskSortLabel"
          variant="subtle"
          :theme="taskSortDir ? 'blue' : 'gray'"
        >
          <template #prefix><LucideArrowDownUp class="size-4" /></template>
        </Button>
      </Dropdown>
      <Dropdown :options="taskDueOptions">
        <Button
          :label="taskDueLabel"
          variant="subtle"
          :theme="taskDueScope ? 'blue' : 'gray'"
        >
          <template #prefix><LucideCalendarClock class="size-4" /></template>
        </Button>
      </Dropdown>
    </template>
  </ViewControls>
  <KanbanView
    v-if="route.params.viewType == 'kanban'"
    v-model="leads"
    :options="{
      getRoute: (row) => ({
        name: 'Lead',
        params: { leadId: row.name },
        query: { view: route.query.view, viewType: route.params.viewType },
      }),
      onClick: (row, e) => onCardClick(row, e),
      onNewClick: (column) => onNewClick(column),
      cardColor: (row) => cardTint(row),
    }"
    @update="(data) => viewControls.updateKanbanSettings(data)"
    @loadMore="(columnName) => viewControls.loadMoreKanban(columnName)"
  >
    <template #title="{ titleField, itemName }">
      <div class="flex flex-col gap-1.5">
      <div class="flex items-center gap-2">
        <div v-if="titleField === 'status'">
          <IndicatorIcon :class="getRow(itemName, titleField).color" />
        </div>
        <div
          v-else-if="
            titleField === 'organization' && getRow(itemName, titleField).label
          "
        >
          <Avatar
            class="flex items-center"
            :image="getRow(itemName, titleField).logo"
            :label="getRow(itemName, titleField).label"
            size="sm"
          />
        </div>
        <div
          v-else-if="
            titleField === 'lead_name' && getRow(itemName, titleField).label
          "
        >
          <Avatar
            class="flex items-center"
            :image="getRow(itemName, titleField).image"
            :label="getRow(itemName, titleField).image_label"
            size="sm"
          />
        </div>
        <div
          v-else-if="
            titleField === 'lead_owner' &&
            getRow(itemName, titleField).full_name
          "
        >
          <Avatar
            class="flex items-center"
            :image="getRow(itemName, titleField).user_image"
            :label="getRow(itemName, titleField).full_name"
            size="sm"
          />
        </div>
        <div v-else-if="titleField === 'mobile_no'">
          <PhoneIcon class="h-4 w-4" />
        </div>
        <div
          v-if="
            [
              'modified',
              'creation',
              'first_response_time',
              'first_responded_on',
              'response_by',
            ].includes(titleField)
          "
          class="truncate text-base"
        >
          <Tooltip :text="getRow(itemName, titleField).label">
            <div>{{ getRow(itemName, titleField).timeAgo }}</div>
          </Tooltip>
        </div>
        <div v-else-if="titleField === 'sla_status'" class="truncate text-base">
          <Badge
            v-if="getRow(itemName, titleField).value"
            :variant="'subtle'"
            :theme="getRow(itemName, titleField).color"
            size="md"
            :label="getRow(itemName, titleField).value"
          />
        </div>
        <div
          v-else-if="getRow(itemName, titleField).label"
          class="min-w-0 truncate text-base"
        >
          {{ getRow(itemName, titleField).label }}
        </div>
        <div v-else class="text-ink-gray-4">{{ __('No Title') }}</div>
        <!-- owner initials: who is working this lead, readable at a glance
             without spending a card row on a full-name avatar field -->
        <span
          v-if="ownerInitials(itemName)"
          class="ml-auto flex size-5 shrink-0 items-center justify-center rounded-full bg-surface-gray-3 text-[10px] font-semibold uppercase leading-none tracking-wide text-ink-gray-7"
          :title="getRow(itemName, 'lead_owner').full_name"
        >
          {{ ownerInitials(itemName) }}
        </span>
      </div>
      <!-- Photos & Lockbox cards only (server sends nothing elsewhere): who on
           our contractor list covers this property's metro. Not a card field,
           so it shows whatever card fields each person has chosen. -->
      <div v-if="getRawValue(itemName, '_contractors')" class="flex">
        <ContractorBadge
          :value="getRawValue(itemName, '_contractors')"
          :lead="itemName"
        />
      </div>
      </div>
    </template>
    <template #fields="{ fieldName, fieldLabel, showBlank, itemName }">
      <KanbanCardField
        v-if="showsField(itemName, fieldName, showBlank)"
        doctype="CRM Lead"
        :name="itemName"
        :fieldName="fieldName"
        :rawValue="getRawValue(itemName, fieldName)"
        :copyText="String(getRow(itemName, fieldName).label ?? '')"
        @updated="reloadKanban"
      >
        <span v-if="fieldLabel" class="shrink-0 text-ink-gray-5">
          {{ fieldLabel }}
        </span>
        <span
          v-if="showBlank && !getRow(itemName, fieldName).label"
          class="truncate text-base text-ink-gray-4"
        >
          &mdash;
        </span>
        <div v-if="fieldName === 'status'">
          <IndicatorIcon :class="getRow(itemName, fieldName).color" />
        </div>
        <div
          v-else-if="
            fieldName === 'organization' && getRow(itemName, fieldName).label
          "
        >
          <Avatar
            class="flex items-center"
            :image="getRow(itemName, fieldName).logo"
            :label="getRow(itemName, fieldName).label"
            size="xs"
          />
        </div>
        <div v-else-if="fieldName === 'lead_name'">
          <Avatar
            v-if="getRow(itemName, fieldName).label"
            class="flex items-center"
            :image="getRow(itemName, fieldName).image"
            :label="getRow(itemName, fieldName).image_label"
            size="xs"
          />
        </div>
        <div v-else-if="fieldName === 'lead_owner'">
          <Avatar
            v-if="getRow(itemName, fieldName).full_name"
            class="flex items-center"
            :image="getRow(itemName, fieldName).user_image"
            :label="getRow(itemName, fieldName).full_name"
            size="xs"
          />
        </div>
        <div
          v-if="
            [
              'modified',
              'creation',
              'first_response_time',
              'first_responded_on',
              'response_by',
              '_last_comm',
            ].includes(fieldName)
          "
          class="truncate text-base"
        >
          <Tooltip :text="getRow(itemName, fieldName).label">
            <div>{{ getRow(itemName, fieldName).timeAgo }}</div>
          </Tooltip>
        </div>
        <div v-else-if="fieldName === 'sla_status'" class="truncate text-base">
          <Badge
            v-if="getRow(itemName, fieldName).value"
            :variant="'subtle'"
            :theme="getRow(itemName, fieldName).color"
            size="md"
            :label="getRow(itemName, fieldName).value"
          />
        </div>
        <div
          v-else-if="fieldName === '_next_task_due'"
          class="truncate text-base"
        >
          <Tooltip
            v-if="getRow(itemName, fieldName).value"
            :text="getRow(itemName, fieldName).label"
          >
            <div
              :class="
                getRow(itemName, fieldName).color
                  ? parseColor(getRow(itemName, fieldName).color)
                  : ''
              "
            >
              {{ getRow(itemName, fieldName).value }}
            </div>
          </Tooltip>
        </div>
        <div
          v-else-if="fieldName === 'dd_expiration_date'"
          class="truncate text-base"
        >
          <div
            v-if="getRow(itemName, fieldName).label"
            :class="
              getRow(itemName, fieldName).color
                ? parseColor(getRow(itemName, fieldName).color)
                : ''
            "
          >
            {{ getRow(itemName, fieldName).label }}
          </div>
        </div>
        <!--
          Who already buys here. Renders nothing at all unless one of the two
          national buyers covers this lead's area, which is ~58% of the board,
          so it costs no height on the rest.
        -->
        <div
          v-else-if="fieldName === '_dispo_buyers'"
          class="truncate text-base"
        >
          <DispoBuyerBadges :value="getRawValue(itemName, fieldName)" />
        </div>
        <div
          v-else-if="fieldName === '_first_call'"
          class="truncate text-base"
        >
          <Badge
            v-if="getRow(itemName, fieldName).label"
            variant="subtle"
            :theme="getRow(itemName, fieldName).color"
            size="md"
            :label="getRow(itemName, fieldName).label"
          />
        </div>
        <div
          v-else-if="fieldName === '_assign'"
          class="flex items-center truncate"
        >
          <MultipleAvatar
            :avatars="getRow(itemName, fieldName).label"
            size="xs"
          />
        </div>
        <a
          v-else-if="['mobile_no', 'phone'].includes(fieldName)"
          href="#"
          class="truncate text-base text-ink-gray-9 hover:text-ink-blue-link hover:underline"
          @click.prevent.stop="clickToCall(getRow(itemName, fieldName).label, { lead: itemName })"
        >
          {{ getRow(itemName, fieldName).label }}
        </a>
        <div v-else class="truncate text-base">
          {{ getRow(itemName, fieldName).label }}
        </div>
      </KanbanCardField>
    </template>
    <template #actions="{ itemName }">
      <!--
        Everything in this row is per-card, so component choice here is
        multiplied by the size of the board. The three counters used a <Tooltip>
        each purely to show a fixed string; a native `title` says the same thing
        for free (the same trade the Today card documents). The actions menu is
        a Dropdown — trigger + portal + content + context per card — and is
        pointless until aimed at, so it is mounted on approach.
      -->
      <!--
        The three counters are 229-258px wide inside a 238px card footer, so
        while the "+" was in flow `justify-between` had nowhere to put it and
        pushed it PAST the card's right edge, where the neighbouring column
        covers all but ~4 clickable pixels.

        Clipping the counters to make room hid the email counter on every card,
        which is a worse trade. Instead the menu leaves the flow entirely and
        behaves like every other control on a card: hidden at rest, revealed on
        hover over its bottom-right corner. Nothing is lost at rest, and the
        button is fully clickable when you actually reach for it — the same
        pattern as the copy/pencil affordances on the field rows above.
      -->
      <div class="relative flex items-center justify-between gap-2">
        <!--
          Tighter gaps than the rest of the card on purpose. The three counters
          are the widest thing on a card and the busiest ones (e.g. "79↑ 205↓")
          ran 258px inside a 238px footer, so the column's overflow clipped the
          trailing arrow of the email counter. Shaving the gaps recovers ~20px,
          which is enough to fit — and unlike letting the row shrink, it hides
          nothing.
        -->
        <div class="text-ink-gray-7 flex items-center gap-1">
          <div class="flex items-center gap-0.5" :title="__('Calls out / in')">
            <PhoneIcon class="h-4 w-4" />
            <span>{{ getRow(itemName, '_call_out_count').label ?? 0 }}&#8593;</span>
            <span>{{ getRow(itemName, '_call_in_count').label ?? 0 }}&#8595;</span>
          </div>
          <span class="text-3xl leading-[0]"> &middot; </span>
          <div class="flex items-center gap-0.5" :title="__('Texts out / in')">
            <CommentIcon class="h-4 w-4" />
            <span>{{ getRow(itemName, '_text_out_count').label ?? 0 }}&#8593;</span>
            <span>{{ getRow(itemName, '_text_in_count').label ?? 0 }}&#8595;</span>
          </div>
          <template v-if="hasEmailActivity(itemName)">
            <span class="text-3xl leading-[0]"> &middot; </span>
            <div
              class="flex items-center gap-0.5"
              :title="__('Emails out / in')"
            >
              <EmailAtIcon class="h-4 w-4" />
              <span>{{ getRow(itemName, '_email_out_count').label ?? 0 }}&#8593;</span>
              <span>{{ getRow(itemName, '_email_in_count').label ?? 0 }}&#8595;</span>
            </div>
          </template>
        </div>
        <!--
          `has-[[data-state=open]]:flex` keeps this visible while its own menu is
          open, and it is load-bearing, not belt-and-braces. reka-ui's dropdown is
          modal: opening it puts `pointer-events: none` on <body>, so the card
          instantly loses :hover, a hover-only trigger collapses to display:none,
          and Popper then anchors the open menu to a 0x0 box at the origin — the
          menu jumps to the top-left corner of the window. reka sets
          `data-state="open"` on the trigger, so this pins the container open for
          exactly as long as the menu is. KanbanCardFieldAction solves the same
          problem with its `editorOpen` ref; this is the CSS equivalent.
        -->
        <!--
          No background. It carried `bg-surface-white` to mask the counters it
          used to sit on top of, back when the email counter made this row
          229-258px wide inside a 238px footer. gw328 stopped rendering that
          counter (this site has never had a single Communication row), and the
          row is now short enough that the chip clears it outright: measured
          over all 113 cards on the board, the SMALLEST gap between the end of
          the counters and the start of this chip is 46px, on the busiest card
          there is ("21^ 5v . 79^ 205v").

          So the mask no longer hides anything -- it only painted a flat
          untinted rectangle onto every due/new tinted card, which is half of
          what "the + isn't centered properly when the card is coloured" was
          reporting. Dropping it lets the card's own tint show through.
        -->
        <div
          class="absolute -right-1 top-1/2 hidden -translate-y-1/2 items-center rounded pl-1 group-hover/card:flex has-[[data-state=open]]:flex"
        >
          <!--
            `variant="ghost"` hovers to an OPAQUE surface-gray-3, which is only
            neutral on a white card; on a tinted one it paints back the same
            flat off-colour square the wrapper above just stopped painting.
            Darkening the backdrop instead needs no colour token, so it is
            correct on plain, red and amber cards and in both themes. The `!`
            is what beats the variant's own hover background; without killing
            that the filter would just tint an opaque grey square.
          -->
          <HoverMount @click.stop.prevent>
            <Dropdown
              class="flex items-center gap-2"
              :options="actions(itemName)"
              variant="ghost"
            >
              <Button
                icon="plus"
                variant="ghost"
                class="hover:!bg-transparent hover:backdrop-brightness-90"
              />
            </Dropdown>
            <template #placeholder>
              <Button
                icon="plus"
                variant="ghost"
                class="hover:!bg-transparent hover:backdrop-brightness-90"
              />
            </template>
          </HoverMount>
        </div>
      </div>
    </template>
  </KanbanView>
  <LeadsListView
    v-else-if="leads.data && rows.length"
    ref="leadsListView"
    v-model="leads.data.page_length_count"
    v-model:list="leads"
    :rows="rows"
    :columns="columns"
    :options="{
      showTooltip: false,
      resizeColumn: true,
      rowCount: leads.data.row_count,
      totalCount: leads.data.total_count,
    }"
    @loadMore="() => loadMore++"
    @columnWidthUpdated="() => triggerResize++"
    @updatePageCount="(count) => (updatedPageCount = count)"
    @applyFilter="(data) => viewControls.applyFilter(data)"
    @applyLikeFilter="(data) => viewControls.applyLikeFilter(data)"
    @likeDoc="(data) => viewControls.likeDoc(data)"
    @selectionsChanged="
      (selections) => viewControls.updateSelections(selections)
    "
  />
  <EmptyState
    v-else-if="leads.data && !rows.length"
    name="Leads"
    :icon="LeadsIcon"
  />
  <ImportLeadsModal
    v-if="showImportModal"
    v-model="showImportModal"
    @imported="onImported"
  />
  <LeadModal
    v-if="showLeadModal"
    v-model="showLeadModal"
    :defaults="defaults"
  />
  <NoteModal
    v-if="showNoteModal"
    v-model="showNoteModal"
    :note="note"
    doctype="CRM Lead"
    :doc="docname"
  />
  <TaskModal
    v-if="showTaskModal"
    v-model="showTaskModal"
    :task="task"
    doctype="CRM Lead"
    :doc="docname"
  />
  <!--
    Kept mounted (v-show semantics via the modal's own `show`) rather than
    v-if'd away, so reopening the same lead does not re-mount Activities and
    refetch the whole timeline. The modal itself gates its heavy children on
    `show`, so nothing renders while it is closed.
  -->
  <LeadQuickViewModal v-model="showQuickView" :lead-id="quickViewLead" />
  <!--
    v-if, so answering the prompt UNMOUNTS it rather than leaving it to play an
    exit transition. Two reka-ui modal dialogs overlapping wedge each other: the
    outgoing one stays on screen at data-state="closed" and the incoming one
    sticks half-faded at opacity .5, both permanently. Measured, not guessed.
  -->
  <LeadOpenModeModal
    v-if="showOpenModePrompt"
    v-model="showOpenModePrompt"
    @choose="onOpenModeChosen"
  />
</template>

<script setup>
import ViewBreadcrumbs from '@/components/ViewBreadcrumbs.vue'
import MultipleAvatar from '@/components/MultipleAvatar.vue'
import CustomActions from '@/components/CustomActions.vue'
import EmailAtIcon from '@/components/Icons/EmailAtIcon.vue'
import PhoneIcon from '@/components/Icons/PhoneIcon.vue'
import NoteIcon from '@/components/Icons/NoteIcon.vue'
import TaskIcon from '@/components/Icons/TaskIcon.vue'
import CommentIcon from '@/components/Icons/CommentIcon.vue'
import IndicatorIcon from '@/components/Icons/IndicatorIcon.vue'
import LeadsIcon from '@/components/Icons/LeadsIcon.vue'
import LayoutHeader from '@/components/LayoutHeader.vue'
import LeadsListView from '@/components/ListViews/LeadsListView.vue'
import EmptyState from '@/components/ListViews/EmptyState.vue'
import KanbanView from '@/components/Kanban/KanbanView.vue'
import KanbanCardField from '@/components/Kanban/KanbanCardField.vue'
import HoverMount from '@/components/Kanban/HoverMount.vue'
import LeadModal from '@/components/Modals/LeadModal.vue'
import DispoBuyerBadges from '@/components/DispoBuyerBadges.vue'
import ContractorBadge from '@/components/ContractorBadge.vue'
import LeadQuickViewModal from '@/components/Modals/LeadQuickViewModal.vue'
import LeadOpenModeModal from '@/components/Modals/LeadOpenModeModal.vue'
import {
  LEAD_OPEN_MODAL,
  LEAD_OPEN_PAGE,
  loadLeadOpenMode,
  saveLeadOpenMode,
  useLeadOpenMode,
} from '@/composables/leadOpenMode'
import ImportLeadsModal from '@/components/Modals/ImportLeadsModal.vue'
import NoteModal from '@/components/Modals/NoteModal.vue'
import TaskModal from '@/components/Modals/TaskModal.vue'
import ViewControls from '@/components/ViewControls.vue'
import { getMeta } from '@/stores/meta'
import { globalStore } from '@/stores/global'
import { usersStore } from '@/stores/users'
import { statusesStore } from '@/stores/statuses'
import { sessionStore } from '@/stores/session'
import { leadDrilldownStore } from '@/stores/leadDrilldown'
import { viewsStore } from '@/stores/views'
import LucideFilter from '~icons/lucide/filter'
import LucideCalendarClock from '~icons/lucide/calendar-clock'
import LucideArrowDownUp from '~icons/lucide/arrow-down-up'
import { callEnabled } from '@/composables/settings'
import { useBroadcast } from '@/composables/useBroadcast'
import {
  formatDate,
  timeAgo,
  website,
  formatTime,
  dueColor,
  mostUrgentTint,
  ddExpiration,
  parseColor,
  firstCallRead,
} from '@/utils'
import { formatPhone } from '@/utils/phoneFormat'
import { clickToCall } from '@/composables/clickToCall'
import { Avatar, Tooltip, Dropdown, call } from 'frappe-ui'
import { useRoute, useRouter } from 'vue-router'
import {
  ref,
  computed,
  reactive,
  watch,
  h,
  nextTick,
  onMounted,
  onBeforeUnmount,
} from 'vue'

const { getFormattedPercent, getFormattedFloat, getFormattedCurrency } =
  getMeta('CRM Lead')
const { makeCall, $socket } = globalStore()
const { getUser } = usersStore()
const { getLeadStatus } = statusesStore()
const { user } = sessionStore()
const { on } = useBroadcast()

const route = useRoute()
const router = useRouter()

const leadsListView = ref(null)
const showLeadModal = ref(false)
const showImportModal = ref(false)

// Opening a lead from the Kanban without losing your place on the board.
//
// The card stays a real <router-link> -- it keeps a genuine href, so cmd/middle
// click still opens the full lead in a background tab, and vue-router's own
// guardEvent ignores modified clicks before we ever see them. We only intercept
// the plain left click and decide what it should do.
const leadOpenMode = useLeadOpenMode()
const showQuickView = ref(false)
const quickViewLead = ref('')
const showOpenModePrompt = ref(false)
const pendingLead = ref('')

function openQuickView(name) {
  quickViewLead.value = name
  showQuickView.value = true
}

function goToLead(name) {
  router.push({
    name: 'Lead',
    params: { leadId: name },
    query: { view: route.query.view, viewType: route.params.viewType },
  })
}

async function onCardClick(row, e) {
  // Let the browser handle new-tab/new-window intents itself. vue-router would
  // ignore these too, but bailing here means we also never preventDefault them.
  if (e && (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || e.button)) return

  // We are taking responsibility for this click, so stop the router-link from
  // navigating. This MUST happen synchronously, before any await -- vue-router's
  // guardEvent checks defaultPrevented during the same event dispatch.
  e?.preventDefault()

  let mode = leadOpenMode.value
  // null means the preference has genuinely not been fetched yet (a click landed
  // before the board's mount request resolved). Waiting is right: guessing here
  // would open the wrong surface on the very first click after a page load.
  if (mode === null) mode = await loadLeadOpenMode()

  if (mode === LEAD_OPEN_PAGE) return goToLead(row.name)
  if (mode === LEAD_OPEN_MODAL) return openQuickView(row.name)

  // Never asked -- ask once, then honour the answer for this very click.
  pendingLead.value = row.name
  showOpenModePrompt.value = true
}

async function onOpenModeChosen(mode) {
  const name = pendingLead.value
  pendingLead.value = ''
  if (!name) {
    saveLeadOpenMode(mode)
    return
  }
  if (mode === LEAD_OPEN_PAGE) {
    saveLeadOpenMode(mode)
    return goToLead(name)
  }
  // Let the prompt finish unmounting before the quick view mounts, so the two
  // dialogs are never on screen together (see the v-if note in the template).
  // Persisting the choice is deliberately NOT awaited here: it is a preference
  // write, and making the lead the user asked for wait on it would put a round
  // trip in front of every first click.
  saveLeadOpenMode(mode)
  await nextTick()
  openQuickView(name)
}

// The "..." menu beside Create. Bulk import lives here rather than on the main
// row: it's an occasional vendor-batch action, not a daily one.
const leadListActions = computed(() => [
  {
    label: __('Import leads'),
    icon: 'upload',
    onClick: () => (showImportModal.value = true),
  },
])

function onImported() {
  // The imported batch is parked behind import_hidden, so the current list
  // won't change — but the new saved views need to show up in the switcher.
  viewsStore().reload()
}

// ── Import-list promote banner ───────────────────────────────────────
// Only while an auto-created import view is open. That view's filter IS the
// list tag, so the list name is read back out of it rather than tracked
// separately.
const importList = ref({ parked: 0, list_name: '' })
const promoting = ref(false)

const openImportList = computed(() => {
  const v = viewsStore().getView(
    route.query.view,
    route.params.viewType,
    'CRM Lead',
  )
  if (!v?.filters) return ''
  try {
    const f = typeof v.filters === 'string' ? JSON.parse(v.filters) : v.filters
    const like = f?.import_lists
    const val = Array.isArray(like) ? like[1] : like
    const m = typeof val === 'string' ? val.match(/%"(.+)"%/) : null
    return m ? m[1] : ''
  } catch (e) {
    return ''
  }
})

async function refreshImportList() {
  const name = openImportList.value
  if (!name) {
    importList.value = { parked: 0, list_name: '' }
    return
  }
  try {
    importList.value = await call('crm.api.lead_import.get_list_summary', {
      list_name: name,
    })
  } catch (e) {
    importList.value = { parked: 0, list_name: '' }
  }
}

async function promoteList() {
  promoting.value = true
  try {
    await call('crm.api.lead_import.unhide_leads', {
      list_name: importList.value.list_name,
    })
    await refreshImportList()
    viewControls.value?.reload?.()
  } finally {
    promoting.value = false
  }
}

watch(openImportList, refreshImportList, { immediate: true })

on('trigger_lead_create', (data) => {
  showLeadModal.value = Boolean(data)
})

const defaults = reactive({})

// leads data is loaded in the ViewControls component
const leads = ref({})
const loadMore = ref(1)
const triggerResize = ref(1)
const updatedPageCount = ref(20)
const viewControls = ref(null)

// Drill-down from the Leads dashboard: an ad-hoc set of lead names to show.
// Injected as a never-persisted `name in [...]` default filter; while a drill
// is active we drop the `converted: 0` base filter so the list matches the
// dashboard's count exactly (which includes converted leads).
const drilldown = leadDrilldownStore()

// "Tasks due" kanban filter: show only leads that have an open task due today /
// overdue. _next_task_due is a computed pseudo-field (not a DB column), so we
// resolve the matching lead names server-side and inject them as the same
// never-persisted `name in [...]` default filter the dashboard drill uses.
//
// The chosen *scope* (not the resolved names — those go stale) is persisted
// per-user in localStorage so the filter survives leaving the board (into a
// lead and back) and full page reloads, staying on until the user clears it.
// On mount we re-resolve the names fresh from the persisted scope (see
// onMounted below). Keyed by user so a different login doesn't inherit it.
const TASK_DUE_STORAGE_KEY = 'leadsTaskDueScope:' + user
const taskDueScope = ref(localStorage.getItem(TASK_DUE_STORAGE_KEY) || '')
const taskDueNames = ref([])

const listFilters = computed(() => {
  if (drilldown.active) {
    return { name: ['in', drilldown.names.length ? drilldown.names : ['__none__']] }
  }
  if (taskDueScope.value) {
    return {
      converted: 0,
      name: ['in', taskDueNames.value.length ? taskDueNames.value : ['__none__']],
    }
  }
  return { converted: 0 }
})

const TASK_DUE_LABELS = {
  today: __('Due today'),
  overdue: __('Overdue'),
  today_overdue: __('Due today + overdue'),
}
const taskDueLabel = computed(() =>
  taskDueScope.value ? TASK_DUE_LABELS[taskDueScope.value] : __('Tasks due'),
)
const taskDueOptions = computed(() => {
  const opts = [
    { label: __('Due today'), onClick: () => applyTaskDue('today') },
    { label: __('Overdue'), onClick: () => applyTaskDue('overdue') },
    { label: __('Due today + overdue'), onClick: () => applyTaskDue('today_overdue') },
  ]
  if (taskDueScope.value) {
    opts.push({ label: __('Clear'), icon: 'x', onClick: clearTaskDue })
  }
  return opts
})

async function applyTaskDue(scope) {
  const names = await call('crm.api.doc.get_docs_with_due_tasks', {
    doctype: 'CRM Lead',
    scope,
  })
  taskDueNames.value = names || []
  taskDueScope.value = scope
  localStorage.setItem(TASK_DUE_STORAGE_KEY, scope)
  nextTick(() => viewControls.value?.reload())
}

function clearTaskDue() {
  taskDueScope.value = ''
  taskDueNames.value = []
  localStorage.removeItem(TASK_DUE_STORAGE_KEY)
  nextTick(() => viewControls.value?.reload())
}

// Kanban "sort by next task due": orders each column's cards by their soonest
// open task (the _next_task_due pseudo-field). The server can't SQL-sort a
// computed field, so it re-derives the card order per column from this order_by.
// Active direction is read straight off the live order_by so it survives reloads
// and view switches; '' = the default (modified desc), no task sort applied.
const taskSortDir = computed(() => {
  const ob = leads.value?.params?.order_by || ''
  if (!ob.startsWith('_next_task_due')) return ''
  return ob.includes('desc') ? 'desc' : 'asc'
})
const taskSortLabel = computed(() => {
  if (taskSortDir.value === 'asc') return __('Task due: soonest')
  if (taskSortDir.value === 'desc') return __('Task due: latest')
  return __('Sort by task')
})
const taskSortOptions = computed(() => {
  const opts = [
    {
      label: __('Soonest due first'),
      onClick: () => applyTaskSort('_next_task_due asc'),
    },
    {
      label: __('Latest due first'),
      onClick: () => applyTaskSort('_next_task_due desc'),
    },
  ]
  if (taskSortDir.value) {
    opts.push({
      label: __('Clear'),
      icon: 'x',
      onClick: () => applyTaskSort('modified desc'),
    })
  }
  return opts
})

function applyTaskSort(orderBy) {
  viewControls.value?.updateSort(orderBy)
}

function clearDrill() {
  drilldown.clear()
  // Reload only after the updated `:filters` prop has propagated to ViewControls,
  // otherwise getParams() re-reads the stale `name in [...]` filter.
  nextTick(() => viewControls.value?.reload())
}

// Both lookups below are called from the Kanban card template ~25 times per
// card (once per v-if branch, per field). Scanning `rows` with .find() on each
// call made board rendering quadratic in card count — 287 cards cost ~7,000
// scans of a 287-element array. Index once per data change instead.
//
// The results are memoized as well, because getRow allocated a fresh
// `{ label }` wrapper on every call. Returning a stable object also lets Vue
// skip patching a child whose props haven't actually changed.
// "Dennis Szafran" → DS; a single-word name → its first two letters. Reads
// the parsed lead_owner row (get_data always fetches it for CRM Lead), so it
// works whether or not Lead Owner is a configured card field.
function ownerInitials(name) {
  const owner = getRow(name, 'lead_owner')
  const full = (owner?.full_name || owner?.label || '').trim()
  if (!full) return ''
  const words = full.split(/\s+/).filter((w) => /[a-z]/i.test(w[0]))
  if (words.length >= 2) return words[0][0] + words[words.length - 1][0]
  if (words.length == 1) return words[0].slice(0, 2)
  return ''
}

function getRow(name, field) {
  const cache = rowValueCache.value
  const key = name + '\u0000' + field
  let value = cache.get(key)
  if (value !== undefined) return value

  const row = rowsByName.value.get(name)
  const raw = row ? row[field] : undefined
  value =
    raw && typeof raw === 'object' && !Array.isArray(raw) ? raw : { label: raw }
  cache.set(key, value)
  return value
}

// rows.value holds formatted/display values; the inline Kanban-card editor
// needs the raw stored value, which lives on the un-parsed kanban data.
// Fields rendered purely as a graphic, with no text label of their own.
//
// They need their own visibility rule, and getting this wrong is not a silent
// no-op: the generic guard is "does this field have a label", a badge field has
// none, so the slot emits nothing -- and Vue treats a comment-only slot as EMPTY
// and falls back to <KanbanView>'s default row, which prints the raw field
// value. That is how `_dispo_buyers` shipped to production rendering its own
// JSON as text on 70 cards.
const BADGE_ONLY_FIELDS = new Set(['_dispo_buyers'])

function showsField(name, field, showBlank) {
  if (BADGE_ONLY_FIELDS.has(field)) return Boolean(getRawValue(name, field))
  return Boolean(getRow(name, field).label || showBlank)
}

function getRawValue(name, field) {
  const lead = rawRowsByName.value.get(name)
  return lead ? lead[field] : ''
}

// Re-fetch the board after an inline card edit so values (and any column move
// when the grouping field changes) reflect the saved state.
function reloadKanban() {
  leads.value?.reload?.()
}

// Kanban card tint. A card carries two independent signals — the
// untouched-new-lead age (`_new_lead_color`) and the soonest open task's due
// date (`_next_task_due`) — and must surface the MOST URGENT one (a `||` would
// let a "created today" amber hide an overdue task on the same card).
function cardTint(row) {
  return mostUrgentTint(row._new_lead_color, dueColor(row._next_task_due))
}

// `crm_task_update` and `crm_first_call` are broadcast SITE-WIDE (every logged-in
// user sits in the site room), and the badges they affect (_next_task_due,
// _first_call) are computed server-side. This used to answer by refetching the
// whole board — ~300KB and a few hundred ms of server time, then a full
// re-render — for one changed card, usually triggered by somebody else's click.
// At 35-116 task changes on a normal day, an idle open board froze over and
// over for reasons its owner never caused.
//
// Refresh just the affected card instead, and coalesce bursts.
const pendingCardRefresh = new Set()
let cardRefreshTimer = null

function findCard(name) {
  for (const col of leads.value?.data?.data || []) {
    const index = (col.data || []).findIndex((r) => r.name === name)
    if (index !== -1) return { col, index }
  }
  return null
}

function queueCardRefresh(data) {
  if (data?.reference_doctype !== 'CRM Lead') return
  if (route.params.viewType !== 'kanban') return
  const name = data.reference_docname
  if (!name || !findCard(name)) return // not on this board — nothing to show

  pendingCardRefresh.add(name)
  clearTimeout(cardRefreshTimer)
  cardRefreshTimer = setTimeout(flushCardRefresh, 250)
}

async function flushCardRefresh() {
  const names = [...pendingCardRefresh]
  pendingCardRefresh.clear()
  if (!names.length) return

  const groupField = leads.value?.data?.column_field
  const rowFields = leads.value?.data?.rows || []

  for (const name of names) {
    const hit = findCard(name)
    if (!hit) continue
    try {
      const fresh = await call('crm.api.doc.get_kanban_card', {
        doctype: 'CRM Lead',
        name,
        rows: JSON.stringify(rowFields),
      })
      // Deleted, no longer visible to this user, or moved to another column:
      // the board's shape changed, so do the honest thing and refetch it.
      if (!fresh || (groupField && fresh[groupField] !== hit.col.column.name)) {
        reloadKanban()
        return
      }
      Object.assign(hit.col.data[hit.index], fresh)
    } catch (e) {
      reloadKanban()
      return
    }
  }
}

onMounted(() => {
  $socket.on('crm_task_update', queueCardRefresh)
  $socket.on('crm_first_call', queueCardRefresh)
  // Warm the lead-open preference so the FIRST card click already knows what to
  // do. onCardClick can await it if this has not landed, but that would put a
  // round trip in front of the very first open of the session.
  loadLeadOpenMode()
  // Restore a persisted "Tasks due" filter: re-resolve the matching lead names
  // fresh (the stored scope is the source of truth; names go stale), then let
  // applyTaskDue reload the board with them. Skip while a dashboard drill is
  // active — that takes precedence in listFilters.
  if (taskDueScope.value && !drilldown.active) {
    applyTaskDue(taskDueScope.value)
  }
})
onBeforeUnmount(() => {
  $socket.off('crm_task_update', queueCardRefresh)
  $socket.off('crm_first_call', queueCardRefresh)
  clearTimeout(cardRefreshTimer)
})

// Rows
const rows = computed(() => {
  if (!leads.value?.data?.data) return []
  if (leads.value.data.view_type === 'group_by') {
    if (!leads.value?.data.group_by_field?.fieldname) return []
    return getGroupedByRows(
      leads.value?.data.data,
      leads.value?.data.group_by_field,
      leads.value.data.columns,
    )
  } else if (leads.value.data.view_type === 'kanban') {
    return getKanbanRows(leads.value.data.data, leads.value.data.fields)
  } else {
    return parseRows(leads.value?.data.data, leads.value.data.columns)
  }
})

// The user-applied filters currently narrowing this board, for the warning
// banner. Deliberately excludes:
//   * named/public views (route.query.view) — those are filtered on purpose;
//   * anything in `listFilters`, i.e. the dashboard drill-down and the
//     tasks-due scope, which are injected as default_filters and already have
//     their own banner/dropdown. Filter.vue draws the same distinction.
const activeFilters = computed(() => {
  if (route.query.view) return []
  const applied = leads.value?.params?.filters || {}
  const injected = listFilters.value || {}
  const fields = leads.value?.data?.fields || []

  return Object.keys(applied)
    .filter((key) => !(key in injected))
    .map((key) => {
      const raw = applied[key]
      const isRange = Array.isArray(raw)
      const operator = isRange ? String(raw[0] ?? '').toLowerCase() : '='
      return {
        key,
        label: fields.find((f) => f.fieldname === key)?.label || key,
        op: operator.includes('like') ? __('contains') : __('is'),
        // strip the %wildcards% a LIKE carries so it reads as what was typed
        value: String((isRange ? raw[1] : raw) ?? '').replace(/%/g, ''),
      }
    })
})

// The Communication table is EMPTY on this site (0 rows, ever) because there is
// no email integration in use, so the email counter rendered "@ 0↑ 0↓" on every
// card forever — ~70px of the widest row on a 268px card, spent on nothing. It
// was also what pushed the counters past the card edge and what the hover menu
// had to overlap. Show it when there is something to show.
//
// Calls and texts stay unconditional on purpose: a zero there is a real signal a
// setter acts on ("never called"), not an absence.
function hasEmailActivity(name) {
  return (
    Number(getRow(name, '_email_out_count').label || 0) > 0 ||
    Number(getRow(name, '_email_in_count').label || 0) > 0
  )
}

// name -> parsed (display-formatted) row, rebuilt only when `rows` changes.
const rowsByName = computed(() => {
  const map = new Map()
  for (const row of rows.value || []) map.set(row.name, row)
  return map
})

// Memo for getRow(), thrown away (by being recomputed) whenever the underlying
// rows change, so it can never serve a stale cell.
const rowValueCache = computed(() => {
  rowsByName.value
  return new Map()
})

// name -> raw server row, across every kanban column.
const rawRowsByName = computed(() => {
  const map = new Map()
  for (const col of leads.value?.data?.data || []) {
    for (const row of col.data || []) map.set(row.name, row)
  }
  return map
})

const columns = computed(() => {
  let _columns = leads.value?.data?.columns || []

  // Set align right for last column
  if (_columns.length) {
    _columns = _columns.map((col, index) => {
      if (index === _columns.length - 1) {
        return { ...col, align: 'right' }
      }
      return col
    })
  }

  return _columns
})

function getGroupedByRows(listRows, groupByField, columns) {
  let groupedRows = []

  groupByField.options?.forEach((option) => {
    let filteredRows

    if (!option) {
      filteredRows = listRows.filter((row) => !row[groupByField.fieldname])
    } else {
      filteredRows = listRows.filter(
        (row) => row[groupByField.fieldname] == option,
      )
    }

    let groupDetail = {
      label: groupByField.label,
      group: option || __(' '),
      collapsed: false,
      rows: parseRows(filteredRows, columns),
    }
    if (groupByField.fieldname == 'status') {
      groupDetail.icon = () =>
        h(IndicatorIcon, {
          class: getLeadStatus(option)?.color,
        })
    }
    groupedRows.push(groupDetail)
  })

  return groupedRows || listRows
}

function getKanbanRows(data, columns) {
  let _rows = []
  data.forEach((column) => {
    column.data?.forEach((row) => {
      _rows.push(row)
    })
  })
  return parseRows(_rows, columns)
}

function parseRows(rows, columns = []) {
  let view_type = leads.value.data.view_type
  let key = view_type === 'kanban' ? 'fieldname' : 'key'
  let type = view_type === 'kanban' ? 'fieldtype' : 'type'

  return rows.map((lead) => {
    let _rows = {}
    leads.value?.data.rows.forEach((row) => {
      _rows[row] = lead[row]

      let fieldType = columns?.find((col) => (col[key] || col.value) == row)?.[
        type
      ]

      if (
        fieldType &&
        ['Date', 'Datetime'].includes(fieldType) &&
        !['modified', 'creation'].includes(row)
      ) {
        _rows[row] = formatDate(lead[row], '', true, fieldType == 'Datetime')
      }

      if (fieldType && fieldType == 'Currency') {
        _rows[row] = getFormattedCurrency(row, lead)
      }

      if (fieldType && fieldType == 'Float') {
        _rows[row] = getFormattedFloat(row, lead)
      }

      if (fieldType && fieldType == 'Percent') {
        _rows[row] = getFormattedPercent(row, lead)
      }

      if (['mobile_no', 'phone'].includes(row)) {
        _rows[row] = formatPhone(lead[row])
      }

      if (row == 'dd_expiration_date') {
        // "7/16/26 (2 days left)" + due color; overrides the generic Date
        // formatting applied above.
        _rows[row] = ddExpiration(lead[row])
      } else if (row == 'lead_name') {
        _rows[row] = {
          label: lead.lead_name,
          image: lead.image,
          image_label: lead.first_name,
        }
      } else if (row == 'organization') {
        _rows[row] = lead.organization
      } else if (row === 'website') {
        _rows[row] = website(lead.website)
      } else if (row == 'status') {
        _rows[row] = {
          label: lead.status,
          color: getLeadStatus(lead.status)?.color,
        }
      } else if (row == 'sla_status') {
        let value = lead.sla_status
        let tooltipText = value
        let color =
          lead.sla_status == 'Failed'
            ? 'red'
            : lead.sla_status == 'Fulfilled'
              ? 'green'
              : 'orange'
        if (value == 'First Response Due' || value == 'Rolling Response Due') {
          value = __(timeAgo(lead.response_by))
          tooltipText = formatDate(lead.response_by)
          if (new Date(lead.response_by) < new Date()) {
            color = 'red'
          }
        }
        _rows[row] = {
          label: tooltipText,
          value: value,
          color: color,
        }
      } else if (row == 'lead_owner') {
        _rows[row] = {
          label: lead.lead_owner && getUser(lead.lead_owner).full_name,
          ...(lead.lead_owner && getUser(lead.lead_owner)),
        }
      } else if (row == '_assign') {
        let assignees = JSON.parse(lead._assign || '[]')
        _rows[row] = assignees.map((user) => ({
          name: user,
          image: getUser(user).user_image,
          label: getUser(user).full_name,
        }))
      } else if (['modified', 'creation'].includes(row)) {
        _rows[row] = {
          label: formatDate(lead[row]),
          timeAgo: __(timeAgo(lead[row])),
        }
      } else if (
        ['first_response_time', 'first_responded_on', 'response_by'].includes(
          row,
        )
      ) {
        let field = row == 'response_by' ? 'response_by' : 'first_responded_on'
        _rows[row] = {
          label: lead[field] ? formatDate(lead[field]) : '',
          timeAgo: lead[row]
            ? row == 'first_response_time'
              ? formatTime(lead[row])
              : __(timeAgo(lead[row]))
            : '',
        }
      }
    })
    _rows['_email_count'] = lead._email_count
    _rows['_note_count'] = lead._note_count
    _rows['_task_count'] = lead._task_count
    _rows['_comment_count'] = lead._comment_count
    _rows['_call_out_count'] = lead._call_out_count
    _rows['_call_in_count'] = lead._call_in_count
    _rows['_text_out_count'] = lead._text_out_count
    _rows['_text_in_count'] = lead._text_in_count
    _rows['_email_out_count'] = lead._email_out_count
    _rows['_email_in_count'] = lead._email_in_count
    _rows['_last_comm'] = {
      label: lead._last_comm ? formatDate(lead._last_comm) : '',
      timeAgo: lead._last_comm ? __(timeAgo(lead._last_comm)) : '',
    }
    _rows['_next_task_due'] = {
      label: lead._next_task_due ? formatDate(lead._next_task_due) : '',
      value: lead._next_task_due ? __(timeAgo(lead._next_task_due)) : '',
      color: dueColor(lead._next_task_due),
    }
    // First-Call Read quadrant chip — only when both axes answered (server sends
    // "_first_call" as "motivated|on_price", e.g. "Yes|No").
    const [_fcMot, _fcPrice] = (lead._first_call || '|').split('|')
    const _fc = firstCallRead(_fcMot, _fcPrice)
    _rows['_first_call'] = {
      label: _fc.quad ? __(_fc.quad.label) : '',
      color: _fc.quad ? _fc.quad.theme : '',
    }
    // Rendered by <DispoBuyerBadges> off the RAW value. The blank label is
    // deliberate: without an entry here the generic parser hands back the
    // field's JSON, which then becomes the card's copy-to-clipboard text and,
    // if any guard slips, gets printed on the card verbatim.
    _rows['_dispo_buyers'] = { label: '' }
    return _rows
  })
}

function onNewClick(column) {
  let column_field = leads.value.params.column_field

  if (column_field) {
    defaults[column_field] = column.column.name
  }

  showLeadModal.value = true
}

function actions(itemName) {
  let mobile_no = getRow(itemName, 'mobile_no')?.label || ''
  let actions = [
    {
      icon: h(PhoneIcon, { class: 'h-4 w-4' }),
      label: __('Make a Call'),
      onClick: () => makeCall(mobile_no),
      condition: () => mobile_no && callEnabled.value,
    },
    {
      icon: h(NoteIcon, { class: 'h-4 w-4' }),
      label: __('New Note'),
      onClick: () => showNote(itemName),
    },
    {
      icon: h(TaskIcon, { class: 'h-4 w-4' }),
      label: __('New Task'),
      onClick: () => showTask(itemName),
    },
  ]
  return actions.filter((action) =>
    action.condition ? action.condition() : true,
  )
}

const docname = ref('')
const showNoteModal = ref(false)
const note = ref({
  title: '',
  content: '',
})

function showNote(name) {
  docname.value = name
  showNoteModal.value = true
}

const showTaskModal = ref(false)
const task = ref({
  title: '',
  description: '',
  assigned_to: '',
  due_date: '',
  priority: 'Low',
  status: 'Backlog',
})

function showTask(name) {
  docname.value = name
  showTaskModal.value = true
}
</script>
