// Save pictures that were texted or emailed in on a lead into that lead's
// Photos folder (Google Drive, see crm/api/photos.py).
//
// One shared state per lead, so every thumbnail in the activity feed agrees on
// what is already saved and what is on its way. "Saved" comes from the Drive
// folder itself: each copied picture carries a `crm_source` app property, which
// `get_lead_photos` returns as `source`.
import { globalStore } from '@/stores/global'
import { call, createResource, toast } from 'frappe-ui'
import { computed, reactive } from 'vue'

// Must match media_source_key() in crm/api/photos.py — change both together.
export function sourceKey({ url = '', file = '' } = {}) {
  if (file) return `file:${file}`.slice(0, 100)
  const path = (url || '').split('?')[0].replace(/\/+$/, '')
  return `quo:${path.split('/').pop()}`.slice(0, 100)
}

export function isSavable(type = '') {
  return type.startsWith('image/') || type.startsWith('video/')
}

const states = {}

function stateFor(lead) {
  if (states[lead]) return states[lead]
  const photos = createResource({
    url: 'crm.api.photos.get_lead_photos',
    params: { lead },
    auto: true,
  })
  const state = {
    photos,
    pending: reactive(new Set()),
    // keys saved in this session, so a tile flips to "Saved" before the
    // Drive listing catches up
    justSaved: reactive(new Set()),
  }
  const { $socket } = globalStore()
  $socket?.on('crm_photos', (data) => {
    if (
      data?.reference_doctype === 'CRM Lead' &&
      data?.reference_docname === lead &&
      !state.pending.size
    )
      photos.reload()
  })
  states[lead] = state
  return state
}

export function useLeadPhotoSaves(lead) {
  if (!lead) {
    return {
      enabled: false,
      isSaved: () => false,
      isPending: () => false,
      save: async () => {},
    }
  }
  const state = stateFor(lead)

  const savedKeys = computed(() => {
    const set = new Set(state.justSaved)
    for (const f of state.photos.data?.files || []) if (f.source) set.add(f.source)
    return set
  })

  const isSaved = (item) => savedKeys.value.has(sourceKey(item))
  const isPending = (item) => state.pending.has(sourceKey(item))

  // items: [{ url } | { file }]. The first goes alone so a lead without a
  // folder yet gets exactly one (parallel first requests would each create
  // one); the rest go three at a time.
  async function save(items) {
    const todo = items.filter((i) => !isSaved(i) && !isPending(i))
    if (!todo.length) return
    todo.forEach((i) => state.pending.add(sourceKey(i)))
    let failed = 0
    const one = async (item) => {
      const key = sourceKey(item)
      try {
        await call('crm.api.photos.save_media_to_photos', { lead, ...item })
        state.justSaved.add(key)
      } catch (e) {
        failed += 1
      } finally {
        state.pending.delete(key)
      }
    }
    await one(todo[0])
    const rest = todo.slice(1)
    while (rest.length) await Promise.all(rest.splice(0, 3).map(one))
    state.photos.reload()

    const ok = todo.length - failed
    if (failed)
      toast.error(
        __('{0} of {1} could not be saved to Photos', [failed, todo.length]),
      )
    else
      toast.success(
        ok === 1 ? __('Saved to Photos') : __('{0} saved to Photos', [ok]),
      )
  }

  return { enabled: true, isSaved, isPending, save }
}
