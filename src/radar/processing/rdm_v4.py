"""Range-Doppler Map (RDM) processing — v4 (optimised memory path).

What changed vs rdm_v3
----------------------
1. **De-interleave is strided reshape/transpose, not scatter/gather.** The ADC
   stream arrives in hardware order ``[slow, tx, fast, iq, rx]`` (rx innermost).
   v3 permuted the whole 786k-sample flat buffer with two fancy-index *gathers*
   (``self.window[self.fast]``, ``self.window_slow[self.slow]``) plus one
   ``np.put`` *scatter* — random-access permutations that dominate the RDM stage
   (~15-25 ms on the Jetson CPU). v4 views the buffer as ``(S, T, F, IQ, RX)``,
   transposes the innermost pair so I/Q become adjacent, and assembles the
   complex cube with two strided int16→float32 passes into one pre-allocated
   buffer. No index arrays, no scatter.

2. **Doppler fftshift folded into the window.** Multiplying the slow-time axis
   by ``(-1)**slow`` before the FFT is exactly equivalent to
   ``np.fft.fftshift`` on the output for even ``SLOW_TIME`` (64), removing one
   full 3.1 MB copy per frame.

3. **Background IIR is vectorized.** Boolean-mask scatter
   (``bg[mask] = ...``) is replaced by a whole-array update + masked
   ``np.copyto(..., where=mask)`` — contiguous and SIMD-friendly.

4. **FFTW_MEASURE + persisted wisdom** instead of FFTW_ESTIMATE. The plan is
   built once at startup (a few seconds) and the wisdom is cached under the
   system temp dir so later runs plan instantly.

Semantics are identical to rdm_v3: same blackman/hann windows, same
background alpha (0.005, the default of v3's ``background_subtraction``),
same power / dB / display math, same output shapes and the same public
interface (``set_buffer`` / ``process`` / ``get_*``).
"""

import os
import tempfile

import numpy as np
import pyfftw

from .. import config


