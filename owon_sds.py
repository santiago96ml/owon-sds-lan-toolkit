"""OWON SDS-series oscilloscope LAN client (raw SCPI socket + binary waveform dump).

Tested against: OWON SDS5032E, firmware V4.3.2 (2026-10-15).
Transport: raw TCP <ip>:<port> (default port 3000), SCPI via ':SDSLSCPI#' handshake.
Commands require NO line terminator. Responses are plain ASCII stripped of whitespace.

Waveform dump protocol (reverse-engineered, validated against the scope's own
measurements):
    1. send '*CLS', then 'STARTMEMDEPTH'
    2. read 12-byte header:  file_length u32 LE [0:4], flags u32 LE [8:12]
    3. read file_length bytes of payload:
         [0:10]   magic 'SPBS0Ae' + 3 bytes
         [10:26]  serial number ASCII
         then per displayed channel a block:
           ';CH<n>' marker (4 bytes)
           14 little-endian words (56 bytes):
             block_len (i32, negative = deep-memory 1-byte samples)
             f1, f2, f3 (metadata, timing info)
             whole_points, num_points (i32)
             timebase_idx (i32, index in TIMEBASE_SCALES)
             zero_point (i32, ADC count at 0 V)
             voltage_idx (i32, index in VOLTAGE_SCALES)
             atten_idx (i32, index in ATTEN_FACTORS = probe attenuation)
             volts_per_div (f32, probe-referred)
             frequency, cycle (f32)
             voltage_per_point_mv (f32, millivolts per count at the BNC, pre-probe)
           then num_points signed int8 samples
       next block starts right after the samples.

Volts conversion (per probe tip):
    volts = (count - zero_point) * (voltage_per_point_mv / 1000) * probe_factor
"""

import socket
import struct
import time

TIMEBASE_SCALES = [
    "5ns", "10ns", "20ns", "50ns", "100ns", "200ns", "500ns",
    "1us", "2us", "5us", "10us", "20us", "50us", "100us", "200us", "500us",
    "1ms", "2ms", "5ms", "10ms", "20ms", "50ms", "100ms", "200ms", "500ms",
    "1s", "2s", "5s", "10s", "20s", "50s", "100s",
]

VOLTAGE_SCALES = [
    "2mv", "5mv", "10mv", "20mv", "50mv", "100mv", "200mv", "500mv",
    "1v", "2v", "5v", "10v",
]

ATTEN_FACTORS = [1, 10, 100, 1000]  # X1, X10, X100, X1000

DIVISIONS_X = 10.0  # horizontal divisions on screen


class OwonError(Exception):
    pass


def parse_scale_to_seconds(text):
    """'1.0ms' -> 0.001 seconds. Returns None if unparsable."""
    s = text.strip().lower()
    for suffix, mult in (("ns", 1e-9), ("us", 1e-6), ("ms", 1e-3), ("s", 1.0)):
        if s.endswith(suffix):
            try:
                return float(s[: -len(suffix)]) * mult
            except ValueError:
                return None
    return None


def parse_volts(text):
    """'400.0mv' -> 0.4 (volts). '?' -> None. Handles uv/mv/v."""
    s = text.strip().lower()
    if not s or s == "?":
        return None
    for suffix, mult in (("uv", 1e-6), ("mv", 1e-3), ("v", 1.0)):
        if s.endswith(suffix):
            try:
                return float(s[: -len(suffix)]) * mult
            except ValueError:
                return None
    return None


def parse_probe(text):
    """'X10' -> 10. Returns None if unparsable."""
    s = text.strip().upper()
    if s.startswith("X"):
        try:
            return int(s[1:])
        except ValueError:
            return None
    return None


