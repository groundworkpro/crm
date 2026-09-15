<template>
  <ActivityHeader
    v-model="tabIndex"
    v-model:showWhatsappTemplates="showWhatsappTemplates"
    v-model:showFilesUploader="showFilesUploader"
    v-model:emailBox="emailBox"
    :tabs="tabs"
    :title="title"
    :doc="doc"
    :whatsappBox="whatsappBox"
    :modalRef="modalRef"
  />
  <!-- lost-lead banner: lost reason lives here, not in the side panel -->
  <div
    v-if="title == 'Activity' && isLostLead"
    class="mx-3 mt-3 flex items-center justify-between gap-2 rounded-lg border border-outline-red-1 bg-surface-red-1 px-4 py-2 sm:mx-10"
  >
    <div class="flex min-w-0 flex-col gap-1 text-base text-ink-red-3">
      <div>
        <span class="font-medium">{{ __(doc.status) }}</span>
        <template v-if="doc.lost_reason"> &middot; {{ doc.lost_reason }}</template>
        <span v-if="doc.lost_notes" class="text-ink-gray-7">
          — {{ doc.lost_notes }}
        </span>
      </div>
      <div
        v-if="isRefundPoolLead"
        class="flex flex-wrap items-center gap-4 text-ink-gray-7"
      >
        <FormControl
          type="checkbox"
          :label="__('Refundable')"
          :modelValue="!!doc.custom_refundable"
          @update:modelValue="(v) => toggleLeadFlag('custom_refundable', v)"
        />
        <FormControl
          type="checkbox"
          :label="__('Refund requested')"
          :modelValue="!!doc.custom_refund_requested"
          @update:modelValue="(v) => toggleLeadFlag('custom_refund_requested', v)"
        />
      </div>
    </div>
    <Button :label="__('Edit')" @click="showLostReasonModal = true" />
  </div>
  <LostReasonModal
    v-if="showLostReasonModal"
    v-model="showLostReasonModal"
    :doctype="doctype"
    :document="_document"
  />
  <FadedScrollableDiv class="flex flex-col h-full overflow-y-auto">
    <div
      v-if="all_activities?.loading"
      class="flex flex-1 flex-col items-center justify-center gap-3 text-xl font-medium text-ink-gray-4"
    >
      <LoadingIndicator class="h-6 w-6" />
      <span>{{ __('Loading...') }}</span>
    </div>
    <div
      v-else-if="
        activities?.length ||
        (whatsappMessages.data?.length && title == 'WhatsApp') ||
        (smsMessages.data?.length && title == 'SMS') ||
        title == 'Activity'
      "
      class="activities"
    >
      <DispoHeaderBlock
        v-if="title == 'Activity' && doctype == 'CRM Lead' && doc.il_property_id"
        :lead="props.docname"
      />
      <TaskTodoList
        v-if="title == 'Activity'"
        :tasks="openTasks"
        :modalRef="modalRef"
      />
      <QuickComments v-if="title == 'Activity'" :modalRef="modalRef" />
      <div v-if="title == 'WhatsApp' && whatsappMessages.data?.length">
        <WhatsAppArea
          v-model="whatsappMessages"
          v-model:reply="replyMessage"
          class="px-3 sm:px-10"
          :messages="whatsappMessages.data"
        />
      </div>
      <div v-else-if="title == 'SMS' && smsMessages.data?.length">
        <SMSArea
          class="px-3 pt-3 sm:px-10"
          :messages="smsMessages.data"
          :contactName="doc.lead_name || doc.first_name"
          :contactImage="doc.image"
          @reload="smsMessages.reload()"
        />
      </div>
      <div
        v-else-if="title == 'Notes'"
        class="grid grid-cols-1 gap-4 px-3 pb-3 sm:px-10 sm:pb-5 lg:grid-cols-2 xl:grid-cols-3"
      >
        <div
          v-for="note in activities"
          :key="note.name"
          @click="modalRef.showNote(note)"
        >
          <NoteArea v-model="all_activities" :note="note" />
        </div>
      </div>
      <div v-else-if="title == 'Comments'" class="pb-5">
        <div v-for="(comment, i) in activities" :key="comment.name">
          <div
            v-if="isNewDay(i)"
            class="flex items-center gap-2.5 px-3 pb-3 sm:px-10"
            :class="i == 0 ? 'pt-1' : 'pt-4'"
          >
            <span
              class="text-2xs font-medium uppercase tracking-wider text-ink-gray-4"
            >
              {{ dayLabel(comment.creation) }}
            </span>
            <div class="h-px flex-1 bg-outline-gray-modals" />
          </div>
          <div
            class="activity grid grid-cols-[30px_minmax(auto,_1fr)] gap-2 px-3 sm:gap-4 sm:px-10"
          >
            <div
              class="z-0 relative flex justify-center before:absolute before:left-[50%] before:-z-[1] before:top-0 before:border-l before:border-outline-gray-modals"
              :class="
                i != activities.length - 1 ? 'before:h-full' : 'before:h-4'
              "
            >
              <div
                class="flex h-8 w-8 items-start justify-center bg-surface-white pt-0.5"
              >
                <TimelineAvatar v-bind="railProps(comment)" />
              </div>
            </div>
            <CommentArea
              class="mb-4"
              v-model="all_activities"
              :activity="comment"
              :docname="docname"
            />
          </div>
        </div>
      </div>
      <div v-else-if="title == 'Tasks'" class="px-3 pb-3 sm:px-10 sm:pb-5">
        <TaskArea :modalRef="modalRef" :tasks="activities" :doctype="doctype" />
      </div>
      <div v-else-if="title == 'Calls'" class="activity">
        <div v-for="(call, i) in activities" :key="call.name">
          <div
            v-if="isNewDay(i)"
            class="flex items-center gap-2.5 px-3 pb-3 sm:px-10"
            :class="i == 0 ? 'pt-1' : 'pt-4'"
          >
            <span
              class="text-2xs font-medium uppercase tracking-wider text-ink-gray-4"
            >
              {{ dayLabel(call.creation) }}
            </span>
            <div class="h-px flex-1 bg-outline-gray-modals" />
          </div>
          <div
            class="activity grid grid-cols-[30px_minmax(auto,_1fr)] gap-4 px-3 sm:px-10"
          >
            <div
              class="z-0 relative flex justify-center before:absolute before:left-[50%] before:-z-[1] before:top-0 before:border-l before:border-outline-gray-modals"
              :class="
                i != activities.length - 1 ? 'before:h-full' : 'before:h-4'
              "
            >
              <div
                class="flex h-8 w-8 items-start justify-center bg-surface-white pt-0.5"
              >
                <TimelineAvatar v-bind="railProps(call)" />
              </div>
            </div>
            <CallArea class="mb-4" :activity="call" />
          </div>
        </div>
      </div>
      <div v-else-if="title == 'Emails'" class="px-3 pb-4 sm:px-10">
        <EmailThread
          v-if="emailMessages.length"
          :messages="emailMessages"
          @reply="replyToEmail"
        />
      </div>
      <div
        v-else-if="title == 'Attachments'"
        class="px-3 pb-3 sm:px-10 sm:pb-5"
      >
        <AttachmentArea
          :attachments="activities"
          @reload="all_activities.reload()"
        />
      </div>
      <template v-else>
        <template
          v-for="(activity, i) in feedItems"
          :key="activity.name"
        >
          <div
            v-if="isNewDay(i)"
            class="flex items-center gap-2.5 px-3 pb-3 sm:px-10"
            :class="i == 0 ? 'pt-1' : 'pt-4'"
          >
            <span
              class="text-2xs font-medium uppercase tracking-wider text-ink-gray-4"
            >
              {{ dayLabel(activity.creation) }}
            </span>
            <div class="h-px flex-1 bg-outline-gray-modals" />
          </div>
          <div
            class="activity px-3 sm:px-10"
            :class="
              ['Activity', 'Emails', 'Comments'].includes(title)
                ? 'grid grid-cols-[30px_minmax(auto,_1fr)] gap-2 sm:gap-4'
                : ''
            "
          >
          <div
            v-if="['Activity', 'Emails', 'Comments'].includes(title)"
            class="z-0 relative flex justify-center before:absolute before:left-[50%] before:-z-[1] before:top-0 before:border-l before:border-outline-gray-modals"
            :class="[
              i != feedItems.length - 1 ? 'before:h-full' : 'before:h-4',
            ]"
          >
            <div
              class="flex h-8 w-8 items-start justify-center bg-surface-white pt-0.5"
            >
              <TimelineAvatar v-bind="railProps(activity)" />
            </div>
          </div>
          <div
            v-if="activity.activity_type == 'email_thread'"
            class="pb-5 mt-px"
          >
            <EmailThread :messages="activity.messages" @reply="replyToEmail" />
          </div>
          <div
            v-else-if="activity.activity_type == 'communication'"
            class="pb-5 mt-px"
          >
            <EmailArea :activity="activity" :emailBox="emailBox" />
          </div>
          <div
            v-else-if="activity.activity_type == 'comment'"
            :id="activity.name"
            class="mb-4"
          >
            <CommentArea
              v-model="all_activities"
              :activity="activity"
              :docname="docname"
            />
          </div>
          <div
            v-else-if="activity.activity_type == 'attachment_log'"
            :id="activity.name"
            class="mb-4 flex flex-col gap-2 py-1.5"
          >
            <div class="flex items-center justify-stretch gap-2 text-xs">
              <div
                class="inline-flex items-center flex-wrap gap-1.5 text-ink-gray-8 font-medium"
              >
                <span class="font-medium">{{ activity.owner_name }}</span>
                <span class="text-ink-gray-5">{{
                  __(activity.data.type)
                }}</span>
                <a
                  v-if="activity.data.file_url"
                  :href="activity.data.file_url"
                  target="_blank"
                >
                  <span>{{ activity.data.file_name }}</span>
                </a>
                <span v-else>{{ activity.data.file_name }}</span>
                <FeatherIcon
                  v-if="activity.data.is_private"
                  name="lock"
                  class="size-3"
                />
              </div>
              <div class="ml-auto whitespace-nowrap">
                <Tooltip :text="formatDate(activity.creation)">
                  <div class="text-xs text-ink-gray-5">
                    {{ shortTime(activity.creation) }}
                  </div>
                </Tooltip>
              </div>
            </div>
          </div>
          <div
            v-else-if="
              activity.activity_type == 'incoming_call' ||
              activity.activity_type == 'outgoing_call'
            "
            class="mb-4"
          >
            <CallArea :activity="activity" />
          </div>
          <div
            v-else-if="
              activity.activity_type == 'incoming_text' ||
              activity.activity_type == 'outgoing_text'
            "
            class="mb-4 flex flex-col py-1.5"
            :class="
              activity.activity_type == 'outgoing_text'
                ? 'items-end'
                : 'items-start'
            "
          >
            <div
              class="mb-1 flex items-baseline gap-1 px-0.5 text-xs text-ink-gray-5"
            >
              <span class="font-medium text-ink-gray-7">
                {{
                  activity.activity_type == 'outgoing_text'
                    ? activity.sender_name || __('Sent')
                    : doc.lead_name || doc.first_name || __('Lead')
                }}
              </span>
              <template v-if="activity.status == 'scheduled'">
                <span>·</span>
                <span class="text-ink-blue-3">{{ __('Scheduled') }}</span>
              </template>
              <template v-else-if="activity.status == 'canceled'">
                <span>·</span>
                <span>{{ __('Not sent — lead closed') }}</span>
              </template>
              <span>·</span>
              <Tooltip :text="formatDate(activity.creation)">
                <span>{{ shortTime(activity.creation) }}</span>
              </Tooltip>
            </div>
            <div
              class="max-w-[78%] whitespace-pre-wrap rounded-lg px-2.5 py-1.5 text-sm"
              :class="
                activity.status == 'scheduled'
                  ? 'border border-dashed border-blue-400 bg-surface-blue-1 text-ink-gray-8'
                  : activity.activity_type == 'outgoing_text'
                    ? 'bg-blue-500 text-white'
                    : 'bg-surface-gray-2 text-ink-gray-8'
              "
            >
              <SMSMedia
                v-if="activity.media?.length"
                :media="activity.media"
                :class="activity.message ? 'mb-1.5' : ''"
              />
              <span v-if="activity.message">{{ activity.message }}</span>
            </div>
          </div>
          <div
            v-else-if="activity.activity_type == 'task'"
            class="mb-4 flex cursor-pointer items-center gap-2 py-1.5 text-xs"
            @click="modalRef.showTask(activity.task)"
          >
            <span class="truncate text-ink-gray-5 line-through">
              {{ activity.task.title }}
            </span>
            <span class="whitespace-nowrap text-ink-gray-5">
              {{
                activity.task.status == 'Done' ? __('completed') : __('canceled')
              }}
            </span>
            <Tooltip :text="formatDate(activity.creation)" class="ml-auto">
              <div class="whitespace-nowrap text-xs text-ink-gray-5">
                {{ shortTime(activity.creation) }}
              </div>
            </Tooltip>
          </div>
          <div
            v-else-if="activity.activity_type == 'tax_pull'"
            class="mb-4 flex flex-col gap-1.5 py-1.5 text-xs"
          >
            <div class="flex items-center gap-1.5">
              <span class="font-medium text-ink-gray-8">{{
                activity.pull.pulled_by_name || activity.pull.pulled_by
              }}</span>
              <span class="text-ink-gray-5">{{ __('fetched tax info') }}</span>
              <span class="text-ink-gray-4">·</span>
              <span class="text-ink-gray-5">${{ Number(activity.pull.cost || 0.22).toFixed(2) }}</span>
              <Tooltip :text="formatDate(activity.creation)" class="ml-auto">
                <div class="whitespace-nowrap text-xs text-ink-gray-5">
                  {{ shortTime(activity.creation) }}
                </div>
              </Tooltip>
            </div>
            <div
              class="flex flex-col gap-1 rounded-md bg-surface-gray-2 px-2.5 py-2 text-sm"
            >
              <template v-if="activity.pull.matched">
                <div v-if="activity.pull.owner_name" class="flex gap-1.5">
                  <span class="shrink-0 text-ink-gray-5">{{ __('Owner') }}</span>
                  <span class="text-ink-gray-8">{{
                    activity.pull.owner_name
                  }}</span>
                </div>
                <div v-if="activity.pull.apn" class="flex gap-1.5">
                  <span class="shrink-0 text-ink-gray-5">{{ __('APN') }}</span>
                  <span class="text-ink-gray-8">{{ activity.pull.apn }}</span>
                </div>
                <div v-if="activity.pull.tax_status" class="flex gap-1.5">
                  <span class="shrink-0 text-ink-gray-5">{{
                    __('Tax status')
                  }}</span>
                  <span
                    :class="
                      activity.pull.tax_default || activity.pull.tax_delinquent_year
                        ? 'font-medium text-ink-red-3'
                        : 'text-ink-gray-8'
                    "
                    >{{ activity.pull.tax_status }}</span
                  >
                </div>
                <div v-if="activity.pull.annual_tax" class="flex gap-1.5">
                  <span class="shrink-0 text-ink-gray-5">{{
                    __('Annual tax')
                  }}</span>
                  <span class="text-ink-gray-8"
                    >${{ formatNumber(activity.pull.annual_tax) }}</span
                  >
                </div>
                <div v-if="activity.pull.assessed_value" class="flex gap-1.5">
                  <span class="shrink-0 text-ink-gray-5">{{
                    __('Assessed')
                  }}</span>
                  <span class="text-ink-gray-8"
                    >${{ formatNumber(activity.pull.assessed_value) }}</span
                  >
                </div>
              </template>
              <span v-else class="text-ink-gray-5">{{
                __('No property match found for this address.')
              }}</span>
            </div>
          </div>
          <div
            v-else-if="activity.activity_type == 'agreement'"
            class="mb-4 flex flex-col gap-1.5 py-1.5 text-xs"
          >
            <div class="flex items-center gap-1.5">
              <span class="font-medium text-ink-gray-8">{{
                activity.agreement.created_by_name || activity.agreement.owner
              }}</span>
              <span class="text-ink-gray-5">{{
                isTerminationAgreement(activity.agreement)
                  ? __('created a unilateral termination notice')
                  : __('created a purchase agreement')
              }}</span>
              <Tooltip :text="formatDate(activity.creation)" class="ml-auto">
                <div class="whitespace-nowrap text-xs text-ink-gray-5">
                  {{ shortTime(activity.creation) }}
                </div>
              </Tooltip>
            </div>
            <div
              class="flex flex-col gap-1.5 rounded-md bg-surface-gray-2 px-2.5 py-2 text-sm"
            >
              <div class="flex items-center justify-between gap-2">
                <span class="truncate text-ink-gray-8">{{
                  activity.agreement.template_title
                }}</span>
                <span class="shrink-0 text-xs text-ink-gray-5">
                  {{ activity.agreement.signed_count || 0 }}/{{
                    activity.agreement.total_signers || 0
                  }}
                  {{ __('signed') }}
                </span>
              </div>
              <div class="flex items-center gap-3">
                <button
                  class="self-start text-xs text-ink-gray-6 underline hover:text-ink-gray-9"
                  @click="openAgreementLink(activity.agreement.buyer_link)"
                >
                  {{
                    isTerminationAgreement(activity.agreement)
                      ? __('Open company representative link')
                      : __('Open buyer link')
                  }}
                </button>
                <a
                  v-if="activity.agreement.is_signed"
                  :href="signedAgreementUrl(activity.agreement)"
                  target="_blank"
                  rel="noopener"
                  class="flex items-center gap-1 self-start text-xs font-medium text-ink-green-3 hover:text-ink-green-2"
                >
                  <FeatherIcon name="external-link" class="size-3" />
                  {{ __('Open signed PDF') }}
                </a>
              </div>
            </div>
          </div>
          <div
            v-else-if="activity.activity_type == 'underwriting'"
            class="mb-4 flex flex-col gap-1.5 py-1.5 text-xs"
          >
            <div class="flex items-center gap-1.5">
              <span class="font-medium text-ink-gray-8">{{
                activity.workbook.created_by_name ||
                activity.workbook.created_by_user
              }}</span>
              <span class="text-ink-gray-5">{{
                __('created an underwriting sheet')
              }}</span>
              <Tooltip :text="formatDate(activity.creation)" class="ml-auto">
                <div class="whitespace-nowrap text-xs text-ink-gray-5">
                  {{ shortTime(activity.creation) }}
                </div>
              </Tooltip>
            </div>
            <div
              class="flex flex-col gap-1 rounded-md bg-surface-gray-2 px-2.5 py-2 text-sm"
            >
              <span class="truncate text-ink-gray-8">{{
                activity.workbook.address || __('Underwriting sheet')
              }}</span>
              <a
                v-if="activity.workbook.sheet_url"
                :href="activity.workbook.sheet_url"
                target="_blank"
                rel="noopener noreferrer"
                class="truncate text-ink-blue-link hover:underline"
              >
                {{ activity.workbook.sheet_url }}
              </a>
            </div>
          </div>
          <div v-else class="mb-4 flex flex-col gap-2 py-1.5">
            <div class="flex items-center justify-stretch gap-2 text-xs">
              <div
                v-if="activity.other_versions"
                class="inline-flex flex-wrap gap-1.5 text-ink-gray-8 font-medium"
              >
                <span>{{
                  activity.show_others ? __('Hide') : __('Show')
                }}</span>
                <span> +{{ activity.other_versions.length + 1 }} </span>
                <span>{{ __('changes from') }}</span>
                <span>{{ activity.owner_name }}</span>
                <Button
                  class="!size-4"
                  variant="ghost"
                  :icon="SelectIcon"
                  @click="activity.show_others = !activity.show_others"
                />
              </div>
              <div
                v-else
                class="inline-flex items-center flex-wrap gap-1 text-ink-gray-5"
              >
                <span class="font-medium text-ink-gray-8">
                  {{ activity.owner_name }}
                </span>
                <span v-if="activity.type">{{ __(activity.type) }}</span>
                <span
                  v-if="activity.data?.field_label"
                  class="max-w-xs truncate font-medium text-ink-gray-8"
                >
                  {{ __(activity.data.field_label) }}
                </span>
                <span v-if="activity.value">{{ __(activity.value) }}</span>
                <span
                  v-if="activity.data?.old_value"
                  class="max-w-xs font-medium text-ink-gray-8"
                >
                  <div
                    v-if="activity.options == 'User'"
                    class="flex items-center gap-1"
                  >
                    <UserAvatar :user="activity.data.old_value" size="xs" />
                    {{ getUser(activity.data.old_value).full_name }}
                  </div>
                  <div v-else class="flex items-center gap-1">
                    <IndicatorIcon
                      v-if="activity.data?.field === 'status'"
                      class="h-3 w-3 shrink-0"
                      :class="statusColorClass(activity, activity.data.old_value)"
                    />
                    <span class="truncate">{{ activity.data.old_value }}</span>
                  </div>
                </span>
                <span v-if="activity.to">{{ __('to') }}</span>
                <span
                  v-if="activity.data?.value"
                  class="max-w-xs font-medium text-ink-gray-8"
                >
                  <div
                    v-if="activity.options == 'User'"
                    class="flex items-center gap-1"
                  >
                    <UserAvatar :user="activity.data.value" size="xs" />
                    {{ getUser(activity.data.value).full_name }}
                  </div>
                  <div v-else class="flex items-center gap-1">
                    <IndicatorIcon
                      v-if="activity.data?.field === 'status'"
                      class="h-3 w-3 shrink-0"
                      :class="statusColorClass(activity, activity.data.value)"
                    />
                    <span class="truncate">{{ activity.data.value }}</span>
                  </div>
                </span>
              </div>

              <div class="ml-auto whitespace-nowrap">
                <Tooltip :text="formatDate(activity.creation)">
                  <div class="text-xs text-ink-gray-5">
                    {{ shortTime(activity.creation) }}
                  </div>
                </Tooltip>
              </div>
            </div>
            <div
              v-if="activity.other_versions && activity.show_others"
              class="flex flex-col gap-0.5"
            >
              <div
                v-for="a in sortByCreation([
                  activity,
                  ...activity.other_versions,
                ])"
                :key="a.creation"
                class="flex items-start justify-stretch gap-2 py-1.5 text-xs"
              >
                <div class="inline-flex flex-wrap gap-1 text-ink-gray-5">
                  <span
                    v-if="a.data?.field_label"
                    class="max-w-xs truncate text-ink-gray-5"
                  >
                    {{ __(a.data.field_label) }}
                  </span>
                  <FeatherIcon
                    name="arrow-right"
                    class="mx-1 h-4 w-4 text-ink-gray-5"
                  />
                  <span v-if="a.type">
                    {{ startCase(__(a.type)) }}
                  </span>
                  <span
                    v-if="a.data?.old_value"
                    class="max-w-xs font-medium text-ink-gray-8"
                  >
                    <div
                      v-if="a.options == 'User'"
                      class="flex items-center gap-1"
                    >
                      <UserAvatar :user="a.data.old_value" size="xs" />
                      {{ getUser(a.data.old_value).full_name }}
                    </div>
                    <div v-else class="flex items-center gap-1">
                      <IndicatorIcon
                        v-if="a.data?.field === 'status'"
                        class="h-3 w-3 shrink-0"
                        :class="statusColorClass(a, a.data.old_value)"
                      />
                      <span class="truncate">{{ a.data.old_value }}</span>
                    </div>
                  </span>
                  <span v-if="a.to">{{ __('to') }}</span>
                  <span
                    v-if="a.data?.value"
                    class="max-w-xs font-medium text-ink-gray-8"
                  >
                    <div
                      v-if="a.options == 'User'"
                      class="flex items-center gap-1"
                    >
                      <UserAvatar :user="a.data.value" size="xs" />
                      {{ getUser(a.data.value).full_name }}
                    </div>
                    <div v-else class="flex items-center gap-1">
                      <IndicatorIcon
                        v-if="a.data?.field === 'status'"
                        class="h-3 w-3 shrink-0"
                        :class="statusColorClass(a, a.data.value)"
                      />
                      <span class="truncate">{{ a.data.value }}</span>
                    </div>
                  </span>
                </div>

                <div class="ml-auto whitespace-nowrap">
                  <Tooltip :text="formatDate(a.creation)">
                    <div class="text-xs text-ink-gray-5">
                      {{ shortTime(a.creation) }}
                    </div>
                  </Tooltip>
                </div>
              </div>
            </div>
          </div>
          </div>
        </template>
      </template>
    </div>
    <div v-else-if="title == 'Data'" class="h-full flex flex-col px-3 sm:px-10">
      <DataFields
        :doctype="doctype"
        :docname="docname"
        @beforeSave="(data) => emit('beforeSave', data)"
        @afterSave="(data) => emit('afterSave', data)"
      />
    </div>
    <EmptyState
      v-else
      :title="emptyText"
      :description="emptyTextDescription"
      :icon="emptyTextIcon"
      :top="top"
    />
  </FadedScrollableDiv>
  <div>
    <CommunicationArea
      v-if="['Emails', 'Comments', 'Activity'].includes(title)"
      ref="emailBox"
      v-model="doc"
      v-model:reload="reload_email"
      :doctype="doctype"
    />
    <WhatsAppBox
      v-if="title == 'WhatsApp'"
      ref="whatsappBox"
      v-model="doc"
      v-model:reply="replyMessage"
      v-model:whatsapp="whatsappMessages"
      :doctype="doctype"
      @scroll="scroll"
    />
    <SMSBox
      v-if="title == 'SMS'"
      ref="smsBox"
      v-model="doc"
      v-model:sms="smsMessages"
      :doctype="doctype"
      @scroll="scroll"
    />
  </div>
  <WhatsappTemplateSelectorModal
    v-if="whatsappEnabled"
    v-model="showWhatsappTemplates"
    :doctype="doctype"
    @send="(t) => sendTemplate(t)"
  />
  <AllModals
    ref="modalRef"
    v-model="all_activities"
    :doctype="doctype"
    :doc="doc"
  />
  <FilesUploader
    v-model="showFilesUploader"
    :doctype="doctype"
    :docname="docname"
    @after="
      () => {
        all_activities.reload()
        changeTabTo('attachments')
      }
    "
  />
