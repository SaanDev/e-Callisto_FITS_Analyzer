import { type ChangeEvent, startTransition, useDeferredValue, useEffect, useState } from 'react'
import Plot from 'react-plotly.js'
import { ApiError, createSession, exportFigure, exportFits, runBackgroundReduction, uploadDataset } from './api'
import type { DatasetSummary, FigureFormat, SessionInfo, SpectrumPayload } from './types'
import './App.css'

const DEFAULT_LOW = -5
const DEFAULT_HIGH = 20

function App() {
  const [session, setSession] = useState<SessionInfo | null>(null)
  const [dataset, setDataset] = useState<DatasetSummary | null>(null)
  const [rawSpectrum, setRawSpectrum] = useState<SpectrumPayload | null>(null)
  const [processedSpectrum, setProcessedSpectrum] = useState<SpectrumPayload | null>(null)
  const [viewerSource, setViewerSource] = useState<'raw' | 'processed'>('raw')
  const [clipLow, setClipLow] = useState(DEFAULT_LOW)
  const [clipHigh, setClipHigh] = useState(DEFAULT_HIGH)
  const deferredClipLow = useDeferredValue(clipLow)
  const deferredClipHigh = useDeferredValue(clipHigh)
  const [figureFormat, setFigureFormat] = useState<FigureFormat>('png')
  const [spectrumTitle, setSpectrumTitle] = useState('')
  const [statusText, setStatusText] = useState('Opening anonymous session...')
  const [errorText, setErrorText] = useState<string | null>(null)
  const [busyLabel, setBusyLabel] = useState<string | null>('Opening session')

  useEffect(() => {
    void bootstrapSession()
  }, [])

  useEffect(() => {
    if (!session || !rawSpectrum) {
      return
    }

    let cancelled = false
    const timerId = window.setTimeout(async () => {
      try {
        setBusyLabel('Updating background reduction')
        const next = await runBackgroundReduction(session.sessionId, deferredClipLow, deferredClipHigh)
        if (cancelled) {
          return
        }
        startTransition(() => {
          setProcessedSpectrum(next)
        })
        setStatusText(`Background reduced with limits ${deferredClipLow} to ${deferredClipHigh}.`)
      } catch (error) {
        if (cancelled) {
          return
        }
        handleApiError(error, 'Background reduction failed.')
      } finally {
        if (!cancelled) {
          setBusyLabel(null)
        }
      }
    }, 320)

    return () => {
      cancelled = true
      window.clearTimeout(timerId)
    }
  }, [session, rawSpectrum, deferredClipLow, deferredClipHigh])

  async function bootstrapSession(): Promise<SessionInfo | null> {
    try {
      setBusyLabel('Opening session')
      const created = await createSession()
      startTransition(() => {
        setSession(created)
        setDataset(null)
        setRawSpectrum(null)
        setProcessedSpectrum(null)
        setViewerSource('raw')
      })
      setStatusText(`Session active until ${new Date(created.expiresAt).toLocaleTimeString()}.`)
      setErrorText(null)
      return created
    } catch (error) {
      handleApiError(error, 'Could not open a working session.')
      return null
    } finally {
      setBusyLabel(null)
    }
  }

  async function handleUpload(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0]
    if (!file) {
      return
    }
    let activeSession = session
    if (!activeSession) {
      activeSession = await bootstrapSession()
    }
    if (!activeSession) {
      return
    }

    try {
      setBusyLabel('Uploading FITS file')
      const summary = await uploadDataset(activeSession.sessionId, file)
      startTransition(() => {
        setDataset(summary)
        setRawSpectrum(summary.rawSpectrum)
        setProcessedSpectrum(null)
        setViewerSource('raw')
      })
      setStatusText(`Loaded ${summary.filename} (${summary.shape[0]} x ${summary.shape[1]}).`)
      setErrorText(null)
    } catch (error) {
      handleApiError(error, 'The FITS upload failed.')
    } finally {
      setBusyLabel(null)
      event.target.value = ''
    }
  }

  async function downloadSpectrumFigure() {
    if (!session || !rawSpectrum) {
      return
    }
    try {
      setBusyLabel('Exporting spectrum figure')
      await exportFigure(session.sessionId, {
        source: viewerSource,
        plotKind: 'spectrum',
        format: figureFormat,
        title: spectrumTitle || undefined,
      })
      setStatusText('Spectrum figure downloaded.')
    } catch (error) {
      handleApiError(error, 'Could not export the spectrum figure.')
    } finally {
      setBusyLabel(null)
    }
  }

  async function downloadFitsFile(source: 'raw' | 'processed') {
    if (!session || (source === 'processed' && !processedSpectrum) || (source === 'raw' && !rawSpectrum)) {
      return
    }
    try {
      setBusyLabel(`Exporting ${source} FITS`)
      await exportFits(session.sessionId, source)
      setStatusText(`${source === 'processed' ? 'Processed' : 'Raw'} FITS downloaded.`)
    } catch (error) {
      handleApiError(error, 'Could not export the FITS file.')
    } finally {
      setBusyLabel(null)
    }
  }

  function handleApiError(error: unknown, fallback: string) {
    const nextMessage = error instanceof ApiError ? error.message : fallback
    setErrorText(nextMessage)
    setStatusText(fallback)
    if (error instanceof ApiError && (error.status === 404 || error.status === 410)) {
      void bootstrapSession()
    }
  }

  const activeSpectrum =
    viewerSource === 'processed' && processedSpectrum ? processedSpectrum : rawSpectrum

  return (
    <div className="app-shell">
      <header className="page-header">
        <div>
          <p className="eyebrow">Standalone Web Workspace</p>
          <h1>e-CALLISTO FITS Viewer</h1>
          <p className="lede">
            Upload a `.fit`, `.fit.gz`, `.fits`, or `.fits.gz` file, adjust the background-reduction
            sliders, inspect the spectrum, and export the current result.
          </p>
        </div>
        <button className="ghost-button" type="button" onClick={() => void bootstrapSession()}>
          New Session
        </button>
      </header>

      <section className="status-bar" aria-live="polite">
        <span>{busyLabel ?? statusText}</span>
        {session ? <span>Session: {session.sessionId.slice(0, 8)}</span> : null}
      </section>

      {errorText ? (
        <section className="error-banner" role="alert">
          {errorText}
        </section>
      ) : null}

      <div className="workspace-grid">
        <aside className="side-column">
          <Panel title="Upload">
            <label className="field-label" htmlFor="fits-upload">
              Select FITS file
            </label>
            <input
              id="fits-upload"
              aria-label="Select FITS file"
              className="file-input"
              type="file"
              accept=".fit,.fits,.fit.gz,.fits.gz"
              onChange={(event) => void handleUpload(event)}
            />

            <p className="muted-copy">Supported formats: `.fit`, `.fit.gz`, `.fits`, `.fits.gz`.</p>
            <p className="muted-copy">
              {dataset ? `Loaded file: ${dataset.filename}` : 'Upload a FITS file to activate the viewer and exports.'}
            </p>
          </Panel>

          <Panel title="Noise Reduction">
            <div className="field-group">
              <label className="field-label" htmlFor="clip-low">
                Low clip
              </label>
              <div className="range-row">
                <input
                  id="clip-low"
                  type="range"
                  min={-50}
                  max={50}
                  value={clipLow}
                  onChange={(event) => setClipLow(Number(event.target.value))}
                  disabled={!rawSpectrum}
                />
                <input
                  aria-label="Low clip value"
                  className="numeric-input"
                  type="number"
                  value={clipLow}
                  onChange={(event) => setClipLow(Number(event.target.value))}
                  disabled={!rawSpectrum}
                />
              </div>
            </div>

            <div className="field-group">
              <label className="field-label" htmlFor="clip-high">
                High clip
              </label>
              <div className="range-row">
                <input
                  id="clip-high"
                  type="range"
                  min={-50}
                  max={80}
                  value={clipHigh}
                  onChange={(event) => setClipHigh(Number(event.target.value))}
                  disabled={!rawSpectrum}
                />
                <input
                  aria-label="High clip value"
                  className="numeric-input"
                  type="number"
                  value={clipHigh}
                  onChange={(event) => setClipHigh(Number(event.target.value))}
                  disabled={!rawSpectrum}
                />
              </div>
            </div>

            <div className="toggle-row">
              <button
                type="button"
                className={viewerSource === 'raw' ? 'chip chip-active' : 'chip'}
                onClick={() => setViewerSource('raw')}
                disabled={!rawSpectrum}
              >
                View Raw
              </button>
              <button
                type="button"
                className={viewerSource === 'processed' ? 'chip chip-active' : 'chip'}
                onClick={() => setViewerSource('processed')}
                disabled={!processedSpectrum}
              >
                View Processed
              </button>
            </div>

            <p className="muted-copy">
              Background reduction runs automatically after upload and whenever you change either slider.
            </p>
          </Panel>

          <Panel title="Export">
            <div className="field-group">
              <label className="field-label" htmlFor="spectrum-title">
                Spectrum figure title
              </label>
              <input
                id="spectrum-title"
                className="text-input"
                value={spectrumTitle}
                onChange={(event) => setSpectrumTitle(event.target.value)}
                placeholder="Leave blank for defaults"
              />
            </div>

            <div className="field-group">
              <label className="field-label" htmlFor="figure-format">
                Figure format
              </label>
              <select
                id="figure-format"
                className="select-input"
                value={figureFormat}
                onChange={(event) => setFigureFormat(event.target.value as FigureFormat)}
              >
                <option value="png">PNG</option>
                <option value="pdf">PDF</option>
                <option value="eps">EPS</option>
                <option value="svg">SVG</option>
                <option value="tiff">TIFF</option>
              </select>
            </div>

            <div className="button-grid">
              <button type="button" onClick={() => void downloadSpectrumFigure()} disabled={!activeSpectrum}>
                Export Spectrum Figure
              </button>
              <button type="button" onClick={() => void downloadFitsFile('raw')} disabled={!rawSpectrum}>
                Export Raw FITS
              </button>
              <button
                type="button"
                onClick={() => void downloadFitsFile('processed')}
                disabled={!processedSpectrum}
              >
                Export Processed FITS
              </button>
            </div>
          </Panel>
        </aside>

        <main className="main-column">
          <Panel title="Spectrum Viewer">
            <p className="muted-copy">
              {activeSpectrum
                ? `Viewing ${viewerSource === 'processed' && processedSpectrum ? 'processed' : 'raw'} spectrum.`
                : 'Upload a FITS file to render the spectrum.'}
            </p>

            <Plot
              data={[
                {
                  z: activeSpectrum?.data ?? [],
                  x: activeSpectrum?.time ?? [],
                  y: activeSpectrum?.freqs ?? [],
                  type: 'heatmap',
                  colorscale: 'Turbo',
                  hovertemplate:
                    'Time %{x:.2f}s<br>Frequency %{y:.2f} MHz<br>Intensity %{z:.2f}<extra></extra>',
                },
              ]}
              layout={{
                autosize: true,
                height: 720,
                paper_bgcolor: '#fefcf6',
                plot_bgcolor: '#fffdf8',
                margin: { l: 68, r: 20, t: 36, b: 56 },
                title: activeSpectrum?.label ?? 'Waiting for FITS upload',
                xaxis: { title: 'Time (s)' },
                yaxis: { title: 'Frequency (MHz)', autorange: 'reversed' },
              }}
              config={{ responsive: true, displaylogo: false }}
              style={{ width: '100%' }}
            />
          </Panel>
        </main>
      </div>
    </div>
  )
}

function Panel(props: { title: string; children: React.ReactNode }) {
  return (
    <section className="panel">
      <div className="panel-heading">
        <h2>{props.title}</h2>
      </div>
      <div className="panel-body">{props.children}</div>
    </section>
  )
}

export default App
