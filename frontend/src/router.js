import { createRouter, createWebHistory } from 'vue-router'
import { usersStore } from '@/stores/users'
import { sessionStore } from '@/stores/session'
import { viewsStore } from '@/stores/views'

const routes = [
  ...(import.meta.env.DEV ? [{
    path: '/phone-preview',
    name: 'Phone Preview',
    component: () => import('@/pages/PhonePreview.vue'),
  }, {
    path: '/phone-settings-preview',
    name: 'Phone Settings Preview',
    component: () => import('@/pages/PhoneSettingsPreview.vue'),
  }] : []),
  {
    // Next workspace: a conversation (channel | dm | standup | live) is a page
    // in the main area. pages/Talk.vue gates on workspaceStore.isNext and, in
    // DEV only, falls back to the fictional TalkPreview mockup.
    path: '/talk/:kind/:id',
    name: 'Talk',
    component: () => import('@/pages/Talk.vue'),
  },
  {
    path: '/phone/:number?',
    name: 'Phone Desk',
    component: () => import('@/pages/PhoneDesk.vue'),
  },
  {
    path: '/settings/phone',
    name: 'Phone Settings',
    component: () => import('@/pages/PhoneSettings.vue'),
  },
  {
    path: '/',
    name: 'Home',
  },
  {
    path: '/notifications',
    name: 'Notifications',
    component: () => import('@/pages/MobileNotification.vue'),
  },
  {
    // Shared daily calling board, generated from Dennis's cadence by the 5am
    // standup job. See crm/api/today_board.py.
    path: '/today',
    name: 'Today',
    component: () => import('@/pages/Today.vue'),
  },
  {
    // Groundwork custom leads dashboard (replaces the generic chart dashboard).
    // The upstream Dashboard.vue is kept in the repo but no longer routed.
    path: '/dashboard',
    name: 'Dashboard',
    component: () => import('@/pages/LeadsDashboard.vue'),
  },
  {
    alias: '/leads',
    path: '/leads/view/:viewType?',
    name: 'Leads',
    component: () => import('@/pages/Leads.vue'),
  },
  {
    path: '/leads/:leadId',
    name: 'Lead',
    component: () => import(`@/pages/${handleMobileView('Lead')}.vue`),
    props: true,
  },
  {
    // Opened in a NEW TAB from the lead, so a rep can keep it beside the lead
    // while underwriting rather than losing the lead behind a modal. Distinct
    // from '/leads/:leadId' -- a param never matches across a '/'.
    path: '/leads/:leadId/comps',
    name: 'Comps',
    component: () => import('@/pages/Comps.vue'),
    props: true,
  },
  {
    // The lead desk: the single screen a rep works a live seller call from.
    // Same '/leads/:leadId/<verb>' shape as Comps above, and for the same
    // reason -- a param never matches across a '/', so it cannot shadow
    // '/leads/:leadId'. Desktop only by design; see pages/LeadDesk.vue.
    path: '/leads/:leadId/desk',
    name: 'LeadDesk',
    component: () => import('@/pages/LeadDesk.vue'),
    props: true,
  },
  {
    alias: '/deals',
    path: '/deals/view/:viewType?',
    name: 'Deals',
    component: () => import('@/pages/Deals.vue'),
  },
  {
    path: '/deals/:dealId',
    name: 'Deal',
    component: () => import(`@/pages/${handleMobileView('Deal')}.vue`),
    props: true,
  },
  {
    alias: '/notes',
    path: '/notes/view/:viewType?',
    name: 'Notes',
    component: () => import('@/pages/Notes.vue'),
  },
  {
    alias: '/tasks',
    path: '/tasks/view/:viewType?',
    name: 'Tasks',
    component: () => import('@/pages/Tasks.vue'),
  },
  {
    alias: '/contacts',
    path: '/contacts/view/:viewType?',
    name: 'Contacts',
    component: () => import('@/pages/Contacts.vue'),
  },
  {
    path: '/contacts/:contactId',
    name: 'Contact',
    component: () => import(`@/pages/${handleMobileView('Contact')}.vue`),
    props: true,
  },
  {
    path: '/buyers',
    name: 'Buyers',
    component: () => import('@/pages/Buyers.vue'),
  },
  {
    path: '/contractors',
    name: 'Contractors',
    component: () => import('@/pages/Contractors.vue'),
  },
  {
    path: '/buyers/:buyerId',
    name: 'Buyer',
    component: () => import('@/pages/Buyer.vue'),
    props: true,
  },
  {
    path: '/dispo/:leadId?',
    name: 'Dispo',
    component: () => import('@/pages/Dispo.vue'),
    props: true,
  },
  {
    alias: '/organizations',
    path: '/organizations/view/:viewType?',
    name: 'Organizations',
    component: () => import('@/pages/Organizations.vue'),
  },
  {
    path: '/organizations/:organizationId',
    name: 'Organization',
    component: () => import(`@/pages/${handleMobileView('Organization')}.vue`),
    props: true,
  },
  {
    alias: '/call-logs',
    path: '/call-logs/view/:viewType?',
    name: 'Call Logs',
    component: () => import('@/pages/CallLogs.vue'),
  },
  {
    path: '/data-import',
    name: 'DataImportList',
    component: () => import('@/pages/DataImport.vue'),
  },
  {
    path: '/data-import/doctype/:doctype',
    name: 'NewDataImport',
    component: () => import('@/pages/DataImport.vue'),
    props: true,
  },
  {
    path: '/data-import/:importName',
    name: 'DataImport',
    component: () => import('@/pages/DataImport.vue'),
    props: true,
  },
  {
    path: '/refunds',
    name: 'Refunds',
    component: () => import('@/pages/Refunds.vue'),
  },
  {
    // Scratch properties: comps + calcs on a house that is not a lead.
    path: '/properties',
    name: 'Properties',
    component: () => import('@/pages/Properties.vue'),
  },
  {
    path: '/properties/:propertyId',
    name: 'Property',
    component: () => import('@/pages/Property.vue'),
    props: true,
  },
  {
    path: '/practice',
    name: 'Practice',
    component: () => import('@/pages/Practice.vue'),
  },
  {
    path: '/practice/:setId',
    name: 'PracticeSet',
    component: () => import('@/pages/PracticeSet.vue'),
    props: true,
  },
  {
    path: '/practice/:setId/run/:attemptId',
    name: 'PracticeRun',
    component: () => import('@/pages/PracticeRun.vue'),
    props: true,
  },
  {
    path: '/sequences',
    name: 'Sequences',
    component: () => import('@/pages/Sequences.vue'),
  },
  {
    path: '/sequences/:sequenceId',
    name: 'Sequence',
    component: () => import('@/pages/Sequence.vue'),
    props: true,
  },
  {
    path: '/texts',
    name: 'Text Messages',
    component: () => import('@/pages/TextMessages.vue'),
  },
  {
    path: '/reports/calls',
    name: 'Call Review',
    component: () => import('@/pages/CallReview.vue'),
  },
  {
    path: '/welcome',
    name: 'Welcome',
    component: () => import('@/pages/Welcome.vue'),
  },
  {
    path: '/:invalidpath',
    name: 'Invalid Page',
    component: () => import('@/pages/InvalidPage.vue'),
  },
  {
    path: '/not-permitted',
    name: 'Not Permitted',
    component: () => import('@/pages/NotPermitted.vue'),
  },
]

