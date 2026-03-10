import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import App from './App'

vi.mock('react-plotly.js', () => ({
  default: () => <div data-testid="plotly-stub">plot</div>,
}))

function jsonResponse(payload: unknown, status = 200) {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

function binaryResponse(
  body: string,
  contentType: string,
  filename: string,
  status = 200,
) {
  return new Response(body, {
    status,
    headers: {
      'Content-Type': contentType,
      'Content-Disposition': `attachment; filename="${filename}"`,
    },
  })
}

describe('App', () => {
  beforeEach(() => {
    cleanup()
    vi.restoreAllMocks()
    vi.stubGlobal('fetch', vi.fn())
    vi.stubGlobal('URL', {
      createObjectURL: vi.fn(() => 'blob:test'),
      revokeObjectURL: vi.fn(),
    })
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
  })

  it('creates a session, uploads a .fit file, and only shows the reduced viewer workflow', async () => {
    const fetchMock = vi.mocked(fetch)
    fetchMock
      .mockResolvedValueOnce(jsonResponse({ sessionId: 'session-1', expiresAt: '2026-03-10T12:00:00Z' }))
      .mockResolvedValueOnce(
        jsonResponse({
          filename: 'demo.fit',
          shape: [4, 6],
          freqRangeMHz: [10, 40],
          timeRangeSeconds: [0, 5],
          utStartSeconds: 1,
          headerSummary: { 'TIME-OBS': '00:00:01' },
          rawSpectrum: {
            label: 'Raw Spectrum',
            shape: [4, 6],
            freqs: [10, 20, 30, 40],
            time: [0, 1, 2, 3, 4, 5],
            data: [[1, 2, 3, 4, 5, 6]],
            displayMin: 1,
            displayMax: 6,
          },
        }),
      )
      .mockResolvedValueOnce(
        jsonResponse({
          label: 'Background Subtracted',
          shape: [4, 6],
          freqs: [10, 20, 30, 40],
          time: [0, 1, 2, 3, 4, 5],
          data: [[1, 2, 3, 4, 5, 6]],
          displayMin: -2,
          displayMax: 2,
        }),
      )

    render(<App />)

    const [input] = await screen.findAllByLabelText('Select FITS file')
    expect(input).toHaveAttribute('accept', '.fit,.fits,.fit.gz,.fits.gz')
    await userEvent.upload(input, new File(['fit-data'], 'demo.fit', { type: 'application/fits' }))

    expect(await screen.findByText('Loaded file: demo.fit')).toBeInTheDocument()
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/v1/sessions/session-1/processing/background',
        expect.objectContaining({ method: 'POST' }),
      ),
    )

    expect(screen.queryByText('Upload & Metadata')).not.toBeInTheDocument()
    expect(screen.queryByText('Analyzer')).not.toBeInTheDocument()
    expect(screen.getByText('Noise Reduction')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Export Processed FITS' })).toBeEnabled()
  })

  it('supports .fit.gz uploads, slider updates, and exports', async () => {
    const fetchMock = vi.mocked(fetch)
    fetchMock
      .mockResolvedValueOnce(jsonResponse({ sessionId: 'session-2', expiresAt: '2026-03-10T12:00:00Z' }))
      .mockResolvedValueOnce(
        jsonResponse({
          filename: 'demo.fit.gz',
          shape: [4, 6],
          freqRangeMHz: [10, 40],
          timeRangeSeconds: [0, 5],
          utStartSeconds: 1,
          headerSummary: {},
          rawSpectrum: {
            label: 'Raw Spectrum',
            shape: [4, 6],
            freqs: [10, 20, 30, 40],
            time: [0, 1, 2, 3, 4, 5],
            data: [[1, 2, 3, 4, 5, 6]],
            displayMin: 1,
            displayMax: 6,
          },
        }),
      )
      .mockResolvedValueOnce(
        jsonResponse({
          label: 'Background Subtracted',
          shape: [4, 6],
          freqs: [10, 20, 30, 40],
          time: [0, 1, 2, 3, 4, 5],
          data: [[1, 2, 3, 4, 5, 6]],
          displayMin: -2,
          displayMax: 2,
        }),
      )
      .mockResolvedValueOnce(
        jsonResponse({
          label: 'Background Subtracted',
          shape: [4, 6],
          freqs: [10, 20, 30, 40],
          time: [0, 1, 2, 3, 4, 5],
          data: [[1, 2, 3, 4, 5, 6]],
          displayMin: -1,
          displayMax: 1,
        }),
      )
      .mockResolvedValueOnce(binaryResponse('figure-data', 'image/png', 'processed.png'))
      .mockResolvedValueOnce(binaryResponse('fits-data', 'application/fits', 'demo_background_subtracted.fit'))

    render(<App />)

    const [input] = await screen.findAllByLabelText('Select FITS file')
    await userEvent.upload(input, new File(['gz-fit-data'], 'demo.fit.gz', { type: 'application/gzip' }))

    expect(await screen.findByText('Loaded file: demo.fit.gz')).toBeInTheDocument()
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/v1/sessions/session-2/processing/background',
        expect.objectContaining({ method: 'POST' }),
      ),
    )

    const highClip = screen.getByRole('spinbutton', { name: 'High clip value' })
    await userEvent.clear(highClip)
    await userEvent.type(highClip, '10')

    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some(([url, init]) => {
          if (url !== '/api/v1/sessions/session-2/processing/background') {
            return false
          }
          const body = init && 'body' in init ? init.body : null
          if (typeof body !== 'string') {
            return false
          }
          const parsed = JSON.parse(body) as { clipHigh?: number }
          return parsed.clipHigh === 10
        }),
      ).toBe(true),
    )

    await userEvent.click(screen.getByRole('button', { name: 'View Processed' }))
    await userEvent.click(screen.getByRole('button', { name: 'Export Spectrum Figure' }))
    await userEvent.click(screen.getByRole('button', { name: 'Export Processed FITS' }))

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/sessions/session-2/exports/figure',
      expect.objectContaining({ method: 'POST' }),
    )
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/sessions/session-2/exports/fits',
      expect.objectContaining({ method: 'POST' }),
    )
    expect(await screen.findByText('Processed FITS downloaded.')).toBeInTheDocument()
  })
})
