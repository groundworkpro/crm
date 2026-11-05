// Ask AI's numbers as a chat message with the math shown, for reps to check against
// their own calculator. The AI never writes to the calculator (Exe, 2026-11-05: "let
// the reps use it").
//
// Takes a 13-step run (`decide`: repairs {tier, per_sf, total}, offers {classic, two_x,
// fee, recommend}) or an older one-shot price card (repair_tier, offers {repairs, ...}).
// The formulas are comping/decide.py step 13's: classic = 70% of value - repairs - fee,
// 2x = 88% of value - 2 x repairs - fee, each rounded to the nearest $1,000.

const TIER_LABEL = {
  clean: 'clean-up',
  paint_carpet: 'paint & carpet',
  kitchen_baths: 'kitchen & baths',
  full_rehab: 'full rehab',
  studs: 'down to studs',
}

const money = (n) => (Number.isFinite(Number(n)) && n !== null && n !== '' ? '$' + Math.round(Number(n)).toLocaleString() : '?')

export function aiCalcText(d, sqft) {
  if (!d?.arv) return ''
  const arv = Number(d.arv)
  const o = d.offers || {}
  const rep = d.repairs || {}
  const total = Number(rep.total ?? o.repairs) || 0
  const tier = rep.tier || d.repair_tier
  const psf = Number(rep.per_sf) || 0
  const fee = Number(o.fee) || 0
  const sf = Number(sqft) || (psf ? total / psf : 0)
  const lines = [__("Ask AI's numbers (for reference; your calculator is yours):")]
  lines.push(
    __('ARV {0}', [money(arv)]) +
      (d.arv_low ? ` (${money(d.arv_low)}–${money(d.arv_high)}` + (d.confidence ? `, ${__('{0} confidence', [d.confidence])})` : ')') : ''),
  )
  const comps = (d.comps_used || []).filter((c) => c?.address).slice(0, 6)
  for (const c of comps) {
    const st = String(c.address).split(',')[0]
    lines.push(
      c.adjusted
        ? `  · ${st}${c.role ? ` (${c.role})` : ''}: ${__('sold {0}, adjusted {1}', [money(c.price), money(c.adjusted)])}`
        : `  · ${st}: ${money(c.price)}`,
    )
  }
  if (total) {
    const how = psf && sf ? `$${psf}/sf × ${Math.round(sf).toLocaleString()} sf = ` : ''
    lines.push(__('Repairs: {0}{1}', [tier ? `${TIER_LABEL[tier] || tier}, ` : '', how + money(total)]))
  }
  if (fee) lines.push(__('Fee: {0}', [money(fee)]))
  // Show the formula only when it gives back the AI's own figure (older cards used 90% for 2x).
  const shown = (label, val, mult, times) => {
    const calc = arv * mult - times * total - fee
    if (total && fee && (val == null || Math.abs(calc - Number(val)) <= 1000))
      return `${label}: ${money(arv)} × ${mult.toFixed(2)} − ${times > 1 ? `${times} × ` : ''}${money(total)} − ${money(fee)} ≈ ${money(val ?? Math.round(calc / 1000) * 1000)}`
    return val != null ? `${label}: ${money(val)}` : ''
  }
  for (const l of [shown(__('Classic 70%'), o.classic, 0.7, 1), shown(__('2× repairs'), o.two_x, 0.88, 2)]) if (l) lines.push(l)
  if (o.recommend) lines.push(__('Leads with: {0}', [o.recommend === 'classic' ? __('Classic 70%') : __('2× repairs')]))
  return lines.join('\n')
}
