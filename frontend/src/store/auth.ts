import { create } from 'zustand'
import api from '../api/client'

interface User {
  id: number
  username: string
  role: 'admin' | 'dev' | 'readonly'
}

interface AuthState {
  user: User | null
  token: string | null
  login: (username: string, password: string) => Promise<void>
  logout: () => void
  fetchMe: () => Promise<void>
}

export const useAuthStore = create<AuthState>((set) => ({
  user: null,
  token: localStorage.getItem('token'),
  login: async (username, password) => {
    const res: any = await api.post('/auth/login', { username, password })
    localStorage.setItem('token', res.access_token)
    set({ token: res.access_token, user: { id: res.user_id, username: res.username, role: res.role } })
  },
  logout: () => {
    localStorage.removeItem('token')
    set({ user: null, token: null })
  },
  fetchMe: async () => {
    try {
      const res: any = await api.get('/auth/me')
      set({ user: res })
    } catch {
      localStorage.removeItem('token')
      set({ token: null, user: null })
    }
  },
}))