</template>
<script setup>
import ActivityHeader from '@/components/Activities/ActivityHeader.vue'
import EmailArea from '@/components/Activities/EmailArea.vue'
import CommentArea from '@/components/Activities/CommentArea.vue'
import CallArea from '@/components/Activities/CallArea.vue'
import NoteArea from '@/components/Activities/NoteArea.vue'
import TaskArea from '@/components/Activities/TaskArea.vue'
import TaskTodoList from '@/components/Activities/TaskTodoList.vue'
import QuickComments from '@/components/Activities/QuickComments.vue'
import DispoHeaderBlock from '@/components/Activities/DispoHeaderBlock.vue'
import AttachmentArea from '@/components/Activities/AttachmentArea.vue'
import DataFields from '@/components/Activities/DataFields.vue'
import UserAvatar from '@/components/UserAvatar.vue'
import IndicatorIcon from '@/components/Icons/IndicatorIcon.vue'
import ActivityIcon from '@/components/Icons/ActivityIcon.vue'
import EmailIcon from '@/components/Icons/EmailIcon.vue'
import DetailsIcon from '@/components/Icons/DetailsIcon.vue'
import PhoneIcon from '@/components/Icons/PhoneIcon.vue'
import NoteIcon from '@/components/Icons/NoteIcon.vue'
import TaskIcon from '@/components/Icons/TaskIcon.vue'
import AttachmentIcon from '@/components/Icons/AttachmentIcon.vue'
import WhatsAppIcon from '@/components/Icons/WhatsAppIcon.vue'
import WhatsAppArea from '@/components/Activities/WhatsAppArea.vue'
import WhatsAppBox from '@/components/Activities/WhatsAppBox.vue'
import SMSArea from '@/components/Activities/SMSArea.vue'
import TimelineAvatar from '@/components/Activities/TimelineAvatar.vue'
import SMSMedia from '@/components/Activities/SMSMedia.vue'
import SMSBox from '@/components/Activities/SMSBox.vue'
import LoadingIndicator from '@/components/Icons/LoadingIndicator.vue'
import EmptyState from '@/components/ListViews/EmptyState.vue'
import LeadsIcon from '@/components/Icons/LeadsIcon.vue'
import DealsIcon from '@/components/Icons/DealsIcon.vue'
import DotIcon from '@/components/Icons/DotIcon.vue'
import MoneyIcon from '@/components/Icons/MoneyIcon.vue'
import CommentIcon from '@/components/Icons/CommentIcon.vue'
import SelectIcon from '@/components/Icons/SelectIcon.vue'
import MissedCallIcon from '@/components/Icons/MissedCallIcon.vue'
import DeclinedCallIcon from '@/components/Icons/DeclinedCallIcon.vue'
import InboundCallIcon from '@/components/Icons/InboundCallIcon.vue'
import OutboundCallIcon from '@/components/Icons/OutboundCallIcon.vue'
import FadedScrollableDiv from '@/components/FadedScrollableDiv.vue'
import CommunicationArea from '@/components/CommunicationArea.vue'
import WhatsappTemplateSelectorModal from '@/components/Modals/WhatsappTemplateSelectorModal.vue'
import AllModals from '@/components/Activities/AllModals.vue'
import LostReasonModal from '@/components/Modals/LostReasonModal.vue'
import EmailThread from '@/components/Activities/EmailThread.vue'
import { statusesStore } from '@/stores/statuses'
import FilesUploader from '@/components/FilesUploader/FilesUploader.vue'
import { timeAgo, formatDate, startCase, formatNumber } from '@/utils'
import { globalStore } from '@/stores/global'
import { usersStore } from '@/stores/users'
import { whatsappEnabled, smsEnabled } from '@/composables/settings'
import { useDocument } from '@/data/document'
import { useTelemetry } from 'frappe-ui/frappe'
import { Button, Tooltip, createResource, toast, dayjs, call } from 'frappe-ui'
import { useElementVisibility } from '@vueuse/core'
import {
  ref,
  computed,
  h,
  markRaw,
  watch,
  nextTick,
  onMounted,
  onBeforeUnmount,
} from 'vue'
import { useRoute } from 'vue-router'

