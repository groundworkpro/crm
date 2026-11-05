// Ask AI's comps on the comps page, kept APART from the rep's own picks so the two
// can be compared (Exe, 2026-11-05: "have them separate ... so they can compare
// whether they have the same ones or different ones and they can actually find
// them on the map").
//
// The run is the one the page was opened from (?ai=<job>), else the latest
// finished Ask AI run on the lead by anyone (crm.api.ai_pricer.ai_comps). Each AI
// comp is matched to a board row by street address; one the board does not have
// still gets its own pin when the run carried its lat/lng (13-step runs do).
import { computed, ref, watch } from 'vue'
import { call } from 'frappe-ui'
import { addressMatchKey } from '@/utils/comps'

export function useAiComps({ lead, job, rows, enabled }) {
  const run = ref(null)
  let seq = 0

  async function reload() {
    const mine = ++seq
    if (!enabled() || !lead()) {
      run.value = null
      return
    }
    try {
      const r = await call('crm.api.ai_pricer.ai_comps', { lead: lead(), job: job() || undefined })
      if (mine === seq) run.value = r?.comps?.length ? r : null
    } catch {
      /* Ask AI off for this user, or offline: the page works without it */
      if (mine === seq) run.value = null
    }
  }
  watch([lead, job, enabled], reload, { immediate: true })

  // [{ ...ai line, key, comp (board row or null), state }]
  //   state: 'both' (the rep picked it too), 'unpicked' (on the board, not picked),
  //          'discarded' (the rep threw it out), 'off_board' (not on this board).
  const list = computed(() => {
    const byKey = new Map()
    for (const c of rows()) {
      const k = addressMatchKey(c.address)
      if (k && !byKey.has(k)) byKey.set(k, c)
    }
    return (run.value?.comps || []).map((a, i) => {
      const comp = byKey.get(addressMatchKey(a.address)) || null
      const state = !comp ? 'off_board' : comp.selected ? 'both' : comp.hidden ? 'discarded' : 'unpicked'
      // key: the board pin's name, else the AI-only pin's (`ai:...`)
      const key = !comp ? `ai:${i}` : comp.hidden ? `ai:${comp.name}` : comp.name
      return { ...a, key, comp, state }
    })
  })

  // board comp name -> AI line (for the tray card badge and the pin's AI mark)
  const byName = computed(() => {
    const out = {}
    for (const a of list.value) if (a.comp) out[a.comp.name] = a
    return out
  })

  // The rep's picks the AI did not use: the other half of the comparison.
  const repOnly = computed(() => rows().filter((c) => c.selected && !byName.value[c.name]))

  return { run, list, byName, repOnly, reload }
}
