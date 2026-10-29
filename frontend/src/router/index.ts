import { createRouter, createWebHistory } from 'vue-router'
import ChatView from '@/views/ChatView.vue'
import KnowledgeView from '@/views/KnowledgeView.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'home', component: ChatView },
    { path: '/c/:id', name: 'chat', component: ChatView },
    { path: '/knowledge', name: 'knowledge', component: KnowledgeView },
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
})

export default router