class OwonScope:
    def __init__(self, ip="192.168.137.72", port=3000, timeout=4.0):
        self.ip = ip
        self.port = port
        self.timeout = timeout
        self.sock = None

    # ------------------------------------------------------------- transport
    def connect(self):
        """Open TCP and try the SCPI handshake. Silence is tolerated: if the
        scope is already in SCPI mode it will not answer again."""
        self.sock = socket.create_connection((self.ip, self.port), timeout=self.timeout)
        self.sock.settimeout(2.5)
        handshake = b""
        try:
            self.sock.sendall(b":SDSLSCPI#")
            time.sleep(0.35)
            handshake = self._drain(quiet=0.4, timeout=1.5)
        except OSError:
            pass
        return handshake

    def close(self):
        if self.sock:
            try:
                self.sock.close()
            finally:
                self.sock = None

    def reconnect(self):
        self.close()
        return self.connect()

    def _drain(self, quiet=0.25, timeout=2.5):
        """Read until nothing arrives for `quiet` seconds."""
        self.sock.settimeout(timeout)
        chunks = []
        try:
            while True:
                data = self.sock.recv(4096)
                if not data:
                    break
                chunks.append(data)
                self.sock.settimeout(quiet)
        except socket.timeout:
            pass
        return b"".join(chunks)

    def _read_exact(self, n, timeout):
        self.sock.settimeout(timeout)
        data = b""
        while len(data) < n:
            try:
                chunk = self.sock.recv(min(8192, n - len(data)))
            except socket.timeout:
                break
            if not chunk:
                break
            data += chunk
        if len(data) != n:
            raise OwonError("short read: got %d of %d bytes" % (len(data), n))
        return data

    # ---------------------------------------------------------------- scpi
    def query(self, cmd, wait=0.35):
        """Send a SCPI command and return its response bytes."""
        if self.sock is None:
            raise OwonError("not connected")
        self.sock.sendall(cmd.encode("ascii"))
        time.sleep(wait)
        return self._drain()

    def query_text(self, cmd):
        return self.query(cmd).decode("ascii", "replace").strip()

    def idn(self):
        return self.query_text("*IDN?")

    def get_config(self, channel=1):
        """Read live settings as a dict of text responses."""
        c = int(channel)
        cmds = {
            "timebase": ":TIMebase:SCALe?",
            "hoffset": ":TIMebase:HOFFset?",
            "trigger_mode": ":TRIGger:MODE?",
            "memory_depth": ":ACQuire:MDEPth?",
            "ch_display": ":CHANnel%d:DISPlay?" % c,
            "ch_scale": ":CHANnel%d:SCALe?" % c,
            "ch_offset": ":CHANnel%d:OFFSet?" % c,
            "ch_coupling": ":CHANnel%d:COUPling?" % c,
            "ch_probe": ":CHANnel%d:PROBe?" % c,
        }
        return {k: self.query_text(v) for k, v in cmds.items()}

    def get_measurements(self, channel=1):
        """Read automatic measurements for a channel (sets measure source)."""
        c = int(channel)
        current = self.query_text(":MEASure:SOURce?")
        if current != "CH%d" % c:
            self.query(":MEASure:SOURce CH%d" % c)
        cmds = {
            "PKPK": ":MEASure:PKPK?",
            "FREQuency": ":MEASure:FREQuency?",
            "PERiod": ":MEASure:PERiod?",
            "CYCRms": ":MEASure:CYCRms?",
            "AVERage": ":MEASure:AVERage?",
            "VTOP": ":MEASure:VTOP?",
            "VBASe": ":MEASure:VBASe?",
        }
        out = {}
        for k, v in cmds.items():
            raw = self.query_text(v)
            out[k] = {"raw": raw, "volts": parse_volts(raw)}
        return out

    def probe_factor(self, channel=1):
        """Probe attenuation factor from the scope (fallback: None)."""
        raw = self.query_text(":CHANnel%d:PROBe?" % channel)
        return parse_probe(raw)

    # ------------------------------------------------------------- waveform
    def dump_waveform(self):
        """Download the full memory dump. Returns dict with 'flags' and
        'channels': {n: {...meta..., 'counts': [int...]}}."""
        if self.sock is None:
            raise OwonError("not connected")

        self.sock.sendall(b"*CLS")
        time.sleep(0.2)
        try:
            self._drain(quiet=0.3, timeout=0.8)
        except OSError:
            pass

        self.sock.sendall(b"STARTMEMDEPTH")
        header = self._read_exact(12, timeout=6.0)
        file_length = struct.unpack("<I", header[0:4])[0]
        flags = struct.unpack("<I", header[8:12])[0]
        if file_length == 0 or file_length > 50_000_000:
            raise OwonError("unreasonable payload length: %d" % file_length)

        payload = self._read_exact(file_length, timeout=25.0)
        return self.parse_payload(payload, flags)

    @staticmethod
    def parse_payload(payload, flags=0):
        channels = {}
        pos = 0
        while True:
            marker = payload.find(b";CH", pos)
            if marker == -1 or marker + 4 > len(payload):
                break
            tag = payload[marker:marker + 4]
            try:
                ch = int(tag[3:4])
            except ValueError:
                pos = marker + 4
                continue

            base = marker + 4
            words = payload[base:base + 56]
            if len(words) < 56:
                break
            (block_len, f1, f2, f3, whole_points, num_points, timebase_idx,
             zero_point, voltage_idx, atten_idx) = struct.unpack("<10i", words[0:40])
            volts_per_div, frequency, cycle, vpp_mv = struct.unpack("<4f", words[40:56])

            data_start = base + 56
            raw = payload[data_start:data_start + abs(num_points)]
            counts = [b - 256 if b > 127 else b for b in raw]

            channels[ch] = {
                "block_len": block_len,
                "meta": (f1, f2, f3),
                "whole_points": whole_points,
                "num_points": num_points,
                "timebase_idx": timebase_idx,
                "zero_point": zero_point,
                "voltage_idx": voltage_idx,
                "atten_idx": atten_idx,
                "volts_per_div": volts_per_div,
                "frequency": frequency,
                "cycle": cycle,
                "voltage_per_point_mv": vpp_mv,
                "counts": counts,
            }
            pos = data_start + abs(num_points)

        if not channels:
            raise OwonError("no channel blocks found in payload")
        return {"flags": flags, "channels": channels}

    @staticmethod
    def to_volts(channel_data, probe_factor=None):
        """Convert counts to volts at the probe tip."""
        factor = probe_factor
        if factor is None:
            factor = ATTEN_FACTORS[channel_data["atten_idx"]] \
                if 0 <= channel_data["atten_idx"] < len(ATTEN_FACTORS) else 1
        vpc = channel_data["voltage_per_point_mv"] / 1000.0 * factor
        zero = channel_data["zero_point"]
        return [(c - zero) * vpc for c in channel_data["counts"]]

    @staticmethod
    def time_axis_ms(channel_data):
        """Relative time axis in milliseconds (assumes 10 horizontal divisions)."""
        idx = channel_data["timebase_idx"]
        if 0 <= idx < len(TIMEBASE_SCALES):
            scale_s = parse_scale_to_seconds(TIMEBASE_SCALES[idx])
        else:
            scale_s = None
        if not scale_s:
            return None
        n = max(1, len(channel_data["counts"]))
        dt_ms = (DIVISIONS_X * scale_s) / n * 1000.0
        return [i * dt_ms for i in range(n)]
