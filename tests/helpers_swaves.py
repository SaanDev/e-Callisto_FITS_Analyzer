"""
e-CALLISTO FITS Analyzer
Version 2.8.0
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

from __future__ import annotations

from datetime import date, datetime

import numpy as np

FILL = -1.0e31

# The real product: 48 log-spaced LFR channels (2.61-153.36 kHz) followed by
# 319 linearly spaced HFR channels that restart at 125 kHz, so the axis is only
# piecewise monotonic.
LFR_COUNT = 48
HFR_COUNT = 319
CHANNEL_COUNT = LFR_COUNT + HFR_COUNT


def make_frequency_axis() -> np.ndarray:
    lfr = np.logspace(np.log10(2.61), np.log10(153.36), LFR_COUNT)
    hfr = 125.0 + 50.0 * np.arange(HFR_COUNT, dtype=float)
    return np.concatenate([lfr, hfr]).astype(np.float32)


def make_epochs(day: date, count: int = 1440) -> np.ndarray:
    """CDF_EPOCH values on the archive's one-minute, :30-centred cadence."""
    from cdflib.epochs import CDFepoch

    spec = []
    for index in range(count):
        minute_of_day = index
        spec.append(
            [day.year, day.month, day.day, minute_of_day // 60, minute_of_day % 60, 30, 0]
        )
    return np.atleast_1d(CDFepoch.compute_epoch(spec))


def make_intensity(
    freqs_khz: np.ndarray,
    n_time: int,
    *,
    seed: int = 0,
    drift: bool = True,
) -> np.ndarray:
    """A (n_time, n_freq) array shaped like a drifting interplanetary burst."""
    rng = np.random.default_rng(seed)
    times = np.arange(n_time, dtype=float)
    data = rng.normal(2.0, 0.4, size=(n_time, freqs_khz.size))
    if drift:
        for column, freq in enumerate(freqs_khz):
            # Lower frequencies peak later, as a real type III does.
            peak = 20.0 + 900.0 / max(freq, 1.0)
            data[:, column] += 22.0 * np.exp(-0.5 * ((times - peak) / 12.0) ** 2)
    return data.astype(np.float32)


def make_background(freqs_khz: np.ndarray, n_time: int) -> np.ndarray:
    """Receiver background in normal frequency order.

    Includes the sharp LFR->HFR step the real instrument shows at the band
    boundary (~56 dB in the archive files), because that step is the signal
    the orientation guard keys on.
    """
    profile = 80.0 - 12.0 * np.log10(np.maximum(freqs_khz, 1.0))
    profile = np.asarray(profile, dtype=float)
    profile[LFR_COUNT:] += 56.0
    return np.tile(profile, (n_time, 1)).astype(np.float32)


class FakeCDF:
    """Minimal stand-in for ``cdflib.CDF`` exposing only ``varget``."""

    def __init__(self, variables: dict[str, np.ndarray]):
        self._variables = dict(variables)

    def varget(self, name: str):
        try:
            return self._variables[name]
        except KeyError as exc:
            raise ValueError(f"no variable named {name!r}") from exc


def build_fake_cdf(
    day: date,
    *,
    n_time: int = 60,
    ahead: bool = True,
    behind: bool = False,
    reverse_ahead_rows: bool = False,
    seed: int = 0,
) -> FakeCDF:
    """Assemble a FakeCDF holding the variables the reader asks for."""
    freqs = make_frequency_axis()
    epochs = make_epochs(day, n_time)

    variables: dict[str, np.ndarray] = {"Epoch": epochs, "frequency": freqs}

    for craft, present, offset in (("ahead", ahead, 0), ("behind", behind, 7)):
        if present:
            intensity = make_intensity(freqs, n_time, seed=seed + offset)
            background = make_background(freqs, n_time)
            if craft == "ahead" and reverse_ahead_rows:
                # A file's intensity and background always share a row order,
                # so a mirrored file mirrors both.
                intensity = intensity[:, ::-1]
                background = background[:, ::-1]
        else:
            intensity = np.full((n_time, freqs.size), FILL, dtype=np.float32)
            background = np.full((n_time, freqs.size), FILL, dtype=np.float32)
        variables[f"avg_intens_{craft}"] = intensity
        variables[f"background_{craft}"] = background

    return FakeCDF(variables)


def patch_reader(monkeypatch, factory) -> None:
    """Route ``src.Backend.swaves._open_cdf`` to locally built FakeCDFs.

    ``factory`` receives the path being opened and returns a FakeCDF.
    """
    monkeypatch.setattr("src.Backend.swaves._open_cdf", lambda path: factory(str(path)))


class FakeResponse:
    def __init__(self, *, status_code: int = 200, body: bytes = b"", text: str = ""):
        self.status_code = int(status_code)
        self._body = bytes(body)
        self.text = str(text)
        self.headers = {"content-length": str(len(self._body))}

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def iter_content(self, chunk_size=0):
        size = int(chunk_size) or len(self._body) or 1
        for start in range(0, len(self._body), size):
            yield self._body[start : start + size]


class FakeSession:
    """Records requested URLs and replies from a URL -> FakeResponse map."""

    def __init__(self, responses: dict[str, FakeResponse], default: FakeResponse | None = None):
        self.responses = dict(responses)
        self.default = default or FakeResponse(status_code=404)
        self.requested: list[str] = []

    def get(self, url, **_kwargs):
        self.requested.append(str(url))
        return self.responses.get(str(url), self.default)
