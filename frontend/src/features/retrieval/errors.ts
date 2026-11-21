import { ApiError } from '@/lib/api/api-error'

export function safeKnowledgeError(error: unknown, activity: 'search' | 'answer'): string {
  let message: string
  if (error instanceof ApiError) {
    switch (error.status) {
      case 401: message = 'Your sign-in is required or has expired.'; break
      case 403: message = 'You do not have access to use this tenant workspace.'; break
      case 404: message = 'This tenant workspace is unavailable.'; break
      case 422: message = 'The query or result count is invalid. Check the form and try again.'; break
      case 500: message = 'The service could not complete this request.'; break
      case 503: message = activity === 'search' ? 'Retrieval is temporarily unavailable.' : 'Answer generation is temporarily unavailable.'; break
      default: message = activity === 'search' ? 'Document search could not be completed.' : 'An answer could not be generated.'
    }
    return error.requestId ? `${message} Support reference: ${error.requestId}` : message
  }
  return activity === 'search' ? 'Document search could not be completed. Check your connection and retry.' : 'An answer could not be generated. Check your connection and retry.'
}
