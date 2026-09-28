import LucideLayoutDashboard from '~icons/lucide/layout-dashboard'
import LeadsIcon from '@/components/Icons/LeadsIcon.vue'
import DealsIcon from '@/components/Icons/DealsIcon.vue'
import ContactsIcon from '@/components/Icons/ContactsIcon.vue'
import OrganizationsIcon from '@/components/Icons/OrganizationsIcon.vue'
import NoteIcon from '@/components/Icons/NoteIcon.vue'
import TaskIcon from '@/components/Icons/TaskIcon.vue'
import PhoneIcon from '@/components/Icons/PhoneIcon.vue'
import CommentIcon from '@/components/Icons/CommentIcon.vue'
import StepsIcon from '@/components/Icons/StepsIcon.vue'
import LucideHeadphones from '~icons/lucide/headphones'
import LucideColumns3 from '~icons/lucide/columns-3'
import LucideUsersRound from '~icons/lucide/users-round'
import LucideListChecks from '~icons/lucide/list-checks'
import LucideCircleDollarSign from '~icons/lucide/circle-dollar-sign'
import LucideTarget from '~icons/lucide/target'
import LucideMapPinHouse from '~icons/lucide/map-pin-house'
import LucideHardHat from '~icons/lucide/hard-hat'

// Read the logged-in user from the session cookie directly (no store import — the
// session store imports the router, which imports this module → circular).
export function currentUser() {
  const cookies = new URLSearchParams(document.cookie.split('; ').join('&'))
  return cookies.get('user_id')
}

// canonical module list for the left sidebar; admin-defined order/visibility
// lives in FCRM Settings.custom_sidebar_items as [{label, hidden}]
export const sidebarLinks = [
  // The shared daily calling board. Sits above Dashboard deliberately: it is the
  // first thing the setters open each morning and the surface the 5am standup
  // DM describes.
  { label: 'Today', icon: LucideListChecks, to: 'Today' },
  ...(import.meta.env.DEV ? [{ label: 'Phone Preview', icon: PhoneIcon, to: 'Phone Preview' }] : []),
  { label: 'Practice', icon: LucideTarget, to: 'Practice' },
  { label: 'Dashboard', icon: LucideLayoutDashboard, to: 'Dashboard' },
  { label: 'Leads', icon: LeadsIcon, to: 'Leads' },
  { label: 'Properties', icon: LucideMapPinHouse, to: 'Properties' },
  { label: 'Refunds', icon: LucideCircleDollarSign, to: 'Refunds' },
  { label: 'Deals', icon: DealsIcon, to: 'Deals' },
  { label: 'Dispo', icon: LucideColumns3, to: 'Dispo' },
  { label: 'Buyers', icon: LucideUsersRound, to: 'Buyers' },
  { label: 'Contractors', icon: LucideHardHat, to: 'Contractors' },
  { label: 'Contacts', icon: ContactsIcon, to: 'Contacts' },
  { label: 'Organizations', icon: OrganizationsIcon, to: 'Organizations' },
  { label: 'Notes', icon: NoteIcon, to: 'Notes' },
  { label: 'Tasks', icon: TaskIcon, to: 'Tasks' },
  { label: 'Call Logs', icon: PhoneIcon, to: 'Call Logs' },
  { label: 'Text Messages', icon: CommentIcon, to: 'Text Messages' },
  { label: 'Sequences', icon: StepsIcon, to: 'Sequences' },
  // Open to the whole sales team — the backend gate in crm/api/reports.py
  // (ALLOWED_REPORT_ROLES) is the one that actually enforces access.
  { label: 'Call Review', icon: LucideHeadphones, to: 'Call Review' },
]

// merge the saved [{label, hidden}] config with the canonical list:
// saved order wins, hidden entries drop out, modules added in later
// releases (absent from the saved config) append at the end, visible
export function applySidebarConfig(configJSON) {
  let config = []
  try {
    config = JSON.parse(configJSON || '[]')
  } catch (e) {
    config = []
  }
  if (!Array.isArray(config) || !config.length) return sidebarLinks

  const remaining = new Map(sidebarLinks.map((l) => [l.label, l]))
  const ordered = []
  for (const item of config) {
    const link = remaining.get(item.label)
    if (!link) continue
    remaining.delete(item.label)
    if (!item.hidden) ordered.push(link)
  }
  ordered.push(...remaining.values())
  return ordered
}
