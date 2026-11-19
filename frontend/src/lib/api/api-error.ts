export type ApiErrorKind = 'http' | 'network' | 'invalid-response'

const statusMessages: Record<number, string> = {
  401: 'Authentication is required or has expired.',
  403: 'You do not have permission to perform this action.',
  404: 'The requested resource is unavailable.',
  413: 'The request is larger than the server allows.',
  422: 'Please review the submitted values.',
  503: 'The service is temporarily unavailable.',
}

export class ApiError extends Error {
  constructor(
    message: string,
    readonly kind: ApiErrorKind,
    readonly status?: number,
    readonly requestId?: string,
  ) {
    super(message)
    this.name = 'ApiError'
  }

  static fromResponse(status: number, requestId?: string) {
    return new ApiError(
      statusMessages[status] ?? (status >= 500 ? 'The server could not complete the request.' : 'The request could not be completed.'),
      'http',
      status,
      requestId,
    )
  }
}
