"""
e-CALLISTO FITS Analyzer
Version 1.7.1
Sahan S Liyanage (sahanslst@gmail.com)
Astronomical and Space Science Unit, University of Colombo, Sri Lanka.
"""

import numpy as np
from astropy.io import fits
import os
from datetime import datetime

def load_fits(filepath):
    hdul = fits.open(filepath)
    data = hdul[0].data
    freqs = hdul[1].data['frequency'][0]
    time = hdul[1].data['time'][0]
    hdul.close()
    return data, freqs, time

def reduce_noise(data, clip_low=-5, clip_high=20):
    data = data - data.mean(axis=1, keepdims=True)
    print("Before clip:", data.min(), data.max())
    data = np.clip(data, clip_low, clip_high)
    data = data * 2500.0 / 255.0 / 25.4
    return data

def parse_filename(filepath):
    base = os.path.basename(filepath)
    parts = base.split("_")
    if len(parts) < 4:
        raise ValueError(f"Invalid CALLISTO filename format: {base}")

    station = parts[0]
    date = parts[1]
    time = parts[2]
    focus = parts[3].split(".")[0]

    return station, date, time, focus


def are_frequency_combinable(file_paths):
    if len(file_paths) != 2:
        return False

    f1, f2 = file_paths

    s1, d1, t1, foc1 = parse_filename(f1)
    s2, d2, t2, foc2 = parse_filename(f2)

    if s1 != s2:
        return False
    if d1 != d2:
        return False
    if t1 != t2:
        return False
    if foc1 == foc2:
        return False

    _, _, time1 = load_fits(f1)
    _, _, time2 = load_fits(f2)

    if not np.allclose(time1, time2, atol=0.01):
        return False

    return True


def combine_frequency(file_paths):
    f1, f2 = file_paths

    data1, freqs1, time1 = load_fits(f1)
    data2, freqs2, time2 = load_fits(f2)

    combined_data = np.vstack([data1, data2])
    combined_freqs = np.concatenate([freqs1, freqs2])

    station, date, tstamp, _ = parse_filename(f1)
    combined_name = f"{station}_{date}_{tstamp}_freq_combined"

    try:
        hdr = fits.open(f1)[0].header
        hh, mm, ss = hdr["TIME-OBS"].split(":")
        ut_start_sec = int(hh) * 3600 + int(mm) * 60 + float(ss)
    except Exception:
        ut_start_sec = None

    return {
        "data": combined_data,
        "freqs": combined_freqs,
        "time": time1,
        "filename": combined_name,
        "ut_start_sec": ut_start_sec,
    }



def are_time_combinable(file_paths):
    if len(file_paths) < 2:
        return False

    try:
        sorted_paths = sorted(
            file_paths,
            key=lambda p: parse_filename(p)[2]  # time
        )
    except Exception:
        return False

    s_ref, d_ref, t_ref, foc_ref = parse_filename(sorted_paths[0])
    _, freqs_ref, _ = load_fits(sorted_paths[0])
    t_prev = datetime.strptime(t_ref, "%H%M%S")

    for fp in sorted_paths[1:]:
        s, d, t, foc = parse_filename(fp)

        if s != s_ref:
            return False
        if d != d_ref:
            return False
        if foc != foc_ref:
            return False

        _, freqs, _ = load_fits(fp)
        if not np.allclose(freqs, freqs_ref, atol=0.01):
            return False

        t_now = datetime.strptime(t, "%H%M%S")
        diff = abs((t_now - t_prev).total_seconds())

        if not (750 <= diff <= 1050):
            return False

        t_prev = t_now

    return True


def combine_time(file_paths):
    sorted_paths = sorted(
        file_paths,
        key=lambda p: parse_filename(p)[2]
    )

    combined_data = None
    combined_time = None
    reference_freqs = None

    for idx, f in enumerate(sorted_paths):
        data, freqs, time = load_fits(f)

        if reference_freqs is None:
            reference_freqs = freqs
            combined_data = data
            combined_time = time
            dt = time[1] - time[0]
        else:

            dt = time[1] - time[0]
            shift = combined_time[-1] + dt
            adjusted_time = time + shift

            combined_data = np.concatenate((combined_data, data), axis=1)
            combined_time = np.concatenate((combined_time, adjusted_time))

    s, d, t, foc = parse_filename(sorted_paths[0])
    combined_name = f"{s}_{d}_combined_time"

    try:
        hdr = fits.open(sorted_paths[0])[0].header
        hh, mm, ss = hdr["TIME-OBS"].split(":")
        ut_start_sec = int(hh) * 3600 + int(mm) * 60 + float(ss)
    except Exception:
        ut_start_sec = None

    return {
        "data": combined_data,
        "freqs": reference_freqs,
        "time": combined_time,
        "filename": combined_name,
        "ut_start_sec": ut_start_sec,
    }