const { $socket } = globalStore()
const { getUser } = usersStore()
const { capture } = useTelemetry()

const props = defineProps({
  doctype: { type: String, default: 'CRM Lead' },
  docname: { type: String, default: '' },
  tabs: { type: Array, default: () => [] },
  scrollOnMount: { type: Boolean, default: true },
})

const emit = defineEmits(['beforeSave', 'afterSave'])

const route = useRoute()

const reload = defineModel('reload', { type: Boolean, default: false })
const tabIndex = defineModel('tabIndex', { type: Number, default: 0 })

const { document: _document } = useDocument(props.doctype, props.docname)

const doc = computed(() => _document.doc || {})

const reload_email = ref(false)
const modalRef = ref(null)
const showFilesUploader = ref(false)

const title = computed(() => props.tabs?.[tabIndex.value]?.name || 'Activity')

const { getLeadStatus, getDealStatus } = statusesStore()
const showLostReasonModal = ref(false)
const isLostLead = computed(
  () =>
    props.doctype == 'CRM Lead' &&
    doc.value.status &&
    getLeadStatus(doc.value.status)?.type == 'Lost',
)
const isRefundPoolLead = computed(
  () =>
    props.doctype == 'CRM Lead' &&
    !!doc.value.status &&
    !!getLeadStatus(doc.value.status)?.custom_refund_pool,
)

