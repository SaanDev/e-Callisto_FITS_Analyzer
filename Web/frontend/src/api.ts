import type {
  AnalysisPoint,
  AnalysisPlotKind,
  AnalysisResult,
  DatasetSummary,
  FigureFormat,
  MaximaPayload,
  SessionInfo,
  SpectrumPayload,
} from './types'

const API_ROOT = (import.meta.env.VITE_API_ROOT as string | undefined)?.replace(/\/$/, '') || '/api/v1'

export class ApiError extends Error {
  status: number

  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

async function parseJson<T>(response: Response): Promise<T> {
  if (!response.ok) {
    let detail = response.statusText
    try {
      const payload = (await response.json()) as { detail?: string }
      if (payload.detail) {
        detail = payload.detail
      }
    } catch {
      // Ignore JSON parse failures for non-JSON error bodies.
    }
    throw new ApiError(response.status, detail)
  }
  return (await response.json()) as T
}

function saveBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  document.body.append(link)
  link.click()
  link.remove()
  URL.revokeObjectURL(url)
}

function filenameFromResponse(response: Response, fallback: string) {
  const header = response.headers.get('content-disposition')
  if (!header) {
    return fallback
  }
  const match = /filename="([^"]+)"/.exec(header)
  return match?.[1] ?? fallback
}

async function download(
  path: string,
  payload: unknown,
  fallbackFilename: string,
): Promise<void> {
  const response = await fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  if (!response.ok) {
    let detail = response.statusText
    try {
      const parsed = (await response.json()) as { detail?: string }
      if (parsed.detail) {
        detail = parsed.detail
      }
    } catch {
      // Keep the default error text.
    }
    throw new ApiError(response.status, detail)
  }
  const blob = await response.blob()
  saveBlob(blob, filenameFromResponse(response, fallbackFilename))
}

export async function createSession(): Promise<SessionInfo> {
  return parseJson<SessionInfo>(
    await fetch(`${API_ROOT}/sessions`, {
      method: 'POST',
    }),
  )
}

export async function uploadDataset(
  sessionId: string,
  file: File,
): Promise<DatasetSummary> {
  const formData = new FormData()
  formData.append('dataset', file)
  return parseJson<DatasetSummary>(
    await fetch(`${API_ROOT}/sessions/${sessionId}/dataset`, {
      method: 'POST',
      body: formData,
    }),
  )
}

export async function runBackgroundReduction(
  sessionId: string,
  clipLow: number,
  clipHigh: number,
): Promise<SpectrumPayload> {
  return parseJson<SpectrumPayload>(
    await fetch(`${API_ROOT}/sessions/${sessionId}/processing/background`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ clipLow, clipHigh }),
    }),
  )
}

export async function extractMaxima(
  sessionId: string,
  source: 'raw' | 'processed',
): Promise<MaximaPayload> {
  return parseJson<MaximaPayload>(
    await fetch(`${API_ROOT}/sessions/${sessionId}/analysis/maxima`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ source }),
    }),
  )
}

export async function runAnalyzer(
  sessionId: string,
  points: AnalysisPoint[],
  mode: 'fundamental' | 'harmonic',
  fold: 1 | 2 | 3 | 4,
): Promise<AnalysisResult> {
  return parseJson<AnalysisResult>(
    await fetch(`${API_ROOT}/sessions/${sessionId}/analysis/fit`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ points, mode, fold }),
    }),
  )
}

export async function exportFigure(
  sessionId: string,
  payload: {
    source: 'raw' | 'processed'
    plotKind: 'spectrum' | 'maxima' | AnalysisPlotKind
    format: FigureFormat
    title?: string
    points?: AnalysisPoint[]
    analysisResult?: AnalysisResult
  },
): Promise<void> {
  await download(`${API_ROOT}/sessions/${sessionId}/exports/figure`, payload, `figure.${payload.format}`)
}

export async function exportFits(
  sessionId: string,
  source: 'raw' | 'processed',
): Promise<void> {
  await download(`${API_ROOT}/sessions/${sessionId}/exports/fits`, { source, bitpix: 'auto' }, `${source}.fit`)
}

export async function exportMaximaCsv(
  sessionId: string,
  points: AnalysisPoint[],
): Promise<void> {
  await download(`${API_ROOT}/sessions/${sessionId}/exports/maxima-csv`, { points }, 'maxima.csv')
}

export async function exportAnalysisXlsx(
  sessionId: string,
  analysisResult: AnalysisResult,
  sourceFilename: string,
): Promise<void> {
  await download(
    `${API_ROOT}/sessions/${sessionId}/exports/analyzer-xlsx`,
    { analysisResult, sourceFilename },
    'analysis.xlsx',
  )
}
