// The comps page opened from an Ask AI result ("Finish on comps page" links with
// ?ai=<job id>): load that run and match the AI's comps to the board's rows, for the
// tray's summary. It changes nothing on the page: the AI's comps list apart ("Ask AI's
// comps", useAiComps) and its numbers show in the rail's chat; the rep's picks and
// calculator are theirs (Exe, 2026-11-05).
import { computed, ref, watch } from 'vue'
import { call } from 'frappe-ui'
import { addressMatchKey } from '@/utils/comps'

export function useAiPricerRun({ job, rows }) {
  const run = ref(null)

  watch(
    job,
    async (id) => {
      run.value = null
      if (!id) return
      try {
        const r = await call('crm.api.ai_pricer.get_job', { job: id })
        if (r?.status === 'done' && r.answer?.arv) run.value = r.answer
      } catch {
        /* expired or offline: the page still works without it */
      }
    },
    { immediate: true },
  )

  // comp name -> the AI's line for it; plus the AI comps the board does not have.
  const matched = computed(() => {
    const byKey = new Map()
    for (const c of rows()) {
      const k = addressMatchKey(c.address)
      if (k && !byKey.has(k)) byKey.set(k, c)
    }
    const used = {}
    const missing = []
    for (const a of run.value?.comps_used || []) {
      const c = byKey.get(addressMatchKey(a.address))
      if (c) used[c.name] = a
      else missing.push(a)
    }
    return { used, missing }
  })

  return { run, used: computed(() => matched.value.used), missing: computed(() => matched.value.missing) }
}