function toggleLeadFlag(fieldname, value) {
  if (!_document.doc) return
  _document.doc[fieldname] = value ? 1 : 0
  if (fieldname === 'custom_refundable' && value) {
    _document.doc.custom_refund_status =
      _document.doc.custom_refund_status || 'To Request'
  }
  if (fieldname === 'custom_refund_requested') {
    _document.doc.custom_refund_requested_on = value
      ? dayjs().format('YYYY-MM-DD HH:mm:ss')
      : null
    if (value && !_document.doc.custom_refund_status) {
      _document.doc.custom_refund_status = 'Requested'
    }
  }
  _document.save.submit()
}

const refundDraft = computed(() => {
  const raw = doc.value.custom_refund_draft_json
  if (!raw) return {}
  try {
    return typeof raw === 'string' ? JSON.parse(raw) : raw
  } catch {
    return {}
  }
})

function replyToEmail(email) {
  emailBox.value?.replyTo?.(email)
}

const emailMessages = computed(() => {
  const list = (all_activities.data?.versions || []).filter(
    (a) => a.activity_type === 'communication',
  )
  return [...list].sort(
    (a, b) => new Date(b.creation) - new Date(a.creation),
  )
})


// Color a status value (e.g. in "changed Status from New to Called No Answer")
// with that status's own color, matching how statuses appear elsewhere.
function statusColorClass(activity, value) {
  if (activity?.data?.field !== 'status' || !value) return ''
  let status =
    props.doctype == 'CRM Deal' ? getDealStatus(value) : getLeadStatus(value)
  return status?.color || ''
}

