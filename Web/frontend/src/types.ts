export interface SessionInfo {
  sessionId: string
  expiresAt: string
}

export interface SpectrumPayload {
  label: string
  shape: [number, number]
  freqs: number[]
  time: number[]
  data: number[][]
  displayMin: number | null
  displayMax: number | null
}

export interface DatasetSummary {
  filename: string
  shape: [number, number]
  freqRangeMHz: [number, number]
  timeRangeSeconds: [number, number]
  utStartSeconds: number | null
  headerSummary: Record<string, string>
  rawSpectrum: SpectrumPayload
}

export interface AnalysisPoint {
  timeChannel: number
  timeSeconds: number
  freqMHz: number
}

export interface MaximaPayload {
  source: 'raw' | 'processed'
  points: AnalysisPoint[]
}

export interface ChartPoint {
  x: number
  y: number
}

export type AnalysisPlotKind =
  | 'best_fit'
  | 'shock_speed_vs_height'
  | 'shock_speed_vs_frequency'
  | 'shock_height_vs_frequency'

export interface AnalysisResult {
  mode: 'fundamental' | 'harmonic'
  fold: 1 | 2 | 3 | 4
  equation: string
  fit: {
    a: number
    b: number
    stdErrs: [number, number]
    r2: number
    rmse: number
  }
  shockSummary: {
    avgFreqMHz: number
    avgFreqErrMHz: number
    avgDriftMHzPerSec: number
    avgDriftErrMHzPerSec: number
    startFreqMHz: number
    startFreqErrMHz: number
    initialShockSpeedKmPerSec: number
    initialShockSpeedErrKmPerSec: number
    initialShockHeightRs: number
    initialShockHeightErrRs: number
    avgShockSpeedKmPerSec: number
    avgShockSpeedErrKmPerSec: number
    avgShockHeightRs: number
    avgShockHeightErrRs: number
    fundamental: boolean
    harmonic: boolean
    fold: 1 | 2 | 3 | 4
  }
  points: AnalysisPoint[]
  plots: {
    bestFit: {
      points: ChartPoint[]
      fitLine: ChartPoint[]
    }
    shockSpeedVsHeight: {
      points: ChartPoint[]
    }
    shockSpeedVsFrequency: {
      points: ChartPoint[]
    }
    shockHeightVsFrequency: {
      points: ChartPoint[]
    }
  }
}

export type FigureFormat = 'png' | 'pdf' | 'eps' | 'svg' | 'tiff'
