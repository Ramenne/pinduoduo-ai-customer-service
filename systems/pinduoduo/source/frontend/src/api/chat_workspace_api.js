import { get, post } from '@/utils/request'
const base = '/chat/workspace'
export const workspaceShops = (params = {}) => get(`${base}/shops`, params)
export const archiveConversations = (params) => get(`${base}/conversations`, params)
export const archiveMessages = (params) => get(`${base}/messages`, params)
export const syncPage = (payload) => post(`${base}/sync`, payload)
export const markConversationRead = (shopPk, uid) => post(`${base}/read`, { shop_pk: shopPk, customer_uid: uid })
export const listPhrases = (shopPk) => get(`${base}/phrases`, { shop_pk: shopPk })
export const savePhrase = (payload) => post(`${base}/phrases`, payload)

export const setAiMode = (shopPk, enabled) => post(`${base}/ai-mode`, { shop_pk: shopPk, enabled })
export const generateAiDraft = (shopPk, uid) => post(`${base}/ai-draft`, { shop_pk: shopPk, customer_uid: uid }, { timeout: 90000 })

export const getConversationControl = (shopPk, uid) => get(`${base}/conversation-control`, { shop_pk: shopPk, customer_uid: uid })
export const setConversationControl = (shopPk, uid, mode, revision) => post(`${base}/conversation-control`, { shop_pk: shopPk, customer_uid: uid, mode, expected_revision: revision })

export const getAiWorkspace = (shopPk, uid) => get(`${base}/ai-workspace`, { shop_pk: shopPk, customer_uid: uid })
export const saveAiSupplement = (shopPk, uid, text, revision, kind = 'fact', answerId = null) => post(`${base}/ai-supplement`, { shop_pk: shopPk, customer_uid: uid, text, expected_revision: revision, kind, answer_id: answerId })
export const generateAiAdvice = (shopPk, uid, instructionId = null) => post(`${base}/ai-advice`, { shop_pk: shopPk, customer_uid: uid, instruction_id: instructionId }, { timeout: 110000 })

export const humanHistory = (params) => get(`${base}/human-history`, params)
export const answerHistory = (params) => get(`${base}/answer-history`, params)
export const harnessOperations = (shopPk) => get(`${base}/operations`, { shop_pk: shopPk })
export const harnessExperiences = (params) => get(`${base}/experiences`, params)
export const experienceVersions = (shopPk, ident) => get(`${base}/experience-versions`, { shop_pk: shopPk, ident })
export const reviewExperience = (payload) => post(`${base}/experience-review`, payload)
export const harnessSummaries = (params) => get(`${base}/summaries`, params)
export const retrySummary = (shopPk, ident) => post(`${base}/summary-retry`, { shop_pk: shopPk, ident })
