const { useEffect, useMemo, useState } = React;

const apiBase = "";

function useSession() {
  const [sessionId, setSessionId] = useState(null);

  useEffect(() => {
    fetch(`${apiBase}/sessions`, { method: "POST" })
      .then((res) => res.json())
      .then((data) => setSessionId(data.session_id))
      .catch(() => setSessionId(null));
  }, []);

  return sessionId;
}

function TabButton({ active, onClick, children }) {
  return (
    <button className={active ? "tab active" : "tab"} onClick={onClick}>
      {children}
    </button>
  );
}

function FileUploader({ sessionId, onFilesUpdated }) {
  const [files, setFiles] = useState([]);

  useEffect(() => {
    if (!sessionId) return;
    fetch(`${apiBase}/sessions/${sessionId}/files`)
      .then((res) => res.json())
      .then((data) => setFiles(data.files || []));
  }, [sessionId]);

  const handleUpload = async (event) => {
    const file = event.target.files[0];
    if (!file || !sessionId) return;
    const form = new FormData();
    form.append("file", file);
    await fetch(`${apiBase}/sessions/${sessionId}/upload`, {
      method: "POST",
      body: form,
    });
    const updated = await fetch(`${apiBase}/sessions/${sessionId}/files`).then(
      (res) => res.json()
    );
    setFiles(updated.files || []);
    onFilesUpdated(updated.files || []);
  };

  return (
    <div className="card">
      <h3>Upload FITS</h3>
      <input type="file" onChange={handleUpload} />
      <ul className="file-list">
        {files.map((file) => (
          <li key={file}>{file}</li>
        ))}
      </ul>
    </div>
  );
}

function SpectrumViewer({ data, freqs, time }) {
  useEffect(() => {
    const target = document.getElementById("spectrum-plot");
    if (!target || !data || !freqs || !time) return;
    Plotly.react(
      target,
      [
        {
          z: data,
          x: time,
          y: freqs,
          type: "heatmap",
          colorscale: "Viridis",
        },
      ],
      {
        title: "Dynamic Spectrum",
        xaxis: { title: "Time (s)" },
        yaxis: { title: "Frequency (MHz)", autorange: "reversed" },
        dragmode: "pan",
      },
      { responsive: true }
    );
  }, [data, freqs, time]);

  return <div id="spectrum-plot" className="plot" />;
}

function MaxIntensityViewer({ timeChannels, freqs }) {
  useEffect(() => {
    const target = document.getElementById("max-intensity-plot");
    if (!target || !timeChannels || !freqs) return;
    Plotly.react(
      target,
      [
        {
          x: timeChannels,
          y: freqs,
          mode: "markers",
          type: "scatter",
          marker: { color: "#e63946", size: 6 },
        },
      ],
      {
        title: "Maximum Intensities",
        xaxis: { title: "Time Channel" },
        yaxis: { title: "Frequency (MHz)" },
        dragmode: "lasso",
      },
      { responsive: true }
    );
  }, [timeChannels, freqs]);

  return <div id="max-intensity-plot" className="plot" />;
}

function AnalyzerPanel({ analysis }) {
  if (!analysis) {
    return (
      <div className="card">
        <h3>Analyzer Output</h3>
        <p>Run a fit analysis to see metrics.</p>
      </div>
    );
  }

  return (
    <div className="card">
      <h3>Analyzer Output</h3>
      <p>
        <strong>Equation:</strong> {analysis.equation}
      </p>
      <div className="grid">
        <div>
          <strong>R²:</strong> {analysis.r2.toFixed(4)}
        </div>
        <div>
          <strong>RMSE:</strong> {analysis.rmse.toFixed(4)}
        </div>
        <div>
          <strong>Avg. Drift:</strong> {analysis.avg_drift.toFixed(4)}
        </div>
        <div>
          <strong>Avg. Shock Speed:</strong>{" "}
          {analysis.avg_shock_speed.toFixed(2)}
        </div>
      </div>
    </div>
  );
}