class RangeDoppler:
    SIZE = config.BYTES_IN_FRAME // config.IQ_BYTES  # total ADC samples

    # Background IIR coefficient — matches rdm_v3.background_subtraction default.
    BACKGROUND_ALPHA = 0.005

    def __init__(self, window="blackman", alpha=0.5):
        self.window_type = window.lower()
        self.alpha = alpha  # kept for interface parity; background uses BACKGROUND_ALPHA

        S, T, F, RX = config.SLOW_TIME, config.TX, config.FAST_TIME, config.RX
        self._S, self._T, self._F, self._RX = S, T, F, RX
        n_chan = T * RX

        # ADC buffer — int16 this time; the complex cube is assembled from it.
        self.adc_data_flat = np.zeros(self.SIZE, dtype=np.int16)

        # Pre-allocated output buffers (reused every frame)
        self.power_map = np.zeros((S, F), dtype=np.float32)
        self.power_db_map = np.zeros((S, F), dtype=np.float32)
        self.display_map = np.zeros((S, F), dtype=np.float32)
        self._db_scratch = np.zeros((S, F), dtype=np.float32)

        # Scratch complex cube in natural layout (S, T, F, RX) — reused.
        self._cube_scratch = np.zeros((S, T, F, RX), dtype=np.complex64)

        # Windows: fast-time (range) window; slow-time (doppler) window with the
        # fftshift pre-multiplier (-1)**slow folded in.
        if self.window_type == "blackman":
            w_fast = np.blackman(F).astype(np.float32)
            w_slow = np.blackman(S).astype(np.float32)
        elif self.window_type == "hann":
            w_fast = np.hanning(F).astype(np.float32)
            w_slow = np.hanning(S).astype(np.float32)
        else:
            w_fast = np.ones(F, dtype=np.float32)
            w_slow = np.ones(S, dtype=np.float32)
        shift = np.where(np.arange(S) % 2 == 0, np.float32(1.0), np.float32(-1.0))
        self.w_fast = w_fast
        self.w_slow_shift = w_slow * shift  # window * (-1)**slow

        # Background stores — cube layout is (TX, RX, SLOW, FAST)
        self.background = np.zeros((T, RX, S, F), dtype=np.complex64)
        self.background_power = np.zeros((T, RX, S, F), dtype=np.float32)

        # FFTW plan over (slow, fast) axes of the (TX, RX, SLOW, FAST) cube
        self._wisdom_file = os.path.join(
            tempfile.gettempdir(),
            f"netradar_fftw_wisdom_{S}x{F}_v4.dat",
        )
        self._load_wisdom()

        self.fftw_in = pyfftw.empty_aligned((T, RX, S, F), dtype=np.complex64)
        self.fftw_out = pyfftw.empty_aligned((T, RX, S, F), dtype=np.complex64)
        self.plan = pyfftw.FFTW(
            self.fftw_in,
            self.fftw_out,
            axes=(2, 3),
            direction="FFTW_FORWARD",
            flags=("FFTW_MEASURE",),
        )
        self._save_wisdom()

    # ------------------------------------------------------------------
    # FFTW wisdom cache (best-effort; never raises)
    # ------------------------------------------------------------------
    def _load_wisdom(self):
        try:
            if os.path.exists(self._wisdom_file):
                with open(self._wisdom_file, "rb") as f:
                    pyfftw.import_wisdom(f.read())
        except Exception:
            pass

    def _save_wisdom(self):
        try:
            with open(self._wisdom_file, "wb") as f:
                f.write(pyfftw.export_wisdom())
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Input
    # ------------------------------------------------------------------
    def set_buffer(self, buf):
        arr = np.asarray(buf, dtype=np.int16)
        if arr.size != self.SIZE:
            raise ValueError(f"Invalid input buffer size: {arr.size} != {self.SIZE}")
        self.adc_data_flat[:] = arr

    # ------------------------------------------------------------------
    # Main processing entry point
    # ------------------------------------------------------------------
    def process(self):
        """Run one frame: shape → FFT → bgnd-sub → power → dB → display."""
        S, T, F, RX = self._S, self._T, self._F, self._RX
        n_chan = T * RX

        # ---- De-interleave: int16 (S, T, F, IQ, RX) → complex64 (S, T, F, RX)
        # Hardware order is [slow][tx][fast][iq][rx]. Transposing the innermost
        # pair makes I/Q adjacent; two strided int16→float32 passes assemble the
        # complex cube into a reused buffer (no scatter, no per-frame alloc).
        x = self.adc_data_flat.reshape(S, T, F, 2, RX)   # view
        x = x.transpose(0, 1, 2, 4, 3)                   # (S, T, F, RX, 2) view
        z = self._cube_scratch
        z.real = x[..., 0]
        z.imag = x[..., 1]

        # ---- Windowing (fast-time range window + shifted doppler window)
        z *= self.w_fast[None, None, :, None]
        z *= self.w_slow_shift[:, None, None, None]

        # ---- 2D FFT over (slow, fast) per (TX, RX) channel
        np.copyto(self.fftw_in, z.transpose(1, 3, 0, 2))
        self.plan()
        rdm = self.fftw_out  # (T, RX, S, F) complex64, doppler-shifted

        # ---- Adaptive background subtraction (vectorized IIR)
        power = rdm.real * rdm.real + rdm.imag * rdm.imag
        diff = power - self.background_power
        threshold = 3.0 * np.abs(diff).mean()
        mask = diff <= threshold
        ma = 1.0 - self.BACKGROUND_ALPHA
        np.copyto(
            self.background,
            ma * self.background + self.BACKGROUND_ALPHA * rdm,
            where=mask,
        )
        np.copyto(
            self.background_power,
            ma * self.background_power + self.BACKGROUND_ALPHA * power,
            where=mask,
        )
        clean = rdm - self.background
        self.clean_rdm_complex = clean

        # ---- Power: channel-average over (T, RX) via einsum (single pass)
        np.einsum("trsf,trsf->sf", clean.real, clean.real, out=self.power_map)
        np.einsum("trsf,trsf->sf", clean.imag, clean.imag, out=self._db_scratch)
        self.power_map += self._db_scratch
        self.power_map /= n_chan

        # ---- dB
        np.log10(self.power_map + 1e-10, out=self._db_scratch)
        np.multiply(10.0, self._db_scratch, out=self.power_db_map)

        # ---- Display normalisation (write into pre-allocated buffer)
        mn = self.power_db_map.min()
        mx = self.power_db_map.max()
        if mx > mn:
            np.subtract(self.power_db_map, mn, out=self.display_map)
            np.multiply(self.display_map, 255.0 / (mx - mn), out=self.display_map)
        else:
            self.display_map.fill(0.0)

        return self.power_map

    # ------------------------------------------------------------------
    # Accessors (same shapes/semantics as rdm_v3)
    # ------------------------------------------------------------------
    def get_clean_rdm(self):
        """Return cleaned complex RDM shaped for angle estimation."""
        if not hasattr(self, "clean_rdm_complex"):
            raise RuntimeError("process() must be called before get_clean_rdm()")
        # (TX, RX, S, F) → (S, RX, TX, F)
        return self.clean_rdm_complex.transpose(2, 1, 0, 3)

    def get_power_map(self):
        return self.power_map

    def get_power_db_map(self):
        return self.power_db_map

    def get_display_map(self):
        return self.display_map
