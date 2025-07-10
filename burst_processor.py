import numpy as np
from astropy.io import fits

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