function MainPanel() {
  const sessionId = useSession();
  const [files, setFiles] = useState([]);
  const [selectedFile, setSelectedFile] = useState("");
  const [clipLow, setClipLow] = useState(-5);
  const [clipHigh, setClipHigh] = useState(20);
  const [plotData, setPlotData] = useState(null);
  const [plotFreqs, setPlotFreqs] = useState(null);
  const [plotTime, setPlotTime] = useState(null);
  const [maxChannels, setMaxChannels] = useState(null);
  const [maxFreqs, setMaxFreqs] = useState(null);
  const [analysis, setAnalysis] = useState(null);

  const payload = useMemo(
    () =>
      plotData
        ? {
            data: plotData,
            freqs: plotFreqs,
            time: plotTime,
          }
        : null,
    [plotData, plotFreqs, plotTime]
  );

  const runNoiseReduction = async () => {
    if (!selectedFile || !sessionId) return;
    const response = await fetch(`${apiBase}/processing/noise-reduction`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        session_id: sessionId,
        file_name: selectedFile,
        clip_low: Number(clipLow),
        clip_high: Number(clipHigh),
      }),
    }).then((res) => res.json());
    setPlotData(response.data.data);
    setPlotFreqs(response.freqs.data);
    setPlotTime(response.time.data);
  };

  const runMaxIntensity = async () => {
    if (!payload || !sessionId) return;
    const response = await fetch(`${apiBase}/analysis/max-intensity`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        session_id: sessionId,
        file_name: selectedFile,
        data: payload.data ? { data: payload.data } : null,
        freqs: payload.freqs ? { data: payload.freqs } : null,
      }),
    }).then((res) => res.json());
    setMaxChannels(response.time_channels);
    setMaxFreqs(response.max_freqs);
  };

  const runFitAnalysis = async () => {
    if (!maxChannels || !maxFreqs) return;
    const response = await fetch(`${apiBase}/analysis/fit`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        time: { data: maxChannels.map((t) => t * 0.25) },
        freq: { data: maxFreqs },
      }),
    }).then((res) => res.json());
    setAnalysis(response);
  };

  return (
    <div className="panel">
      <div className="header">
        <h2>Dynamic Spectrum Workspace</h2>
        <span className="session">Session: {sessionId || "Loading..."}</span>
      </div>
      <div className="grid-2">
        <FileUploader sessionId={sessionId} onFilesUpdated={setFiles} />
        <div className="card">
          <h3>Controls</h3>
          <label>
            Select file
            <select
              value={selectedFile}
              onChange={(event) => setSelectedFile(event.target.value)}
            >
              <option value="">Choose FITS</option>
              {files.map((file) => (
                <option key={file} value={file}>
                  {file}
                </option>
              ))}
            </select>
          </label>
          <label>
            Clip low ({clipLow})
            <input
              type="range"
              min="-20"
              max="0"
              value={clipLow}
              onChange={(event) => setClipLow(event.target.value)}
            />
          </label>
          <label>
            Clip high ({clipHigh})
            <input
              type="range"
              min="0"
              max="50"
              value={clipHigh}
              onChange={(event) => setClipHigh(event.target.value)}
            />
          </label>
          <div className="button-row">
            <button onClick={runNoiseReduction}>Run Noise Reduction</button>
            <button onClick={runMaxIntensity}>Max Intensities</button>
            <button onClick={runFitAnalysis}>Analyze Fit</button>
          </div>
        </div>
      </div>
      <SpectrumViewer data={plotData} freqs={plotFreqs} time={plotTime} />
      <div className="grid-2">
        <MaxIntensityViewer timeChannels={maxChannels} freqs={maxFreqs} />
        <AnalyzerPanel analysis={analysis} />
      </div>
    </div>
  );
}

function DownloaderPanel() {
  return (
    <div className="panel">
      <h2>FITS Downloader</h2>
      <p>
        Use the API to fetch files, then add them to your session using the
        uploader. This panel mirrors the desktop download workflow.
      </p>
      <div className="card">
        <label>
          Remote URL
          <input type="text" placeholder="https://..." />
        </label>
        <button>Queue Download</button>
      </div>
    </div>
  );
}

function CmePanel() {
  return (
    <div className="panel">
      <h2>CME Catalog Viewer</h2>
      <p>Browse catalog entries and compare against burst timelines.</p>
      <div className="card">
        <div className="placeholder">Catalog results will appear here.</div>
      </div>
    </div>
  );
}

function GoesPanel() {
  return (
    <div className="panel">
      <h2>GOES X-Ray Viewer</h2>
      <p>Inspect GOES flux around event windows.</p>
      <div className="card">
        <div className="placeholder">GOES data plots will render here.</div>
      </div>
    </div>
  );
}

function App() {
  const [tab, setTab] = useState("workspace");

  return (
    <div>
      <header>
        <h1>e-CALLISTO FITS Analyzer</h1>
        <nav>
          <TabButton
            active={tab === "workspace"}
            onClick={() => setTab("workspace")}
          >
            Spectrum
          </TabButton>
          <TabButton
            active={tab === "downloader"}
            onClick={() => setTab("downloader")}
          >
            FITS Downloader
          </TabButton>
          <TabButton active={tab === "cme"} onClick={() => setTab("cme")}>
            CME Catalog
          </TabButton>
          <TabButton active={tab === "goes"} onClick={() => setTab("goes")}>
            GOES X-Ray
          </TabButton>
        </nav>
      </header>
      {tab === "workspace" && <MainPanel />}
      {tab === "downloader" && <DownloaderPanel />}
      {tab === "cme" && <CmePanel />}
      {tab === "goes" && <GoesPanel />}
    </div>
  );
}

ReactDOM.createRoot(document.getElementById("root")).render(<App />);