const handleMobileView = (componentName) => {
  return window.innerWidth < 768 ? `Mobile${componentName}` : componentName
}

let router = createRouter({
  history: createWebHistory('/crm'),
  routes,
})

router.beforeEach(async (to, from, next) => {
  router.previousRoute = from

  const { isLoggedIn } = sessionStore()
  const { users, isCrmUser } = usersStore()

  if (isLoggedIn && !users.fetched) {
    try {
      await users.promise
    } catch (error) {
      console.error('Error loading users', error)
    }
  }

  if (isLoggedIn && to.name !== 'Not Permitted' && !isCrmUser()) {
    next({ name: 'Not Permitted' })
  } else if (to.name === 'Home' && isLoggedIn) {
    // Groundwork: the custom leads dashboard is the landing page.
    next({ name: 'Dashboard' })
  } else if (!isLoggedIn) {
    window.location.href = '/login?redirect-to=/crm'
  } else if (to.matched.length === 0) {
    next({ name: 'Invalid Page' })
  } else if (['Deal', 'Lead'].includes(to.name) && !to.hash) {
    let storageKey = to.name === 'Deal' ? 'lastDealTab' : 'lastLeadTab'
    const activeTab = localStorage.getItem(storageKey) || 'activity'
    const hash = '#' + activeTab
    next({ ...to, hash })
  } else if (
    [
      'Leads',
      'Deals',
      'Contacts',
      'Organizations',
      'Notes',
      'Tasks',
      'Call Logs',
    ].includes(to.name) &&
    !to.query?.view
  ) {
    const { views, standardViews, getDefaultView } = viewsStore()
    await views.promise

    const viewType = to.params?.viewType ?? ''
    const standardViewTypes = ['list', 'kanban', 'group_by']

    if (!viewType) {
      const doctypeMap = {
        Leads: 'CRM Lead',
        Deals: 'CRM Deal',
        Contacts: 'Contact',
        Organizations: 'CRM Organization',
        Notes: 'FCRM Note',
        Tasks: 'CRM Task',
        'Call Logs': 'CRM Call Log',
      }

      const doctype = doctypeMap[to.name]
      let defaultViewType = 'list'

      let globalDefault = getDefaultView()
      if (globalDefault && globalDefault.route_name === to.name) {
        defaultViewType = globalDefault.type || 'list'
        if (globalDefault.name && !globalDefault.is_standard) {
          next({
            name: to.name,
            params: { viewType: defaultViewType },
            query: { ...to.query, view: globalDefault.name },
          })
          return
        }
      }

      for (const viewType of standardViewTypes) {
        const standardView = standardViews.value?.[doctype + ' ' + viewType]
        if (standardView?.is_default) {
          defaultViewType = viewType
          break
        }
      }

      next({
        name: to.name,
        params: { viewType: defaultViewType },
        query: to.query,
      })
    } else if (!standardViewTypes.includes(viewType)) {
      const viewNameOrLabel = viewType

      let view = views.data?.find(
        (v) => v.name == viewNameOrLabel || v.label === viewNameOrLabel,
      )

      if (view) {
        next({
          name: to.name,
          params: { viewType: view.type || 'list' },
          query: { ...to.query, view: view.name },
        })
      } else {
        next({
          name: to.name,
          params: { viewType: 'list' },
          query: to.query,
        })
      }
    } else {
      next()
    }
  } else {
    next()
  }
})

export default router
