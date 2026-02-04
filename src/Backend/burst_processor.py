"""
e-CALLISTO FITS Analyzer
Version 1.7.7 - Development Version
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
    # Support common CALLISTO variants, e.g.:
    #   STATION_YYYYMMDD_HHMMSS_ID.fit(.gz)
    #   STATION_YYYYMMDD_HHMMSS_HHMMSS_ID.fit(.gz)
    stem = base
    for ext in (".fit.gz", ".fits.gz", ".fit", ".fits"):
        if stem.lower().endswith(ext):
            stem = stem[: -len(ext)]
            break

    parts = stem.split("_")
    if len(parts) < 4:
        raise ValueError(f"Invalid CALLISTO filename format: {base}")

    station = parts[0]
    date = parts[1]
    time = parts[2]
    receiver_id = parts[-1]

    return station, date, time, receiver_id


def are_frequency_combinable(file_paths):
    if len(file_paths) < 2:
        return False

    try:
        s_ref, d_ref, t_ref, _ = parse_filename(file_paths[0])
    except Exception:
        return False

    receiver_ids = set()
    time_ref = None

    for fp in file_paths:
        try:
            s, d, t, rec = parse_filename(fp)
        except Exception:
            return False

        if s != s_ref or d != d_ref or t != t_ref:
            return False

        # Require different receiver IDs (adjacent frequency blocks)
        if rec in receiver_ids:
            return False
        receiver_ids.add(rec)

        _, _, time_arr = load_fits(fp)
        if time_ref is None:
            time_ref = time_arr
        elif not np.allclose(time_arr, time_ref, atol=0.01):
            return False

    return True


def combine_frequency(file_paths):
    if len(file_paths) < 2:
        raise ValueError("Need at least 2 files to combine frequencies.")

    data_list = []
    freq_list = []
    time_ref = None

    for fp in file_paths:
        data, freqs, time_arr = load_fits(fp)
        data_list.append(data)
        freq_list.append(freqs)

        if time_ref is None:
            time_ref = time_arr
        elif not np.allclose(time_arr, time_ref, atol=0.01):
            raise ValueError("Time arrays do not match; cannot frequency-combine.")

    combined_data = np.vstack(data_list)
    combined_freqs = np.concatenate(freq_list)

    station, date, tstamp, _ = parse_filename(file_paths[0])
    combined_name = f"{station}_{date}_{tstamp}_freq_combined"

    try:
        hdr = fits.getheader(file_paths[0], 0)
        hh, mm, ss = hdr["TIME-OBS"].split(":")
        ut_start_sec = int(hh) * 3600 + int(mm) * 60 + float(ss)
    except Exception:
        ut_start_sec = None

    return {
        "data": combined_data,
        "freqs": combined_freqs,
        "time": time_ref,
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
        hdr = fits.getheader(sorted_paths[0], 0)
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
