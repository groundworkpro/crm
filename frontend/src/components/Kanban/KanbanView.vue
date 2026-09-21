<template>
  <div class="flex overflow-x-auto h-full">
    <Draggable
      v-if="columns"
      :list="columns"
      item-key="column"
      :delay="isTouchScreenDevice() ? 200 : 0"
      class="flex sm:mx-2.5 mx-2 pb-3.5"
      @end="updateColumn"
    >
      <template #item="{ element: column }">
        <div
          v-if="!column.column.delete"
          class="flex flex-col gap-2.5 min-w-72 w-72 hover:bg-surface-gray-2 rounded-lg p-2.5"
        >
          <div class="flex gap-2 items-center group justify-between">
            <div class="flex items-center text-base">
              <Popover>
                <template #target="{ togglePopover }">
                  <Button
                    variant="ghost"
                    size="sm"
                    class="hover:!bg-surface-gray-2"
                    @click="togglePopover"
                  >
                    <IndicatorIcon :class="parseColor(column.column.color)" />
                  </Button>
                </template>
                <template #body>
                  <div
                    class="flex flex-col gap-3 px-3 py-2.5 min-w-40 rounded-lg bg-surface-modal shadow-2xl ring-1 ring-black ring-opacity-5 focus:outline-none"
                  >
                    <div class="flex gap-1">
                      <Button
                        v-for="color in colors"
                        :key="color"
                        variant="ghost"
                        @click="() => (column.column.color = color)"
                      >
                        <IndicatorIcon :class="parseColor(color)" />
                      </Button>
                    </div>
                    <div class="flex flex-row-reverse">
                      <Button
                        variant="solid"
                        :label="__('Apply')"
                        @click="updateColumn"
                      />
                    </div>
                  </div>
                </template>
              </Popover>
              <div class="text-ink-gray-9">{{ column.column.name }}</div>
            </div>
            <div class="flex">
              <Dropdown :options="actions(column)">
                <template #default>
                  <!--
                    data-[state=open]: reka-ui sets pointer-events:none on <body>
                    while the menu is open, so the column loses :hover and a
                    hover-only trigger collapses to display:none — Popper then
                    anchors the menu to a 0x0 box at the window origin. Keep the
                    trigger rendered for as long as its own menu is open.
                  -->
                  <Button
                    class="hidden group-hover:flex data-[state=open]:flex"
                    icon="more-horizontal"
                    variant="ghost"
                  />
                </template>
              </Dropdown>
              <Button
                icon="plus"
                variant="ghost"
                @click="options.onNewClick(column)"
              />
            </div>
          </div>
          <div class="overflow-y-auto flex flex-col gap-2 h-full">
            <Draggable
              :list="column.data"
              group="fields"
              item-key="name"
              class="flex flex-col gap-3.5 flex-1"
              :delay="isTouchScreenDevice() ? 200 : 0"
              :data-column="column.column.name"
              @end="updateColumn"
            >
              <template #item="{ element: fields }">
                <component
                  :is="
                    options.onClick ? 'a' : options.getRoute ? 'router-link' : 'div'
                  "
                  :class="[
                    'group/card relative pt-3 px-3.5 pb-2.5 rounded-lg border bg-surface-white text-base flex flex-col text-ink-gray-9',
                    options.cardColor ? dueTint(options.cardColor(fields)) : '',
                  ]"
                  :data-name="fields.name"
                  v-bind="{
                    to:
                      !options.onClick && options.getRoute
                        ? options.getRoute(fields)
                        : undefined,
                    href: options.onClick ? cardHref(fields) : undefined,
                    onClick: options.onClick
                      ? (e) => options.onClick(fields, e)
                      : undefined,
                  }"
                >
                  <slot
                    name="title"
                    v-bind="{ fields, titleField, itemName: fields.name }"
                  >
                    <div class="h-5 flex items-center">
                      <div v-if="fields[titleField]">
                        {{ fields[titleField] }}
                      </div>
                      <div v-else class="text-ink-gray-4">
                        {{ __('No Title') }}
                      </div>
                    </div>
                  </slot>
                  <div class="border-b h-px my-2.5" />

                  <div class="flex flex-col gap-3.5">
                    <template
                      v-for="field in normalizeFields(column.fields)"
                      :key="field.fieldname"
                    >
                      <slot
                        name="fields"
                        v-bind="{
                          fields,
                          fieldName: field.fieldname,
                          fieldLabel: field.label,
                          showBlank: field.showBlank,
                          itemName: fields.name,
                        }"
                      >
                        <div
                          v-if="fields[field.fieldname] || field.showBlank"
                          class="truncate flex items-center gap-2"
                        >
                          <span v-if="field.label" class="shrink-0 text-ink-gray-5">
                            {{ field.label }}
                          </span>
                          <span v-if="fields[field.fieldname]" class="truncate">
                            {{ fields[field.fieldname] }}
                          </span>
                          <span v-else class="truncate text-ink-gray-4">&mdash;</span>
                        </div>
                      </slot>
                    </template>
                  </div>
                  <div class="border-b h-px mt-2.5 mb-2" />
                  <slot name="actions" v-bind="{ itemName: fields.name }">
                    <div class="flex gap-2 items-center justify-between">
                      <div></div>
                      <Button icon="plus" variant="ghost" @click.stop.prevent />
                    </div>
                  </slot>
                </component>
              </template>
            </Draggable>
            <div
              v-if="column.column.count < column.column.all_count"
              class="flex items-center justify-center"
            >
              <Button
                :label="__('Load More')"
                @click="emit('loadMore', column.column.name)"
              />
            </div>
          </div>
        </div>
      </template>
    </Draggable>
    <div class="shrink-0 min-w-64">
      <Autocomplete
        value=""
        :options="deletedColumns"
        @change="(e) => addColumn(e)"
      >
        <template #target="{ togglePopover }">
          <Button
            class="w-full mt-2.5 mb-1 mr-5"
            :label="__('Add Column')"
            iconLeft="plus"
            @click="togglePopover()"
          />
        </template>
        <template #footer>
          <Button
            class="w-full"
            :label="__('Reload Columns')"
            :iconLeft="RefreshIcon"
            @click="updateColumn(null, true)"
          />
        </template>
      </Autocomplete>
    </div>
  </div>
