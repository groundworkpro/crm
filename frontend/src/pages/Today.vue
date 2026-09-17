<template>
  <LayoutHeader>
    <template #left-header>
      <div class="flex items-center gap-2">
        <span class="text-lg font-semibold text-ink-gray-8">{{ __('Today') }}</span>
        <Badge v-if="board.data?.date" variant="subtle" theme="gray" :label="prettyDate" />
        <!-- Whose board this is belongs next to the title, not in Filters: it
             says what you are looking at rather than narrowing it. -->
        <Dropdown :options="ownerOptions" placement="bottom-start">
          <Button :variant="viewingOwnBoard ? 'ghost' : 'subtle'" :theme="viewingOwnBoard ? 'gray' : 'blue'" icon-right="chevron-down">
            <span class="max-w-[12rem] truncate">{{ ownerLabel }}</span>
          </Button>
        </Dropdown>
      </div>
    </template>
    <template #right-header>
      <div class="flex flex-wrap items-center justify-end gap-2">
        <Dropdown :options="filterOptions" placement="bottom-end">
          <Button
            :label="activeFilterCount ? __('Filters ({0})', [activeFilterCount]) : __('Filters')"
            :variant="activeFilterCount ? 'subtle' : undefined"
            :theme="activeFilterCount ? 'blue' : 'gray'"
            icon-right="chevron-down"
          >
            <template #prefix><FeatherIcon name="filter" class="size-4" /></template>
          </Button>
        </Dropdown>
        <Button :label="streakLabel" @click="openTodayReport" />
        <!-- Handing a deal over is a board-level action, so it lives next to the
             other board-level controls rather than on each card: the whole point
             is to move several at once. -->
        <Button
          :label="selecting ? __('Done') : __('Reassign')"
          :variant="selecting ? 'subtle' : undefined"
          :theme="selecting ? 'blue' : 'gray'"
          @click="toggleSelecting"
        >
          <template #prefix><FeatherIcon name="check-square" class="size-4" /></template>
        </Button>
        <Button :label="__('Priority')" @click="showPriorityModal = true">
          <template #prefix><FeatherIcon name="list" class="size-4" /></template>
        </Button>
        <Badge
          v-if="toCallCount"
          variant="subtle"
          theme="blue"
          :label="`${toCallLeadCount} ${toCallLeadCount === 1 ? __('lead') : __('leads')} · ${callsOwed} ${callsOwed === 1 ? __('call') : __('calls')}`"
        />
        <!-- "All clear" has to mean you FINISHED, not that you were never given
             anything: with the board scoped per person, a rep who owns no leads
             would otherwise get the same congratulatory badge as one who worked
             through 40 cards. -->
        <Badge
          v-else-if="boardCardCount"
          variant="subtle"
          theme="green"
          :label="__('All clear')"
        />
        <Badge
          v-else
          variant="subtle"
          theme="gray"
          :label="viewingOwnBoard ? __('No leads on your board') : __('No leads on this board')"
        />
        <Tooltip
          :text="__('Re-run the cadence now and add any newly-due leads or tasks')"
          placement="bottom"
        >
          <Button :label="__('Sync list')" :loading="refreshing" @click="syncList">
            <template #prefix><RefreshIcon class="h-4 w-4" /></template>
          </Button>
        </Tooltip>
        <Badge
          v-if="syncStatus"
          variant="subtle"
          :theme="syncStatusTheme"
          :label="syncStatus"
          role="status"
        />
      </div>
    </template>
  </LayoutHeader>

  <div v-if="board.data && !board.data.available" class="flex h-full items-center justify-center">
    <div class="text-center text-ink-gray-5">
      <p class="text-base">{{ __('The Today board is not set up on this site yet.') }}</p>
      <p class="mt-1 text-sm">{{ __('Run scripts/setup_today_board.py from the ops repo.') }}</p>
    </div>
  </div>

  <!-- The selection bar counts LEADS, not cards. A lead can hold two call cards,
       so "4 selected" followed by a toast saying 2 leads moved reads as a bug;
       ownership moves per lead, so that is the unit to lead with. -->
  <div
    v-if="selecting && board.data?.available !== false"
    class="flex flex-wrap items-center gap-2 border-b border-outline-gray-2 bg-surface-blue-1 px-4 py-2"
  >
    <span class="text-sm font-medium text-ink-gray-8">
      {{ selectionSummary }}
    </span>
    <span v-if="selectedLeadCount && selectedNames.length > selectedLeadCount" class="text-xs text-ink-gray-5">
      {{ __('{0} cards', [selectedNames.length]) }}
    </span>
    <div class="ml-auto flex items-center gap-2">
      <Button
        v-if="selectedNames.length"
        :label="__('Clear')"
        variant="ghost"
        @click="clearSelection"
      />
      <Dropdown :options="assigneeOptions" placement="bottom-end">
        <Button
          variant="solid"
          :disabled="!selectedLeadCount || assigning"
          :loading="assigning"
          :label="__('Assign to…')"
          icon-right="chevron-down"
        />
      </Dropdown>
    </div>
  </div>

  <div v-if="board.data?.available !== false" class="flex flex-1 gap-3 overflow-x-auto p-4">
    <div
      v-for="col in columns"
      :key="col.state"
      class="flex min-w-[17rem] flex-1 basis-0 flex-col rounded-lg bg-surface-gray-1"
    >
      <div class="flex items-center justify-between px-3 py-2">
        <div class="flex items-center gap-2">
          <div class="h-2 w-2 rounded-full" :class="dotClass(col.state)" />
          <span class="text-sm font-medium text-ink-gray-8">{{ __(col.state) }}</span>
          <span class="text-xs text-ink-gray-5">{{ col.items.length }}</span>
        </div>
      </div>

      <!-- `change` rather than `end`: it names the destination column and hands
           back the moved card, which is what decides whether the outcome modal
           has to open before anything is persisted. -->
      <!-- Dragging is disabled while selecting: a drag and a pick are the same
           gesture on a card, and a mis-drag in selection mode would resolve a
           card (and open the outcome modal) when the rep meant to tick it. -->
      <Draggable
        v-model="col.items"
        :group="'today'"
        item-key="name"
        :disabled="selecting"
        filter="[data-no-drag]"
        :prevent-on-filter="false"
        class="flex min-h-[6rem] flex-1 flex-col gap-2 overflow-y-auto px-2 pb-3"
        @change="onChange($event, col)"
      >
        <template #item="{ element: item }">
          <div
            class="group relative cursor-pointer rounded-lg bg-surface-white p-3 shadow-sm ring-1 hover:ring-outline-gray-3"
            :class="
              isSelected(item)
                ? 'ring-2 ring-outline-blue-2 bg-surface-blue-1'
                : 'ring-outline-gray-1'
            "
            @click="selecting ? toggleCard(item) : openTodayItem(item)"
          >
            <!-- In selection mode the hover actions are replaced by a checkbox
                 rather than sitting alongside it: Done/Skip/Put-back all resolve
                 a card, which is a different job from choosing it. -->
            <div v-if="selecting" class="absolute right-2 top-2" @click.stop>
              <input
                type="checkbox"
                class="size-4 cursor-pointer rounded border-outline-gray-3 text-ink-blue-3 focus:ring-0"
                :checked="isSelected(item)"
                :aria-label="__('Select {0}', [item.lead_name])"
                @change="toggleCard(item)"
              />
            </div>
            <div
              v-else
              class="absolute right-2 top-2 flex items-center gap-1 opacity-0 transition-opacity group-hover:opacity-100 group-focus-within:opacity-100"
              @click.stop
            >
              <Tooltip :text="item.state === 'Done' ? __('Edit outcome') : __('Done')">
                <button
                  class="flex size-7 items-center justify-center rounded-md hover:bg-surface-gray-2"
                  @click.stop="requestState(item, 'Done')"
                >
                  <CheckIcon class="size-4 text-ink-green-3" />
                </button>
              </Tooltip>
              <Tooltip :text="item.state === 'Skipped' ? __('Edit skip reason') : __('Skip for today')">
                <button
                  class="flex size-7 items-center justify-center rounded-md hover:bg-surface-gray-2"
                  @click.stop="requestState(item, 'Skipped')"
                >
                  <BanIcon class="size-4 text-ink-gray-5" />
                </button>
              </Tooltip>
              <Tooltip v-if="item.state !== 'To Call'" :text="__('Put back')">
                <button
                  class="flex size-7 items-center justify-center rounded-md hover:bg-surface-gray-2"
                  @click.stop="requestState(item, 'To Call')"
                >
                  <UndoIcon class="size-4 text-ink-gray-5" />
                </button>
              </Tooltip>
            </div>

            <div class="min-w-0 pr-16">
              <div class="flex items-center gap-2">
                <div v-if="showOwnerOnCard" class="shrink-0" @click.stop>
                  <Dropdown
                    :options="cardAssigneeOptions(item)"
                    placement="bottom-start"
                  >
                    <button
                      class="flex size-6 items-center justify-center rounded-full bg-surface-gray-2 text-[10px] font-semibold uppercase tracking-tight text-ink-gray-7 hover:bg-surface-gray-3 disabled:opacity-60"
                      :title="item.owner_name || __('Unassigned')"
                      :aria-label="__('Reassign {0}', [item.owner_name || __('Unassigned')])"
                      :disabled="assigning"
                    >
                      {{ ownerInitials(item) }}
                    </button>
                  </Dropdown>
                </div>
                <div
                  class="min-w-0 flex-1 truncate text-base font-medium text-ink-gray-8"
                  :class="item.state === 'Skipped' ? 'line-through opacity-60' : ''"
                >
                  {{ item.lead_name }}
                </div>
              </div>
              <button
                v-if="item.address"
                class="mt-0.5 block max-w-full truncate text-left text-xs text-ink-gray-5 hover:text-ink-blue-3 hover:underline"
                :title="item.address"
                @click.stop="openAddress(item.address)"
              >
                {{ item.address }}
              </button>
              <div v-else class="mt-0.5 text-xs text-ink-gray-5">—</div>
              <div
                v-if="item.zillow_unresolved"
                class="mt-0.5 text-xs font-medium text-ink-amber-3"
              >
                {{ __('Address not on Zillow — ask the seller') }}
              </div>
              <div v-if="item.mobile_no" class="mt-0.5 flex items-center gap-1.5">
                <button
                  type="button"
                  class="w-fit text-xs text-ink-blue-3 hover:underline"
                  @click.stop="clickToCall(item.mobile_no, { lead: item.lead, name: item.lead_name })"
                >
                  {{ formatPhone(item.mobile_no) }}
                </button>
                <Tooltip :text="__('Send text')">
                  <button
                    class="flex size-6 items-center justify-center rounded text-ink-blue-3 hover:bg-surface-blue-1"
                    @click.stop="openText(item)"
                  >
                    <FeatherIcon name="message-square" class="size-3.5" />
                  </button>
                </Tooltip>
              </div>
            </div>

            <div class="mt-2 flex flex-wrap items-center gap-1.5">
              <Badge
                v-if="PHASE[item.priority_key]"
                variant="subtle"
                :theme="PHASE[item.priority_key].theme"
                :class="PHASE[item.priority_key].badgeClass"
                :label="__(PHASE[item.priority_key].label)"
              />
              <Badge
                v-if="item.in_cadence"
                variant="subtle"
                theme="blue"
                :label="__('Cadence')"
              />
              <Badge
                v-if="item.has_task"
                variant="subtle"
                theme="green"
                :label="__('Task')"
              />
              <Badge
                v-if="item.non_refundable"
                variant="subtle"
                theme="gray"
                :label="__('No refund')"
                :title="item.non_refundable_reason || __('The provider will not refund this lead')"
              />
              <div @click.stop>
                <Dropdown
                  :options="leadStatusOptions(item)"
                  placement="bottom-start"
                >
                  <button
                    class="flex h-6 max-w-[12rem] items-center gap-1 rounded-full bg-surface-gray-2 px-2 text-xs font-medium text-ink-gray-7 hover:bg-surface-gray-3 disabled:cursor-wait disabled:opacity-60"
                    :disabled="savingStatusLeads.includes(item.lead)"
                    :title="__('Change lead status')"
                    @click.stop
                  >
                    <IndicatorIcon
                      v-if="getLeadStatus(item.lead_status)"
                      class="size-3 shrink-0"
                      :class="getLeadStatus(item.lead_status).color"
                    />
                    <span class="truncate">{{ item.lead_status || __('Set status') }}</span>
                    <FeatherIcon name="chevron-down" class="size-3 shrink-0 text-ink-gray-5" />
                  </button>
                </Dropdown>
              </div>
              <span class="text-xs text-ink-gray-5">
                {{ item.calls_today }} {{ __('logged today') }}
              </span>
            </div>

            <Tooltip
              v-if="item.last_incoming_text"
              :text="formatDate(item.last_incoming_text, 'ddd, MMM D, YYYY | hh:mm a')"
            >
              <div class="mt-2 flex w-fit items-center gap-1 rounded bg-surface-green-1 px-1.5 py-1 text-xs font-medium text-ink-green-3">
                <FeatherIcon name="flag" class="size-3.5" />
                {{ __('Texted us {0}', [__(timeAgo(item.last_incoming_text))]) }}
              </div>
            </Tooltip>

            <!-- The circle is its own hit target (tick/untick without leaving the
                 board); the rest of the row opens the lead's to-do list, where
                 this task can be edited AND more can be added — rather than a
                 single-task dialog that can only ever edit the one you clicked. -->
            <div
              v-if="cardTasks(item).length"
              class="mt-2 flex flex-col gap-1"
              @click.stop
            >
              <div
                v-for="task in cardTasks(item)"
                :key="task.name"
                class="flex w-full items-center gap-1.5 rounded-md border border-outline-gray-1 bg-surface-gray-1 px-2 py-1.5 hover:border-outline-gray-3"
              >
                <Tooltip
                  :text="task.is_completed ? __('Mark as not done') : __('Mark as done')"
                >
                  <button
                    class="flex shrink-0 items-center rounded disabled:cursor-wait disabled:opacity-60"
                    :disabled="togglingTasks.includes(task.name)"
                    @click.stop="toggleTask(item, task)"
                  >
                    <FeatherIcon
                      :name="task.is_completed ? 'check-circle' : 'circle'"
                      class="size-3.5"
                      :class="
                        task.is_completed
                          ? 'text-ink-green-3'
                          : 'text-ink-gray-4 hover:text-ink-green-3'
                      "
                    />
                  </button>
                </Tooltip>
                <!-- native `title` rather than <Tooltip>: Tooltip renders a wrapper
                     element, which would become the flex child and break the
                     truncating `min-w-0 flex-1` title on a narrow card. -->
                <button
                  class="flex min-w-0 flex-1 items-center gap-1.5 text-left hover:opacity-80"
                  :title="__('Open to-dos — edit this one or add more')"
                  @click.stop="openTodayItem(item)"
                >
                  <span
                    class="min-w-0 flex-1 truncate text-xs font-medium text-ink-gray-7"
                    :class="task.is_completed ? 'line-through opacity-60' : ''"
                  >
                    {{ task.title }}
                  </span>
                  <span
                    v-if="task.completed_at || task.due_date"
                    class="shrink-0 text-xs text-ink-gray-5"
                  >
                    {{
                      __(
                        timeAgo(
                          task.is_completed ? task.completed_at : task.due_date,
                        ),
                      )
                    }}
                  </span>
                </button>
              </div>
            </div>

            <!-- Follow-up strip: a To Call card with no open task offers the rep's
                 own due chips right on the card. One tap = a "Follow up" task at
                 that time, the same rule as the lead-page composer; "+ other"
                 swaps the strip for a title box with the same chips. Always
                 visible rather than behind a menu (Lance, 2026-09-04 — mockup B):
                 the rep works the column top to bottom and should never hunt.
                 `data-no-drag` keeps typing/tapping here from starting a drag. -->
            <div
              v-if="showFollowUpStrip(item)"
              data-no-drag
              class="followup-strip mt-2 rounded-md border px-2 py-1.5 text-xs"
              :class="
                composerFor === item.name
                  ? 'border-outline-gray-2 bg-surface-gray-1'
                  : 'border-dashed border-outline-gray-2'
              "
              @click.stop
            >
              <template v-if="composerFor !== item.name">
                <div class="flex flex-wrap items-center gap-1">
                  <span
                    class="mr-0.5 flex items-center gap-1 text-ink-gray-5"
                    :title="__('Follow up in…')"
                  >
                    <FeatherIcon name="clock" class="size-3.5" />
                    <span class="strip-label">{{ __('Follow up') }}</span>
                  </span>
                  <Tooltip v-for="(f, i) in duePresets" :key="i" :text="presetTooltip(f)">
                    <button
                      type="button"
                      class="rounded-md border border-outline-gray-1 bg-surface-white px-1.5 py-0.5 text-xs text-ink-gray-7 hover:bg-surface-gray-2 disabled:cursor-wait disabled:opacity-60"
                      :disabled="addingTaskFor.includes(item.name)"
                      @click.stop="quickFollowUp(item, f)"
                    >
                      {{ f.label }}
                    </button>
                  </Tooltip>
                  <Tooltip :text="__('Pick a date')">
                    <button
                      type="button"
                      class="flex items-center rounded-md border border-outline-gray-1 bg-surface-white px-1.5 py-1 text-ink-gray-7 hover:bg-surface-gray-2"
                      :aria-label="__('Pick a date')"
                      @click.stop="openComposer(item, { picker: true })"
                    >
                      <FeatherIcon name="calendar" class="size-3" />
                    </button>
                  </Tooltip>
                  <button
                    type="button"
                    class="ml-auto whitespace-nowrap text-ink-gray-5 hover:text-ink-gray-8"
                    :title="__('Add a different task')"
                    @click.stop="openComposer(item)"
                  >
                    +<span class="strip-label"> {{ __('other') }}</span>
                  </button>
                </div>
              </template>
              <template v-else>
                <div class="flex items-center gap-1.5">
                  <FeatherIcon name="plus" class="size-3.5 shrink-0 text-ink-gray-4" />
                  <input
                    ref="composerInput"
                    v-model="composerTitle"
                    type="text"
                    :placeholder="__('Follow up')"
                    autocomplete="off"
                    class="min-w-0 flex-1 rounded-md border border-outline-gray-2 bg-surface-white px-2 py-1 text-sm text-ink-gray-8 placeholder:text-ink-gray-4 focus:border-outline-gray-3 focus:outline-none"
                    @keydown.enter.prevent="submitComposer(item)"
                    @keydown.esc.prevent="closeComposer"
                  />
                </div>
                <div class="mt-1.5 flex flex-wrap items-center gap-1.5 pl-5">
                  <span class="text-ink-gray-4">{{ __('When') }}</span>
                  <button
                    type="button"
                    class="rounded-md border px-2 py-0.5 text-xs"
                    :class="composerChoiceClass(!composerDue)"
                    @click.stop="composerDue = ''; composerPreset = -1; composerPicker = false"
                  >
                    {{ __('No date') }}
                  </button>
                  <Tooltip v-for="(f, i) in duePresets" :key="i" :text="presetTooltip(f)">
                    <button
                      type="button"
                      class="rounded-md border px-2 py-0.5 text-xs"
                      :class="composerChoiceClass(composerPreset === i)"
                      @click.stop="pickComposerPreset(f, i)"
                    >
                      {{ f.label }}
                    </button>
                  </Tooltip>
                  <button
                    type="button"
                    class="flex items-center rounded-md border px-1.5 py-1"
                    :class="composerChoiceClass(composerPicker && composerPreset < 0)"
                    :aria-label="__('Pick a date')"
                    @click.stop="composerPreset = -1; composerPicker = !composerPicker"
                  >
                    <FeatherIcon name="calendar" class="size-3" />
                  </button>
                  <span class="ml-auto flex items-center gap-1">
                    <Button variant="ghost" size="sm" :label="__('Cancel')" @click.stop="closeComposer" />
                    <Button
                      variant="solid"
                      size="sm"
                      :label="__('Add')"
                      :loading="addingTaskFor.includes(item.name)"
                      @click.stop="submitComposer(item)"
                    />
                  </span>
                </div>
                <div v-if="composerPicker" class="mt-1.5 flex items-center gap-2 pl-5">
                  <span class="shrink-0 text-ink-gray-4">{{ __('Due') }}</span>
                  <DateTimePicker
                    v-model="composerDue"
                    class="flex-1"
                    :placeholder="__('Due date')"
                    :format="getFormat('', '', true, true, false)"
                    input-class="text-xs"
                    @update:modelValue="composerPreset = -1"
                  />
                </div>
              </template>
            </div>

            <!-- What the rep said when they resolved the card, kept on the card so
                 a wrong answer is visible (and fixable) rather than write-only. -->
            <div v-if="item.outcome || item.outcome_note" class="mt-2 flex flex-wrap items-center gap-1.5">
              <Badge
                v-if="item.outcome"
                variant="subtle"
                :theme="item.outcome === 'Booked an Appointment' ? 'green' : 'gray'"
                :label="__(item.outcome)"
              />
              <span
                v-if="item.outcome_note"
                class="min-w-0 flex-1 truncate text-xs text-ink-gray-5"
                :title="item.outcome_note"
              >
                {{ item.outcome_note }}
              </span>
            </div>

            <div v-if="item.reason" class="mt-1 truncate text-xs text-ink-gray-5">
              {{ item.reason }}
            </div>
          </div>
        </template>
      </Draggable>
    </div>
  </div>

  <TodayLeadModal
    v-model="showLeadModal"
    :item="selectedItem"
    @open-address="openAddress"
    @address-updated="onLeadAddressUpdated"
  />
  <LostReasonModal
    v-if="showLostReasonModal"
    v-model="showLostReasonModal"
    doctype="CRM Lead"
    :status="pendingLeadStatus?.status"
    :on-confirm="confirmLostStatus"
    :on-cancel="cancelLostStatus"
  />
  <PropertyLinkModal v-model="showPropertyLinkModal" :address="selectedAddress" />
  <TodayOutcomeModal
    v-model="showOutcomeModal"
    :item="pendingOutcome?.item"
    :state="pendingOutcome?.state || 'Done'"
    :saving="savingOutcome"
    @confirm="confirmOutcome"
  />
  <TodayReportModal
    v-model="showReportModal"
    :report="todayReport.data"
    :loading="todayReport.loading"
  />
  <TodayPriorityModal
    v-model="showPriorityModal"
    :priorities="priorityItems"
    :saving="savingPriority"
    @save="savePriorityOrder"
  />
  <SendTextModal
    v-if="showTextModal"
    v-model="showTextModal"
    :reference-doc="textReferenceDoc"
    doctype="CRM Lead"
    show-outcome-actions
    :options="{ afterInsert: () => board.reload() }"
    @finish="finishTextItem"
    @skip="skipTextItem"
  />
