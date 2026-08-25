import axios from 'axios'

const http = axios.create({
  baseURL: '/v1',
  timeout: 60000,
})

export interface QueryRequest {
  question: string
  session_id?: string
}

export interface QueryResponse {
  answer: string
  sql?: string
  data?: Record<string, unknown>[]
  columns?: string[]
  cached?: boolean
  error?: string
}

export async function postQuery(req: QueryRequest): Promise<QueryResponse> {
  const { data } = await http.post<QueryResponse>('/query', req)
  return data
}