</template>
<script setup>
import RefreshIcon from '@/components/Icons/RefreshIcon.vue'
import Autocomplete from '@/components/frappe-ui/Autocomplete.vue'
import IndicatorIcon from '@/components/Icons/IndicatorIcon.vue'
import { isTouchScreenDevice, colors, parseColor, dueTint } from '@/utils'
import Draggable from 'vuedraggable'
import { Dropdown, Popover, call, toast } from 'frappe-ui'
import { computed, watch } from 'vue'
import { useRouter } from 'vue-router'

const props = defineProps({
  options: {
    type: Object,
    default: () => ({
      getRoute: null,
      onClick: null,
      onNewClick: null,
    }),
  },
})

const emit = defineEmits(['update', 'loadMore'])

const router = useRouter()

// The card element is a plain <a> (not <router-link>) whenever the host supplies
// an onClick, because RouterLink will not let a click handler win: it registers
// its own navigate handler on the anchor and that runs BEFORE a fallthrough
// onClick attr, so by the time our handler calls preventDefault the router has
// already pushed -- guardEvent only checks defaultPrevented at ITS turn, which
// has passed. Verified live: the handler ran, defaultPrevented was true
// afterwards, and the page navigated anyway.
//
// A real href is kept either way, so cmd/middle-click still opens the record in
// a background tab (the browser does that natively and never fires our handler).
// Hosts that pass no onClick keep the original RouterLink and SPA navigation.
//
// NOTE none of this may be explained in a comment inside <template #item>:
// vuedraggable requires each item to have exactly ONE root node, and in dev the
// compiler keeps comments as vnodes, so a comment there turns the item into a
// fragment, leaves $el undefined and kills the board with
// "Cannot read properties of undefined (reading 'updated')".
//
// Resolve the destination getRoute already describes into a real URL, so a card
// that handles its own clicks still behaves like a link for everything the
// browser does natively: status-bar preview, cmd/middle-click, copy link address.
function cardHref(fields) {
  if (!props.options?.getRoute) return undefined
  try {
    return router.resolve(props.options.getRoute(fields)).href
  } catch (e) {
    // A malformed route must not take the whole board down; the card just stops
    // being a link and its click handler still works.
    return undefined
  }
}

const kanban = defineModel({ type: Object })

const titleField = computed(() => {
  return kanban.value?.data?.title_field
})