</template>

<script setup>
import LayoutHeader from '@/components/LayoutHeader.vue'
import RefreshIcon from '@/components/Icons/RefreshIcon.vue'
import TodayLeadModal from '@/components/TodayLeadModal.vue'
import TodayPriorityModal from '@/components/TodayPriorityModal.vue'
import TodayReportModal from '@/components/TodayReportModal.vue'
import IndicatorIcon from '@/components/Icons/IndicatorIcon.vue'
import LostReasonModal from '@/components/Modals/LostReasonModal.vue'
import PropertyLinkModal from '@/components/Modals/PropertyLinkModal.vue'
import SendTextModal from '@/components/Modals/SendTextModal.vue'
import TodayOutcomeModal from '@/components/Modals/TodayOutcomeModal.vue'
import { globalStore } from '@/stores/global'
import { sessionStore } from '@/stores/session'
import { statusesStore } from '@/stores/statuses'
import { formatDate, getFormat, timeAgo } from '@/utils'
import { formatPhone } from '@/utils/phoneFormat'
import { clickToCall } from '@/composables/clickToCall'
import { dueFromPreset, formatDueStamp, snapMidnightToMorning } from '@/utils/taskDue'
import { useTaskDuePresets } from '@/composables/taskDuePresets'
import {
  Badge,
  Button,
  DateTimePicker,
  Dropdown,
  FeatherIcon,
  Tooltip,
  call,
  createResource,
  toast,
} from 'frappe-ui'
import Draggable from 'vuedraggable'
import { computed, h, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'

const { $socket } = globalStore()
const { statusOptions, getLeadStatus } = statusesStore()
const { user: sessionUser } = sessionStore()

// Whose board is on screen. Deliberately NOT persisted across reloads: the
// board is the list you work from, and silently reopening on a teammate's list
// is the one mistake here that costs real calls. Switching is one click.
const ALL_OWNERS = 'all'
const selectedOwner = ref(sessionUser)
const refreshing = ref(false)
const syncStatus = ref('')
const syncStatusTheme = ref('green')
const selectedStatus = ref('')
const selectedPriority = ref('')
const selectedSignal = ref('')
const selectedItem = ref(null)
const showLeadModal = ref(false)
const savingStatusLeads = ref([])
const pendingLeadStatus = ref(null)
const showLostReasonModal = ref(false)
const selectedAddress = ref('')
const showPropertyLinkModal = ref(false)
const showReportModal = ref(false)
const showPriorityModal = ref(false)
const savingPriority = ref(false)
const togglingTasks = ref([])
const selectedTextItem = ref(null)
const showTextModal = ref(false)
// The card waiting on an answer before its state is written. Holds the
// destination column when the move came from a drag, because vuedraggable has
// already moved the card and a cancel has to put it back.
const pendingOutcome = ref(null)
const showOutcomeModal = ref(false)
const savingOutcome = ref(false)
// Bulk hand-over. Selection is by CARD name (that is what the user clicks and
// what the board renders), and collapsed to leads only at the moment it matters,
// because ownership is a property of the lead and a lead can own two cards.
const selecting = ref(false)
const selectedNames = ref([])
const assigning = ref(false)

const CheckIcon = () =>
  h('svg', { viewBox: '0 0 20 20', fill: 'none', stroke: 'currentColor', 'stroke-width': '2' }, [
    h('path', { d: 'M4 10.5l4 4 8-8', 'stroke-linecap': 'round', 'stroke-linejoin': 'round' }),
  ])
const BanIcon = () =>
  h('svg', { viewBox: '0 0 20 20', fill: 'none', stroke: 'currentColor', 'stroke-width': '2' }, [
    h('circle', { cx: '10', cy: '10', r: '7' }),
    h('path', { d: 'M5 5l10 10', 'stroke-linecap': 'round' }),
  ])
const UndoIcon = () =>
  h('svg', { viewBox: '0 0 20 20', fill: 'none', stroke: 'currentColor', 'stroke-width': '2' }, [
    h('path', {
      d: 'M8 5L4 9l4 4M4 9h8a4 4 0 010 8h-1',
      'stroke-linecap': 'round',
      'stroke-linejoin': 'round',
    }),
  ])

// The board generates two kinds of card since 2026-09-09: a task due today
// (how the sequences hand work to a rep) and a deal in flight with nothing
// booked. The call-ladder keys below are kept ONLY so cards created before
// that keep their labels; the Priority modal takes its list from the server.
const PHASE = {
  task: { label: 'Task due', theme: 'green', defaultOrder: 0 },
  // frappe-ui's Badge has no purple theme; override the subtle-gray classes so
  // it reads as its own thing next to the green task chip.
  closer: {
    label: 'Deal in flight',
    theme: 'gray',
    badgeClass: '!bg-purple-100 !text-purple-700',
    defaultOrder: 1,
  },
  // live lead, no sequence driving it, no dated next step — daily until booked
  nudge: { label: 'No next step', theme: 'orange', defaultOrder: 2 },
  never: { label: 'Never called', theme: 'red', defaultOrder: 3 },
  week1_am: { label: 'Week 1 · morning', theme: 'orange', defaultOrder: 3 },
  week1_pm: { label: 'Week 1 · afternoon', theme: 'orange', defaultOrder: 4 },
  weekly: { label: 'Weekly', theme: 'blue', defaultOrder: 5 },
  monthly: { label: 'Monthly', theme: 'gray', defaultOrder: 6 },
}
const DEFAULT_PRIORITY_ORDER = ['task', 'closer', 'nudge']

const board = createResource({
  url: 'crm.api.today_board.get_today_board',
  auto: true,
  cache: 'today_board',
})
const todayReport = createResource({
  url: 'crm.api.today_board.get_today_report',
  auto: true,
  cache: 'today_report',
})

const columns = ref([])
function syncColumns() {
  columns.value = (board.data?.columns || []).map((c) => ({
    state: c.state,
    items: [...(c.items || [])],
  }))
}
watch(() => board.data, syncColumns, { immediate: true, deep: false })

const toCallItems = computed(
  () => columns.value.find((c) => c.state === 'To Call')?.items || [],
)
const toCallCount = computed(() => toCallItems.value.length)
const toCallLeadCount = computed(
  () => new Set(toCallItems.value.map((item) => item.lead)).size,
)
const callsOwed = computed(() => toCallCount.value)
const boardCardCount = computed(() =>
  columns.value.reduce((total, col) => total + col.items.length, 0),
)
const streakLabel = computed(() => {
  const days = todayReport.data?.streak?.current || 0
  return `🔥 ${__('Team')} · ${days} ${days === 1 ? __('day') : __('days')}`
})
const activeFilterCount = computed(
  () => [selectedStatus.value, selectedPriority.value, selectedSignal.value].filter(Boolean).length,
)
const priorityItems = computed(() => {
  const order = board.data?.priority_order || DEFAULT_PRIORITY_ORDER
  return order.map((key) => ({ key, ...PHASE[key] })).filter((item) => item.label)
})
const filterOptions = computed(() => {
  const statusCounts = board.data?.status_counts || []
  return [
    {
      group: __('Lead status'),
      items: [
        {
          label: __('All statuses'),
          icon: selectedStatus.value ? null : 'check',
          onClick: () => setFilter('status', ''),
        },
        ...statusCounts.map((row) => ({
          label: `${row.status} (${row.count})`,
          icon: selectedStatus.value === row.status ? 'check' : null,
          onClick: () => setFilter('status', row.status),
        })),
      ],
    },
    {
      group: __('Priority'),
      items: [
        {
          label: __('All priorities'),
          icon: selectedPriority.value ? null : 'check',
          onClick: () => setFilter('priority', ''),
        },
        ...priorityItems.value.map((item) => ({
          label: __(item.label),
          icon: selectedPriority.value === item.key ? 'check' : null,
          onClick: () => setFilter('priority', item.key),
        })),
      ],
    },
    {
      group: __('Signals'),
      items: [
        {
          label: __('All cards'),
          icon: selectedSignal.value ? null : 'check',
          onClick: () => setFilter('signal', ''),
        },
        {
          label: __('Texted us'),
          icon: selectedSignal.value === 'incoming' ? 'check' : null,
          onClick: () => setFilter('signal', 'incoming'),
        },
        {
          label: __('Has an open task'),
          icon: selectedSignal.value === 'task' ? 'check' : null,
          onClick: () => setFilter('signal', 'task'),
        },
        {
          label: __('In cadence'),
          icon: selectedSignal.value === 'cadence' ? 'check' : null,
          onClick: () => setFilter('signal', 'cadence'),
        },
        {
          label: __('Task, not cadence'),
          icon: selectedSignal.value === 'task_only' ? 'check' : null,
          onClick: () => setFilter('signal', 'task_only'),
        },
        {
          label: __('Cadence, no task'),
          icon: selectedSignal.value === 'cadence_only' ? 'check' : null,
          onClick: () => setFilter('signal', 'cadence_only'),
        },
      ],
    },
  ]
})
const prettyDate = computed(() => {
  if (!board.data?.date) return ''
  return new Date(board.data.date + 'T00:00:00').toLocaleDateString(undefined, {
    weekday: 'short',
    month: 'short',
    day: 'numeric',
  })
})
const textReferenceDoc = computed(() => ({
  name: selectedTextItem.value?.lead,
  lead_name: selectedTextItem.value?.lead_name,
  mobile_no: selectedTextItem.value?.mobile_no,
  phone: selectedTextItem.value?.mobile_no,
}))

function dotClass(state) {
  return {
    'To Call': 'bg-surface-blue-3',
    Done: 'bg-surface-green-3',
    Skipped: 'bg-surface-gray-4',
  }[state]
}

function reloadWithFilters() {
  board.params = {
    owner: selectedOwner.value,
    ...(selectedStatus.value ? { status: selectedStatus.value } : {}),
    ...(selectedPriority.value ? { priority: selectedPriority.value } : {}),
    ...(selectedSignal.value ? { signal: selectedSignal.value } : {}),
  }
  board.reload()
}

function setFilter(type, value) {
  if (type === 'status') selectedStatus.value = value
  if (type === 'priority') selectedPriority.value = value
  if (type === 'signal') selectedSignal.value = value
  reloadWithFilters()
}

function setOwner(owner) {
  if (owner === selectedOwner.value) return
  selectedOwner.value = owner
  reloadWithFilters()
  // The report panel has to follow the board, or its progress bar describes a
  // card set that isn't the one on screen.
  todayReport.params = { owner: owner }
  todayReport.reload()
}

const ownerLabel = computed(() => {
  if (selectedOwner.value === ALL_OWNERS) return __('The whole team')
  const match = (board.data?.owners || []).find(
    (o) => o.user === selectedOwner.value,
  )
  if (match) return match.full_name
  return selectedOwner.value === sessionUser ? __('My leads') : selectedOwner.value
})

const viewingOwnBoard = computed(() => selectedOwner.value === sessionUser)
const viewingTeamBoard = computed(() => selectedOwner.value === ALL_OWNERS)
const showOwnerOnCard = computed(() => viewingTeamBoard.value || !viewingOwnBoard.value)

function ownerInitials(item) {
  const name = (item.owner_name || '').trim()
  const parts = name.split(/\s+/).filter(Boolean)
  if (parts.length >= 2) {
    return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase()
  }
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase()
  const email = (item.lead_owner || '').trim()
  if (email) return email.slice(0, 2).toUpperCase()
  return '?'
}

const ownerOptions = computed(() => {
  const owners = board.data?.owners || []
  const mine = owners.find((o) => o.user === sessionUser)
  const teamCount = owners.reduce((n, o) => n + (o.count || 0), 0)
  const options = [
    {
      label: `${__('The whole team')}${teamCount ? ` (${teamCount})` : ''}`,
      icon: viewingTeamBoard.value ? 'check' : null,
      onClick: () => setOwner(ALL_OWNERS),
    },
    {
      label: `${__('My leads')}${mine ? ` (${mine.count})` : ''}`,
      icon: viewingOwnBoard.value ? 'check' : null,
      onClick: () => setOwner(sessionUser),
    },
  ]
  const others = owners.filter((o) => o.user !== sessionUser)
  if (others.length) {
    options.push({
      group: __('Other boards'),
      items: others.map((o) => ({
        label: `${o.full_name} (${o.count})`,
        icon: selectedOwner.value === o.user ? 'check' : null,
        onClick: () => setOwner(o.user),
      })),
    })
  }
  return options
})

const allCards = computed(() => columns.value.flatMap((col) => col.items))
const selectedCards = computed(() =>
  allCards.value.filter((item) => selectedNames.value.includes(item.name)),
)
const selectedLeads = computed(() => [
  ...new Set(selectedCards.value.map((item) => item.lead)),
])
const selectedLeadCount = computed(() => selectedLeads.value.length)
const selectionSummary = computed(() => {
  const n = selectedLeadCount.value
  if (!n) return __('Pick the cards you want to hand over')
  return n === 1 ? __('1 lead selected') : __('{0} leads selected', [n])
})

const assigneeOptions = computed(() => {
  const people = board.data?.assignees || []
  if (!people.length) return [{ label: __('No one to assign to'), onClick: () => {} }]
  return people.map((person) => ({
    label: person.full_name,
    onClick: () => assignCards(selectedNames.value, person.user),
  }))
})

function cardAssigneeOptions(item) {
  const people = board.data?.assignees || []
  if (!people.length) return [{ label: __('No one to assign to'), onClick: () => {} }]
  return people.map((person) => ({
    label: person.full_name,
    icon: person.user === item.lead_owner ? 'check' : null,
    onClick: () => assignCards([item.name], person.user),
  }))
}

function isSelected(item) {
  return selectedNames.value.includes(item.name)
}

function toggleCard(item) {
  selectedNames.value = isSelected(item)
    ? selectedNames.value.filter((name) => name !== item.name)
    : [...selectedNames.value, item.name]
}

function clearSelection() {
  selectedNames.value = []
}

function toggleSelecting() {
  selecting.value = !selecting.value
  // Leaving selection mode drops the picks. Keeping them would mean a stale set
  // is still armed the next time the mode is entered, and the rep would be one
  // click from handing over cards they chose minutes ago for another reason.
  if (!selecting.value) clearSelection()
}

async function assignCards(names, owner) {
  if (!owner || !names?.length || assigning.value) return
  assigning.value = true
  try {
    const r = await call('crm.api.today_board.assign_today_leads', {
      items: names,
      owner,
    })
    const who =
      (board.data?.assignees || []).find((p) => p.user === owner)?.full_name || owner
    // Report what actually happened rather than what was asked for: a lead the
    // target already owned is not a move, and a lead that could not be saved has
    // to be named or the rep will believe it went across.
    if (r.moved) {
      toast.success(
        r.moved === 1
          ? __('1 lead moved to {0}', [who])
          : __('{0} leads moved to {1}', [r.moved, who]),
      )
    } else if (r.skipped && !r.failed?.length) {
      toast.success(__('Already owned by {0}', [who]))
    }
    if (r.failed?.length) {
      toast.error(
        __('Could not move {0}: {1}', [
          r.failed.map((f) => f.lead_name).join(', '),
          r.failed[0].error,
        ]),
      )
    }
    clearSelection()
    await Promise.all([board.reload(), todayReport.reload()])
  } catch (e) {
    toast.error(e.messages?.[0] || __('Could not assign these leads'))
  } finally {
    assigning.value = false
  }
}

function openTodayItem(item) {
  selectedItem.value = item
  showLeadModal.value = true
}

function leadStatusOptions(item) {
  return statusOptions('lead', [], (status) => chooseLeadStatus(item, status))
}

function chooseLeadStatus(item, status) {
  if (!item || !status || status === item.lead_status) return
  if (getLeadStatus(status)?.type === 'Lost') {
    pendingLeadStatus.value = { item, status }
    showLostReasonModal.value = true
    return
  }
  updateLeadStatus(item, status)
}

function confirmLostStatus(values) {
  const pending = pendingLeadStatus.value
  pendingLeadStatus.value = null
  if (!pending) return
  updateLeadStatus(pending.item, pending.status, values)
}

function cancelLostStatus() {
  pendingLeadStatus.value = null
}

async function updateLeadStatus(item, status, extraValues = {}) {
  if (savingStatusLeads.value.includes(item.lead)) return
  const oldStatus = item.lead_status
  savingStatusLeads.value = [...savingStatusLeads.value, item.lead]

  // A lead can have two independently actionable call cards. Keep both badges
  // in step while the save is in flight rather than showing conflicting states.
  for (const col of columns.value) {
    for (const card of col.items) {
      if (card.lead === item.lead) card.lead_status = status
    }
  }

  try {
    await call('crm.api.today_board.set_today_lead_status', {
      item: item.name,
      status,
      ...extraValues,
    })
    await board.reload()
  } catch (e) {
    for (const col of columns.value) {
      for (const card of col.items) {
        if (card.lead === item.lead) card.lead_status = oldStatus
      }
    }
    toast.error(e.messages?.[0] || __('Could not update lead status'))
    board.reload()
  } finally {
    savingStatusLeads.value = savingStatusLeads.value.filter(
      (lead) => lead !== item.lead,
    )
  }
}

function openTodayReport() {
  showReportModal.value = true
  todayReport.reload()
}

function openAddress(address) {
  if (!address) return
  selectedAddress.value = address
  showPropertyLinkModal.value = true
}

function onLeadAddressUpdated({ lead, address, zillow_unresolved } = {}) {
  if (!lead) return
  const patch = {}
  if (address != null) patch.address = address
  if (zillow_unresolved != null) patch.zillow_unresolved = zillow_unresolved
  for (const col of columns.value) {
    for (const item of col.items || []) {
      if (item.lead === lead) Object.assign(item, patch)
    }
  }
  if (selectedItem.value?.lead === lead) {
    selectedItem.value = { ...selectedItem.value, ...patch }
  }
}

// Tick a task straight off the card, in either direction. Reopening matters as
// much as completing: the checkbox is one pixel from the row that opens the
// task, so a mis-click has to be undoable without hunting for the lead.
function cardTasks(item) {
  if (item.tasks?.length) return item.tasks
  return item.task ? [item.task] : []
}

async function toggleTask(item, task) {
  task = task || item.task
  if (!task || togglingTasks.value.includes(task.name)) return
  const wasCompleted = task.is_completed
  togglingTasks.value = [...togglingTasks.value, task.name]
  task.is_completed = !wasCompleted
  try {
    await call('frappe.client.set_value', {
      doctype: 'CRM Task',
      name: task.name,
      fieldname: 'status',
      value: wasCompleted ? 'Todo' : 'Done',
    })
    await board.reload()
  } catch (e) {
    task.is_completed = wasCompleted
    toast.error(e.messages?.[0] || __('Could not update the task'))
    board.reload()
  } finally {
    togglingTasks.value = togglingTasks.value.filter((name) => name !== task.name)
  }
}

// ---- Follow-up strip -------------------------------------------------------
// Chips are the rep's own (shared with the lead-page composer). Creation goes
// through the same CRM Task insert the to-do block uses, so `crm_task_update`
// fires and every other open surface — the lead page, the kanban badge — sees
// it. The card is patched optimistically and the board reloaded for the
// canonical row (has_task, priority), so the strip gives way to the task row
// without waiting on the round trip.
const { presets: duePresets } = useTaskDuePresets()
const addingTaskFor = ref([])
const composerFor = ref(null)
const composerTitle = ref('')
const composerDue = ref('')
const composerPreset = ref(-1)
const composerPicker = ref(false)
const composerInput = ref(null)

function showFollowUpStrip(item) {
  return item.state === 'To Call' && !item.has_task && !selecting.value
}

function presetTooltip(f) {
  return __('Follow up') + ' · ' + formatDate(dueFromPreset(f), 'ddd, MMM D, YYYY | hh:mm a')
}

function composerChoiceClass(selected) {
  return selected
    ? 'border-outline-gray-3 bg-surface-gray-3 font-medium text-ink-gray-8'
    : 'border-outline-gray-1 bg-surface-white text-ink-gray-6 hover:bg-surface-gray-2'
}

function openComposer(item, { picker = false } = {}) {
  composerFor.value = item.name
  composerTitle.value = ''
  composerDue.value = ''
  composerPreset.value = -1
  composerPicker.value = picker
  nextTick(() => {
    const el = composerInput.value
    const input = Array.isArray(el) ? el[0] : el
    if (!picker) input?.focus?.()
  })
}

function closeComposer() {
  composerFor.value = null
  composerTitle.value = ''
  composerDue.value = ''
  composerPreset.value = -1
  composerPicker.value = false
}

function pickComposerPreset(f, i) {
  composerPreset.value = i
  composerDue.value = formatDueStamp(dueFromPreset(f))
  composerPicker.value = false
}

async function quickFollowUp(item, preset) {
  const due = formatDueStamp(dueFromPreset(preset))
  await createFollowUp(item, __('Follow up'), due)
}

async function submitComposer(item) {
  // Blank title = "Follow up", the same shorthand the chips use; the composer
  // exists for the WHEN (a picked date) as much as for the WHAT.
  const title = composerTitle.value.trim() || __('Follow up')
  const due = snapMidnightToMorning(composerDue.value)
  const ok = await createFollowUp(item, title, due)
  if (ok) closeComposer()
}

async function createFollowUp(item, title, due) {
  if (addingTaskFor.value.includes(item.name)) return false
  addingTaskFor.value = [...addingTaskFor.value, item.name]
  try {
    const doc = await call('frappe.client.insert', {
      doc: {
        doctype: 'CRM Task',
        title,
        status: 'Todo',
        due_date: due || null,
        reference_doctype: 'CRM Lead',
        reference_docname: item.lead,
        assigned_to: sessionUser,
      },
    })
    const task = {
      name: doc?.name,
      title,
      due_date: due || null,
      is_completed: false,
    }
    // A lead can hold two call cards; both should show the new task.
    for (const col of columns.value) {
      for (const card of col.items) {
        if (card.lead === item.lead && !card.has_task) {
          card.task = task
          card.has_task = true
        }
      }
    }
    toast.success(
      due
        ? __('{0} · {1}', [title, formatDate(due, 'ddd, MMM D | hh:mm a')])
        : __('{0} added', [title]),
    )
    board.reload()
    return true
  } catch (e) {
    toast.error(e.messages?.[0] || __('Could not add the task'))
    return false
  } finally {
    addingTaskFor.value = addingTaskFor.value.filter((n) => n !== item.name)
  }
}

function openText(item) {
  selectedTextItem.value = item
  showTextModal.value = true
}

function finishTextItem() {
  if (selectedTextItem.value) requestState(selectedTextItem.value, 'Done')
}

function skipTextItem() {
  if (selectedTextItem.value) requestState(selectedTextItem.value, 'Skipped')
}

// Resolving a card asks what happened first. Putting one BACK doesn't: undoing a
// mis-click is not a judgement worth interrogating, and a prompt there would
// make the mistake more expensive than the action.
function requestState(item, state) {
  if (state === 'To Call') {
    applyState(item, state)
    return
  }
  pendingOutcome.value = { item, state, col: null, viaDrag: false }
  showOutcomeModal.value = true
}

function onChange(evt, col) {
  if (evt.moved) {
    persistOrder(col)
    return
  }
  if (!evt.added) return
  const item = evt.added.element
  if (item.state === col.state) {
    persistOrder(col)
    return
  }
  if (col.state === 'To Call') {
    applyState(item, 'To Call', { col, alreadyMoved: true })
    return
  }
  pendingOutcome.value = { item, state: col.state, col, viaDrag: true }
  showOutcomeModal.value = true
}

async function confirmOutcome({ outcome, outcome_note }) {
  const pending = pendingOutcome.value
  if (!pending) return
  pendingOutcome.value = null
  savingOutcome.value = true
  try {
    await applyState(pending.item, pending.state, {
      outcome,
      outcome_note,
      col: pending.col,
      alreadyMoved: pending.viaDrag,
    })
  } finally {
    savingOutcome.value = false
    showOutcomeModal.value = false
  }
}

// Esc, the backdrop and Cancel all land here. A dragged card is sitting in the
// wrong column until the server is re-read, so abandoning the answer has to put
// it back rather than leave the board lying.
watch(showOutcomeModal, (open) => {
  if (open) return
  const pending = pendingOutcome.value
  pendingOutcome.value = null
  if (pending?.viaDrag) board.reload()
})

async function applyState(item, state, options = {}) {
  const { outcome = '', outcome_note = '', col = null, alreadyMoved = false } = options
  const prev = item.state
  item.state = state
  if (!alreadyMoved) {
    const from = columns.value.find((c) => c.state === prev)
    const to = columns.value.find((c) => c.state === state)
    if (from && to) {
      const i = from.items.findIndex((x) => x.name === item.name)
      if (i > -1) from.items.splice(i, 1)
      to.items.unshift(item)
    }
  }
  item.outcome = outcome
  item.outcome_note = outcome_note
  try {
    await call('crm.api.today_board.set_today_state', {
      item: item.name,
      state,
      outcome,
      outcome_note,
    })
    if (col) await persistOrder(col)
  } catch (e) {
    toast.error(e.messages?.[0] || __('Could not update'))
    board.reload()
  }
}

async function persistOrder(col) {
  try {
    await call('crm.api.today_board.reorder_today', {
      order: col.items.map((i) => i.name),
      state: col.state,
    })
    col.items.forEach((i) => (i.state = col.state))
  } catch (e) {
    toast.error(e.messages?.[0] || __('Could not reorder'))
    board.reload()
  }
}

async function savePriorityOrder(order) {
  savingPriority.value = true
  try {
    await call('crm.api.today_board.set_today_priority_order', { order })
    showPriorityModal.value = false
    await board.reload()
    toast.success(__('Priority order saved'))
  } catch (e) {
    toast.error(e.messages?.[0] || __('Could not save priority order'))
  } finally {
    savingPriority.value = false
  }
}

async function syncList() {
  refreshing.value = true
  syncStatus.value = ''
  try {
    const r = await call('crm.api.today_board.generate_today')
    await Promise.all([board.reload(), todayReport.reload()])
    // `closed` has to be said out loud: after 4pm the sync deliberately adds
    // nothing, and "List is up to date" would read as a lie to anyone who knows
    // a lead just came in.
    // Kept short: this renders as a Badge in an already-crowded header row.
    const message = r.closed
      ? __('Closed for today — new leads go on tomorrow’s list')
      : r.created
        ? __('Added {0} new call(s)', [r.created])
        : __('List is up to date')
    syncStatusTheme.value = r.closed ? 'orange' : 'green'
    syncStatus.value = message
    toast.success(message)
  } catch (e) {
    const message = e.messages?.[0] || __('Could not sync the list')
    syncStatusTheme.value = 'red'
    syncStatus.value = __('Sync failed')
    toast.error(message)
  } finally {
    refreshing.value = false
  }
}

function onRealtime() {
  board.reload()
  todayReport.reload()
}
onMounted(() => $socket.on('crm_today', onRealtime))
onBeforeUnmount(() => {
  $socket.off('crm_today', onRealtime)
})
</script>

<style scoped>
/* The strip has to fit ONE line in a ~245px card (three columns at 1200px with
   the nav open) and still read as a sentence in a wide one. The words are what
   give way: the clock icon and the chips are the control; "Follow up" / "other"
   are captions (kept as titles). Container query, not a viewport breakpoint —
   column width depends on how many columns are open, not on the window. */
.followup-strip {
  container-type: inline-size;
}
@container (max-width: 270px) {
  .followup-strip .strip-label {
    display: none;
  }
}
</style>