const changeTabTo = (tabName) => {
  const tabNames = props.tabs?.map((tab) => tab.name?.toLowerCase())
  const index = tabNames?.indexOf(tabName)
  if (index == -1) return
  tabIndex.value = index
}

const all_activities = createResource({
  url: 'crm.api.activities.get_activities',
  params: { name: props.docname },
  cache: ['activity', props.docname],
  auto: true,
  transform: ([versions, calls, notes, tasks, attachments]) => {
    return { versions, calls, notes, tasks, attachments }
  },
})

const showWhatsappTemplates = ref(false)

const whatsappMessages = createResource({
  url: 'crm.api.whatsapp.get_whatsapp_messages',
  cache: ['whatsapp_messages', props.docname],
  params: {
    reference_doctype: props.doctype,
    reference_name: props.docname,
  },
  auto: whatsappEnabled.value,
  transform: (data) => sortByCreation(data),
})

const smsMessages = createResource({
  url: 'crm.api.sms.get_sms_messages',
  cache: ['sms_messages', props.docname],
  params: {
    reference_doctype: props.doctype,
    reference_name: props.docname,
  },
  transform: (data) => sortByCreation(data),
})

// BatchData tax-info pulls for this lead (shown in the Activity timeline + the
// sidebar Tax Info card). Leads only.
const taxPulls = createResource({
  url: 'crm.api.tax_info.get_tax_pulls',
  cache: ['tax_pulls', props.docname],
  params: { lead: props.docname },
  auto: props.doctype === 'CRM Lead',
})

