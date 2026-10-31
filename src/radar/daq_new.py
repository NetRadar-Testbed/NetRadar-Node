import gc
import socket
import time
import numpy as np
from . import config

class DataAcquisition:
    def __init__(self, debug_level=1):
        """
        Initialize DataAcquisition

        Args:
            debug_level: 0=silent, 1=errors/warnings only, 2=info, 3=verbose debug
        """
        self.debug_level = debug_level
        self.frame = 0
        self.packets_read = 0

        # Constants from config
        self.BYTES_IN_PACKET = config.BYTES_IN_PACKET
        self.BYTES_IN_FRAME = config.BYTES_IN_FRAME
        self.UINT16_IN_FRAME = self.BYTES_IN_FRAME // 2

        # Reusable frame storage
        # Raw byte buffer is required for correct split-packet handling
        self.frame_bytes = bytearray(self.BYTES_IN_FRAME)

        # Reusable receive buffer
        self.buffer = bytearray(config.BUFFER_SIZE)

        self.sockfd = None
        self.cliaddr = None
        self.first_frame_captured = False

        # Frame assembly state
        self._frame_start_byte = None      # Absolute byte offset of current frame start
        self._frame_highwater = 0          # Highest byte written in current frame
        self._frame_start_ms = None        # Arrival time of that frame's first byte

        # Carry-over state for split packets
        self._pending_payload = None       # memoryview of leftover payload bytes
        self._pending_byte_count = 0       # absolute stream byte offset of pending payload start
        self._pending_payload_ms = None    # arrival time of the packet holding those bytes

        # Lightweight packet diagnostics
        self._expected_packet_num = None
        self.lost_packets = 0

        # Per-frame capture timing (filled by capture(); read by daq_process):
        #   recv  = time blocked in recvfrom_into (I/O wait)
        #   asm   = packet-assembly compute
        #   copy  = final int16 frame copy
        #   total = whole capture() call
        self.last_timing = {"recv": 0.0, "asm": 0.0, "copy": 0.0, "total": 0.0}

        # When the capture of the frame returned by the most recent capture()
        # call started: epoch milliseconds at which that frame's first byte
        # arrived from the DCA1000 (see capture()). None until a frame is
        # returned; read by daq_process right after the call.
        self.last_frame_capture_ms = None

    def __del__(self):
        self.close_socket()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close_socket()
        return False

    def create_bind_socket(self):
        if self.sockfd is None:
            try:
                print("trying to make socket")
                self.sockfd = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                self.sockfd.setsockopt(
                    socket.SOL_SOCKET, socket.SO_RCVBUF, 16 * 1024 * 1024
                )
                self.sockfd.settimeout(0.5)
                self.sockfd.bind(("", config.PORT))
                print("socket made")
            except socket.error as e:
                print(f"Socket creation failed: {e}")
                self.sockfd = None

    def close_socket(self):
        if self.sockfd is not None:
            self.sockfd.close()
            self.sockfd = None

    def _reset_partial_state(self):
        """Reset partial frame/payload state after timeout or receive error."""
        self._frame_start_byte = None
        self._frame_highwater = 0
        self._frame_start_ms = None
        self._pending_payload = None
        self._pending_byte_count = 0
        self._pending_payload_ms = None

    def capture(self, timeout=None):
        """
        High-performance frame capture with correct split-packet handling.

        This version:
        - uses true BYTES_IN_FRAME instead of BYTES_IN_FRAME_CLIPPED
        - handles frames that end in the middle of a UDP packet
        - carries leftover bytes from the same packet into the next frame
        - keeps recvfrom_into + reusable buffers for speed

        Args:
            timeout (float, optional): Seconds to wait for a frame before raising TimeoutError.
                                       If None, wait indefinitely.

        Returns:
            np.ndarray: 1D int16 frame array of length BYTES_IN_FRAME // 2

        Side effect:
            ``last_frame_capture_ms`` is set to the moment the returned frame's
            first byte arrived, i.e. when its capture started. If the frame's head
            bytes were never seen (packet loss, or the first frame attaching
            mid-stream) it falls back to the arrival of the first byte seen.
        """
        if self.sockfd is None:
            self.create_bind_socket()

        # Hot constants
        F = self.BYTES_IN_FRAME
        BUF_SIZE = config.BUFFER_SIZE

        # Hot refs
        s = self.sockfd
        b = self.buffer
        fb = self.frame_bytes

        frame_start = self._frame_start_byte
        highwater = self._frame_highwater
        frame_start_ms = self._frame_start_ms
        pending_payload = self._pending_payload
        pending_byte_count = self._pending_byte_count
        pending_payload_ms = self._pending_payload_ms
        expected_packet_num = self._expected_packet_num
        lost_packets = self.lost_packets

        # Disable GC in hot loop
        gc_was_enabled = gc.isenabled()
        gc.disable()

        start_time = time.time()
        t_loop = time.perf_counter()
        recv_t = 0.0
        asm_t = 0.0
        copy_t = 0.0
        packet_ms = None                   # arrival time of the packet being consumed

        try:
            while True:
                # Overall timeout check
                if timeout is not None and (time.time() - start_time) > timeout:
                    raise TimeoutError(f"Timed out waiting for frame data after {timeout}s")

                # ------------------------------------------------------------
                # Either consume pending tail bytes from a previous split packet,
                # or receive a fresh UDP packet into the reusable buffer.
                # ------------------------------------------------------------
                if pending_payload is not None:
                    payload = pending_payload
                    byte_count = pending_byte_count
                    packet_ms = pending_payload_ms
                    pending_payload = None
                    pending_payload_ms = None
                else:
                    try:
                        _t0 = time.perf_counter()
                        recv_len, _ = s.recvfrom_into(b, BUF_SIZE)
                        recv_t += time.perf_counter() - _t0
                        packet_ms = time.time_ns() // 1_000_000
                    except socket.timeout:
                        # Drop partial frame on timeout
                        self._reset_partial_state()
                        frame_start = None
                        highwater = 0
                        frame_start_ms = None
                        pending_payload = None
                        pending_byte_count = 0
                        pending_payload_ms = None
                        continue
                    except Exception:
                        # Drop partial frame on any receive error
                        self._reset_partial_state()
                        frame_start = None
                        highwater = 0
                        frame_start_ms = None
                        pending_payload = None
                        pending_byte_count = 0
                        pending_payload_ms = None
                        continue

                    # Must at least contain 10-byte DCA header
                    if recv_len <= 10:
                        continue

                    # Packet number: 4 bytes little-endian
                    packet_num = (
                        b[0]
                        | (b[1] << 8)
                        | (b[2] << 16)
                        | (b[3] << 24)
                    )

                    # Byte count: 6 bytes little-endian
                    byte_count = (
                        b[4]
                        | (b[5] << 8)
                        | (b[6] << 16)
                        | (b[7] << 24)
                        | (b[8] << 32)
                        | (b[9] << 40)
                    )

                    # Lightweight packet-loss tracking
                    if expected_packet_num is not None and packet_num != expected_packet_num:
                        if packet_num > expected_packet_num:
                            lost_packets += (packet_num - expected_packet_num)
                    expected_packet_num = packet_num + 1

                    # Zero-copy payload view over reusable receive buffer
                    payload = memoryview(b)[10:recv_len]

                payload_len = len(payload)
                payload_pos = 0
                stream_pos = byte_count

                # Initialize frame window from the stream position
                if frame_start is None:
                    frame_start = (stream_pos // F) * F
                    highwater = 0
                    frame_start_ms = None

                # ------------------------------------------------------------
                # Consume packet payload into current frame, and if needed,
                # split across frame boundary correctly.
                # ------------------------------------------------------------
                while payload_pos < payload_len:
                    _t0 = time.perf_counter()
                    frame_offset = stream_pos - frame_start

                    # Skip stale bytes if stream position is before current frame
                    if frame_offset < 0:
                        skip = min(payload_len - payload_pos, -frame_offset)
                        payload_pos += skip
                        stream_pos += skip
                        continue

                    # Advance frame window if stream position is already beyond it
                    while frame_offset >= F:
                        frame_start += F
                        highwater = 0
                        frame_offset = stream_pos - frame_start
                        frame_start_ms = None

                    # Copy the largest payload slice that fits in current frame
                    ncopy = min(payload_len - payload_pos, F - frame_offset)
                    end_offset = frame_offset + ncopy

                    fb[frame_offset:end_offset] = payload[payload_pos:payload_pos + ncopy]

                    if end_offset > highwater:
                        highwater = end_offset

                    if frame_offset == 0 and packet_ms is not None:
                        # This packet carried the first byte of the frame, so the
                        # capture of this frame started when the packet arrived.
                        frame_start_ms = packet_ms

                    payload_pos += ncopy
                    stream_pos += ncopy
                    asm_t += time.perf_counter() - _t0

                    # --------------------------------------------------------
                    # Full frame completed
                    # --------------------------------------------------------
                    if highwater == F:
                        # Preserve leftover bytes from SAME packet for next capture() call
                        if payload_pos < payload_len:
                            pending_payload = payload[payload_pos:]
                            pending_byte_count = stream_pos
                            pending_payload_ms = packet_ms
                        else:
                            pending_payload = None
                            pending_byte_count = 0
                            pending_payload_ms = None

                        # Capture start of the frame just completed: when its first
                        # byte arrived, or the packet that completed it if that
                        # first byte was never seen.
                        capture_ms = (
                            frame_start_ms if frame_start_ms is not None else packet_ms
                        )

                        # Move state to next frame
                        frame_start += F
                        highwater = 0
                        frame_start_ms = None

                        # Save persistent state before returning
                        self._frame_start_byte = frame_start
                        self._frame_highwater = highwater
                        self._frame_start_ms = frame_start_ms
                        self._pending_payload = pending_payload
                        self._pending_byte_count = pending_byte_count
                        self._pending_payload_ms = pending_payload_ms
                        self._expected_packet_num = expected_packet_num
                        self.lost_packets = lost_packets

                        # First-frame policy:
                        # keep behavior similar to old code by allowing the first
                        # returned frame only if it began exactly on a real frame boundary,
                        # otherwise discard one initial possibly partial frame.
                        if self.first_frame_captured or (byte_count % F) == 0:
                            self.first_frame_captured = True
                            _t0 = time.perf_counter()
                            out = np.frombuffer(fb, dtype=np.int16).copy()
                            copy_t += time.perf_counter() - _t0
                            self.last_timing = {
                                "recv": recv_t,
                                "asm": asm_t,
                                "copy": copy_t,
                                "total": time.perf_counter() - t_loop,
                            }
                            # Stamp only frames actually handed to the caller.
                            self.last_frame_capture_ms = capture_ms
                            return out

                        # Drop the first possibly partial frame once, then continue
                        self.first_frame_captured = True
                        pending_payload = None
                        pending_byte_count = 0
                        pending_payload_ms = None
                        self._pending_payload = None
                        self._pending_byte_count = 0
                        self._pending_payload_ms = None

        finally:
            if gc_was_enabled:
                gc.enable()