// kanban_fields entries are either a bare fieldname (legacy / no custom label)
// or { fieldname, label, showBlank } when the user set a card label and/or
// "show if blank" in Kanban Settings.
//
// Memoized on the source array: this is called from the template once per
// COLUMN on every render, and every column is handed the same `kanban_fields`
// array. Returning a stable reference also stops Vue re-keying the inner v-for
// each time the board re-renders.
const normalizedFieldsCache = new WeakMap()
function normalizeFields(fieldsList) {
  if (!fieldsList) return []
  const cached = normalizedFieldsCache.get(fieldsList)
  if (cached) return cached
  const normalized = fieldsList.map((f) =>
    typeof f === 'string'
      ? { fieldname: f, label: '', showBlank: false }
      : {
          fieldname: f.fieldname,
          label: f.label || '',
          showBlank: !!f.showBlank,
        },
  )
  normalizedFieldsCache.set(fieldsList, normalized)
  return normalized
}

const columns = computed(() => {
  if (!kanban.value?.data?.data || kanban.value.data.view_type != 'kanban')
    return []
  return kanban.value.data.data
})

// Fallback column colors are assigned OUTSIDE the computed above. Doing it in
// the getter meant a computed that wrote to the very data it read — a
// self-invalidating dependency, and the kind of thing that turns into a render
// loop the moment a board legitimately has no colors. The write still has to
// happen (updateColumn ships `column.color` back to the server, and the colour
// picker writes to the same place), just not during evaluation.
watch(
  columns,
  (cols) => {
    if (!cols?.length) return
    if (cols.some((column) => column.column?.color)) return
    cols.forEach((column, i) => {
      if (column.column) column.column.color = colors[i % colors.length]
    })
  },
  { immediate: true },
)

const deletedColumns = computed(() => {
  const _columns = kanban.value?.data?.kanban_columns || []
  return _columns
    ?.filter((col) => col['delete'])
    .map((col) => {
      return { label: col.name, value: col.name }
    })
})

function actions(column) {
  return [
    {
      group: __('Options'),
      hideLabel: true,
      items: [
        {
          label: __('Delete'),
          icon: 'trash-2',
          onClick: () => deleteColumn(column),
        },
      ],
    },
  ]
}

// Deleting a column deletes the STATUS everywhere — the status row itself,
// every user's saved view, and its place in every lead's status dropdown —
// not just this board (Lance, 2026-09-22: a hidden column whose status
// survives makes leads assigned to it vanish from the board).
// The server refuses while any record still sits in the status.
async function deleteColumn(column) {
  const name = column.column.name
  const boardDoctype = kanban.value?.data?.doctype
  const statusDoctype =
    boardDoctype === 'CRM Deal' ? 'CRM Deal Status' : 'CRM Lead Status'
  const ok = window.confirm(
    __(
      'Delete the "{0}" status everywhere? This removes the status itself — every board and every status dropdown — not just this column. It is refused while any record is still in "{0}".',
      [name],
    ),
  )
  if (!ok) return
  try {
    await call('crm.api.lead_status.delete_status', {
      doctype: statusDoctype,
      status: name,
    })
  } catch (e) {
    toast.error(
      __('Could not delete "{0}"', [name]) +
        ': ' +
        (e?.messages?.join?.('\n') || e?.message || __('Unknown error')),
    )
    return
  }
  toast.success(__('Status "{0}" deleted', [name]))
  column.column['delete'] = true
  updateColumn()
}

function addColumn(e) {
  let column = columns.value.find((col) => col.column.name == e.value)
  column.column['delete'] = false
  columns.value.splice(columns.value.indexOf(column), 1)
  columns.value.push(column)
  updateColumn()
}

function updateColumn(d, fetchNewColumns = false) {
  let toColumn = d?.to?.dataset.column
  let fromColumn = d?.from?.dataset.column
  let itemName = d?.item?.dataset.name

  let _columns = []
  columns.value.forEach((col) => {
    col.column['order'] = col.data.map((d) => d.name)
    if (col.column.page_length) {
      delete col.column.page_length
    }
    _columns.push(col.column)
  })

  let data = { kanban_columns: _columns, fetchNewColumns }

  if (toColumn != fromColumn) {
    data = { item: itemName, to: toColumn, kanban_columns: _columns }
  }

  emit('update', data)
}
</script>