// Documenso e-sign agreements for this lead (timeline + sidebar card). Leads only.
const agreements = createResource({
  url: 'crm.api.agreement.get_agreements',
  cache: ['agreements', props.docname],
  params: { lead: props.docname },
  auto: props.doctype === 'CRM Lead',
})

// Google Sheets underwriting workbooks for this lead (timeline + sidebar card).
const underwritingWorkbooks = createResource({
  url: 'crm.api.underwriting.get_underwriting_workbooks',
  cache: ['underwriting_workbooks', props.docname],
  params: { lead: props.docname },
  auto: props.doctype === 'CRM Lead',
})

// is_sms_enabled resolves asynchronously, so `auto: smsEnabled.value` is often
// false at creation and the resource never fetches. Fetch once it's enabled.
watch(
  smsEnabled,
  (on) => {
    if (on) smsMessages.reload()
  },
  { immediate: true },
)

onBeforeUnmount(() => {
  $socket.off('whatsapp_message')
  $socket.off('quo_message')
  $socket.off('crm_task_update')
  $socket.off('crm_tax_pull')
  $socket.off('crm_esign')
  $socket.off('crm_underwriting')
  $socket.off('crm_call_log')
})

onMounted(() => {
  $socket.on('whatsapp_message', (data) => {
    if (
      data.reference_doctype === props.doctype &&
      data.reference_name === props.docname
    ) {
      whatsappMessages.reload()
    }
  })

  // quo_message events are always lead-scoped; on a deal, match its linked lead
  $socket.on('quo_message', (data) => {
    const onLead =
      props.doctype === 'CRM Lead' && data.reference_docname === props.docname
    const onDeal =
      props.doctype === 'CRM Deal' && data.reference_docname === doc.value.lead
    if (onLead || onDeal) smsMessages.reload()
  })

  // tasks (create / complete / delete) — refresh the to-do block + history
  $socket.on('crm_task_update', (data) => {
    if (
      data.reference_doctype === props.doctype &&
      data.reference_docname === props.docname
    ) {
      all_activities.reload()
    }
  })

  // tax-info pull finished — refresh the timeline entry + sidebar Tax Info card
  $socket.on('crm_tax_pull', (data) => {
    if (
      data.reference_doctype === props.doctype &&
      data.reference_docname === props.docname
    ) {
      taxPulls.reload()
    }
  })

  // e-sign agreement created or a recipient signed — refresh timeline + card
  $socket.on('crm_esign', (data) => {
    if (
      data.reference_doctype === props.doctype &&
      data.reference_docname === props.docname
    ) {
      agreements.reload()
    }
  })

  // underwriting sheet created — refresh the timeline entry + Underwriting card
  $socket.on('crm_underwriting', (data) => {
    if (
      data.reference_doctype === props.doctype &&
      data.reference_docname === props.docname
    ) {
      underwritingWorkbooks.reload()
    }
  })

  // A newly-added lead phone finished its Quo backfill — the Calls / Activity
  // timeline should show the pulled logs without a reload.
  $socket.on('crm_call_log', (data) => {
    if (
      data.reference_doctype === props.doctype &&
      data.reference_docname === props.docname
    ) {
      all_activities.reload()
    }
  })

  // Auto-scroll happens ONLY here, on mount: land on the deep-linked element
  // (e.g. a #comment hash) or, with no hash, on the newest entry. We intentionally
  // do NOT scroll on resource reloads (comment/task adds, realtime events from
  // teammates) — that yanked the viewport away every time the user did anything.
  nextTick(() => {
    if (!props.scrollOnMount) return
    const hash = route.hash.slice(1) || null
    let tabNames = props.tabs?.map((tab) => tab.name)
    if (!tabNames?.includes(hash)) {
      scroll(hash)
    }
  })
})

function sendTemplate(template) {
  showWhatsappTemplates.value = false
  capture('send_whatsapp_template', { doctype: props.doctype })
  createResource({
    url: 'crm.api.whatsapp.send_whatsapp_template',
    params: {
      reference_doctype: props.doctype,
      reference_name: props.docname,
      to: doc.value.mobile_no,
      template,
    },
    auto: true,
    onError: (error) => {
      toast.error(error.messages?.[0] || __('Failed to send WhatsApp template'))
    },
    onSuccess: () => whatsappMessages.reload(),
  })
}

const replyMessage = ref({})

function get_activities() {
  if (!all_activities.data?.versions) return []
  if (!all_activities.data?.calls.length)
    return all_activities.data.versions || []
  return [...all_activities.data.versions, ...all_activities.data.calls]
}

// who each feed entry is "by", for the avatar rail (type drives the badge)
function railProps(activity) {
  const t = activity.activity_type
  if (t == 'incoming_text')
    return {
      image: doc.value.image,
      label: doc.value.lead_name || doc.value.first_name,
      type: t,
    }
  if (t == 'outgoing_text') return { user: activity.sender, type: t }
  if (t == 'incoming_call' || t == 'outgoing_call')
    return {
      image: activity._caller?.image,
      label: activity._caller?.label,
      type: t,
    }
  if (t == 'communication') return { user: activity.data?.sender, type: t }
  if (t == 'email_thread') {
    const last = activity.messages?.[0]
    return { user: last?.data?.sender, type: 'communication' }
  }
  if (t == 'task') return { user: activity.task?.assigned_to, type: t }
  if (t == 'tax_pull') return { user: activity.pull?.pulled_by, type: t }
  if (t == 'agreement') return { user: activity.agreement?.owner, type: t }
  if (t == 'underwriting')
    return { user: activity.workbook?.created_by_user, type: t }
  return { user: activity.owner, type: t }
}

// day dividers: the feed repeats "22 hours ago" on every row otherwise —
// group by calendar day and let each row carry just a clock time
function isNewDay(i) {
  if (i == 0) return true
  const list =
    title.value === 'Activity' ? feedItems.value : activities.value
  const day = (a) => dayjs(a.creation).format('YYYY-MM-DD')
  return day(list[i]) != day(list[i - 1])
}

function dayLabel(date) {
  const d = dayjs(date)
  if (d.isSame(dayjs(), 'day')) return __('Today')
  if (d.isSame(dayjs().subtract(1, 'day'), 'day')) return __('Yesterday')
  return d.format(d.isSame(dayjs(), 'year') ? 'ddd, MMM D' : 'MMM D, YYYY')
}

function shortTime(date) {
  return formatDate(date, 'h:mm a')
}

// texts as timeline entries, so they show in the unified Activity feed
function get_text_activities() {
  if (!smsMessages.data) return []
  return smsMessages.data.map((m) => ({
    name: m.name,
    activity_type: m.type === 'Incoming' ? 'incoming_text' : 'outgoing_text',
    creation: m.creation,
    message: m.message,
    media: m.media,
    status: m.status,
    sender: m.sender,
    sender_name: m.sender_name,
  }))
}

// tax-info pulls as timeline entries, anchored at the time of the pull
function get_tax_pull_activities() {
  if (!taxPulls.data) return []
  return taxPulls.data.map((p) => ({
    name: p.name,
    activity_type: 'tax_pull',
    creation: p.pulled_at || p.creation,
    pull: p,
  }))
}

// e-sign agreements as timeline entries, anchored at creation
function get_agreement_activities() {
  if (!agreements.data) return []
  return agreements.data.map((a) => ({
    name: a.name,
    activity_type: 'agreement',
    creation: a.creation,
    agreement: a,
  }))
}

function openAgreementLink(url) {
  if (url) window.open(url, '_blank', 'noopener,noreferrer')
}

function isTerminationAgreement(a) {
  return String(a?.template_title || '').startsWith('Unilateral Termination - No EMD')
}

// Proxy endpoint that streams the fully-signed PDF (the backend holds the
// Documenso token; the raw Documenso URL is an internal, expiring MinIO link).
function signedAgreementUrl(a) {
  return `/api/method/crm.api.agreement.download_signed_agreement?agreement=${encodeURIComponent(a.name)}`
}

// underwriting workbooks as timeline entries, anchored at creation
function get_underwriting_activities() {
  if (!underwritingWorkbooks.data) return []
  return underwritingWorkbooks.data.map((w) => ({
    name: w.name,
    activity_type: 'underwriting',
    creation: w.workbook_created_at || w.creation,
    workbook: w,
  }))
}

// open tasks pinned at the top of the Activity feed (Trello-style to-do list)
const openTasks = computed(() => {
  if (!all_activities.data?.tasks) return []
  return all_activities.data.tasks.filter(
    (t) => !['Done', 'Canceled'].includes(t.status),
  )
})

// completed/canceled tasks fold into the chronological history, anchored at
// their completion date (modified ≈ when the status was flipped).
function get_task_activities() {
  if (!all_activities.data?.tasks) return []
  return all_activities.data.tasks
    .filter((t) => ['Done', 'Canceled'].includes(t.status))
    .map((t) => ({
      name: t.name,
      activity_type: 'task',
      creation: t.modified,
      task: t,
    }))
}

const activities = computed(() => {
  let _activities = []
  if (title.value == 'Activity') {
    _activities = [
      ...get_activities(),
      ...get_text_activities(),
      ...get_task_activities(),
      ...get_tax_pull_activities(),
      ...get_agreement_activities(),
      ...get_underwriting_activities(),
    ]
  } else if (title.value == 'Emails') {
    if (!all_activities.data?.versions) return []
    _activities = all_activities.data.versions.filter(
      (activity) => activity.activity_type === 'communication',
    )
  } else if (title.value == 'Comments') {
    if (!all_activities.data?.versions) return []
    _activities = all_activities.data.versions.filter(
      (activity) => activity.activity_type === 'comment',
    )
  } else if (title.value == 'Calls') {
    if (!all_activities.data?.calls) return []
    return sortByCreation(all_activities.data.calls)
  } else if (title.value == 'Tasks') {
    if (!all_activities.data?.tasks) return []
    return sortByModified(all_activities.data.tasks)
  } else if (title.value == 'Notes') {
    if (!all_activities.data?.notes) return []
    return sortByModified(all_activities.data.notes)
  } else if (title.value == 'Attachments') {
    if (!all_activities.data?.attachments) return []
    return sortByModified(all_activities.data.attachments)
  }

  _activities.forEach((activity) => {
    if (
      activity.activity_type == 'incoming_call' ||
      activity.activity_type == 'outgoing_call' ||
      activity.activity_type == 'communication' ||
      activity.activity_type == 'incoming_text' ||
      activity.activity_type == 'outgoing_text' ||
      activity.activity_type == 'task' ||
      activity.activity_type == 'tax_pull' ||
      activity.activity_type == 'agreement' ||
      activity.activity_type == 'underwriting'
    )
      return

    update_activities_details(activity)

    if (activity.other_versions) {
      activity.show_others = false
      activity.other_versions.forEach((other_version) => {
        update_activities_details(other_version)
      })
    }
  })
  // Every lead view reads newest-first (Lance, 2026-08-06) — Activity, Emails,
  // Comments, Calls, Tasks, Notes and Attachments all land here. The Text and
  // WhatsApp threads are deliberately NOT in this computed: they render from
  // their own chat resources and keep chat's oldest-at-top convention, where the
  // newest message belongs at the bottom next to the composer.
  return sortByCreation(_activities).reverse()
})

const feedItems = computed(() => {
  const list = activities.value || []
  if (title.value !== 'Activity') return list
  const emails = list.filter((a) => a.activity_type === 'communication')
  if (!emails.length) return list
  const out = []
  let placed = false
  for (const a of list) {
    if (a.activity_type === 'communication') {
      if (!placed) {
        out.push({
          activity_type: 'email_thread',
          name: 'thread-all',
          creation: a.creation,
          messages: emails,
        })
        placed = true
      }
      continue
    }
    out.push(a)
  }
  return out
})

function sortByCreation(list) {
  return list.sort((a, b) => new Date(a.creation) - new Date(b.creation))
}
function sortByModified(list) {
  return list.sort((b, a) => new Date(a.modified) - new Date(b.modified))
}

function update_activities_details(activity) {
  activity.owner_name = getUser(activity.owner).full_name
  activity.type = ''
  activity.value = ''
  activity.to = ''

  if (activity.activity_type == 'creation') {
    activity.type = activity.data
  } else if (activity.activity_type == 'added') {
    activity.type = 'added'
    activity.value = 'as'
  } else if (activity.activity_type == 'removed') {
    activity.type = 'removed'
    activity.value = 'value'
  } else if (activity.activity_type == 'changed') {
    activity.type = 'changed'
    activity.value = 'from'
    activity.to = 'to'
  }
}

const top = computed(() => {
  if (['Activity', 'Emails', 'Comments'].includes(title.value)) {
    return '32.3%'
  }
  return '30%'
})

const emptyText = computed(() => {
  let text = 'No Activities Found'
  if (title.value == 'Emails') {
    text = 'No Emails Found'
  } else if (title.value == 'Comments') {
    text = 'No Comments Found'
  } else if (title.value == 'Data') {
    text = 'No Data Fields Added Yet'
  } else if (title.value == 'Calls') {
    text = 'No Call History'
  } else if (title.value == 'Notes') {
    text = 'No Notes Found'
  } else if (title.value == 'Tasks') {
    text = 'No Tasks Found'
  } else if (title.value == 'Attachments') {
    text = 'No Attachments Found'
  } else if (title.value == 'WhatsApp') {
    text = 'No WhatsApp Messages Found'
  } else if (title.value == 'SMS') {
    text = 'No Text Messages Found'
  }
  return text
})

const emptyTextDescription = computed(() => {
  let description =
    'There are no activities to display here. Go ahead and make some changes.'
  if (title.value == 'Emails') {
    description =
      'No emails found in your inbox. New messages will appear here soon.'
  } else if (title.value == 'Comments') {
    description = 'Be the first to add one.'
  } else if (title.value == 'Data') {
    description = 'No data fields have been added yet.'
  } else if (title.value == 'Calls') {
    description = 'No recent calls to display. Log a call or call someone now!'
  } else if (title.value == 'Notes') {
    description = 'Nothing here for now. Add a note to keep track of things.'
  } else if (title.value == 'Tasks') {
    description =
      'Nothing to do at the moment. Start organizing by adding one here.'
  } else if (title.value == 'Attachments') {
    description =
      'No files have been attached yet. Upload files to see them here.'
  } else if (title.value == 'WhatsApp') {
    description = 'Start a conversation now!'
  } else if (title.value == 'SMS') {
    description = 'Send a text to start the conversation.'
  }
  return description
})

const emptyTextIcon = computed(() => {
  let icon = ActivityIcon
  if (title.value == 'Emails') {
    icon = EmailIcon
  } else if (title.value == 'Comments') {
    icon = CommentIcon
  } else if (title.value == 'Data') {
    icon = DetailsIcon
  } else if (title.value == 'Calls') {
    icon = PhoneIcon
  } else if (title.value == 'Notes') {
    icon = NoteIcon
  } else if (title.value == 'Tasks') {
    icon = TaskIcon
  } else if (title.value == 'Attachments') {
    icon = AttachmentIcon
  } else if (title.value == 'WhatsApp') {
    icon = WhatsAppIcon
  } else if (title.value == 'SMS') {
    icon = CommentIcon
  }
  return h(icon, { class: 'text-ink-gray-4' })
})

const emailBox = ref(null)
const whatsappBox = ref(null)
const smsBox = ref(null)

const draftLoadedFor = ref('')
watch(
  () => [refundDraft.value?.reply, props.docname, emailBox.value],
  () => {
    const d = refundDraft.value
    if (!d?.reply || props.doctype !== 'CRM Lead') return
    if (!emailBox.value?.loadDraft) return
    if (draftLoadedFor.value === props.docname) return
    draftLoadedFor.value = props.docname
    changeTabTo('emails')
    nextTick(() => {
      const last = emailMessages.value[0]
      emailBox.value.loadDraft({
        to: d.reply_to,
        subject: d.subject,
        body: d.reply,
        quoteHtml: last?.data?.content || '',
      })
    })
  },
)

watch([reload, reload_email], ([reload_value, reload_email_value]) => {
  if (reload_value || reload_email_value) {
    all_activities.reload()
    _document.reload()
    reload.value = false
    reload_email.value = false
  }
})

function scroll(hash) {
  if (['tasks', 'notes'].includes(route.hash?.slice(1))) return
  setTimeout(() => {
    let el
    if (!hash) {
      let e = document.getElementsByClassName('activity')
      // Every tab in this feed is newest-first now, so the newest entry is always
      // the first element — no per-tab special case left.
      el = e[0]
    } else {
      el = document.getElementById(hash)
    }
    if (el && !useElementVisibility(el).value) {
      el.scrollIntoView({ behavior: 'smooth' })
      el.focus()
    }
  }, 500)
}

// Surface the call-log / send-text modals so the page header (Lead/Deal) can
// trigger them from buttons next to the name, mirroring ActivityHeader.
const createCallLog = () => modalRef.value?.createCallLog()
const sendText = () => modalRef.value?.sendText()
const fetchTaxInfo = () => modalRef.value?.fetchTaxInfo()
const createAgreement = () => modalRef.value?.createAgreement()
const createUnderwriting = () => modalRef.value?.createUnderwriting()

defineExpose({
  emailBox,
  all_activities,
  changeTabTo,
  createCallLog,
  sendText,
  fetchTaxInfo,
  createAgreement,
  createUnderwriting,
})
</script>
