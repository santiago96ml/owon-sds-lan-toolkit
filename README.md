# OWON SDS oscilloscope LAN toolkit

Python scripts to pull live data from an OWON SDS-series oscilloscope over
Ethernet, without any vendor software. Validated on **OWON SDS5032E,
firmware V4.3.2** (2026-10-15).

## Requirements

- Python 3.8+ (tested on 3.14), standard library only.
- `matplotlib` optional (only for PNG plots / live viewer window; CSV always works).
- Scope reachable over LAN (see setup below).

## Scope setup (one time)

The SDS-E series has **no DHCP** — the network must be configured manually on
the scope:

1. Connect the scope's LAN port directly to the PC (or via a router/switch).
2. On the scope: **Utility -> H1 -> M knob "LAN Set" -> H2** and set:
   - IP: `192.168.137.72`
   - Port: `3000`
   - Netgate: `192.168.137.1`
   - Net mask: `255.255.255.0`
3. **H3 "Save set"**, then power-cycle the scope (it asks for a reset to apply).
4. On the PC, the Ethernet NIC must be on the same subnet
   (e.g. static `192.168.137.1 / 255.255.255.0`).

Quick check: `ping 192.168.137.72` (note: the scope's factory MAC starts with
the multicast bit — `7F:...` — but Windows resolves it fine).

## Usage

```bash
# one-shot capture of channel 1 -> captures/ch1_<timestamp>.csv + PNG
python capture.py

# both channels (CH2 must be enabled on the scope)
python capture.py --channel both

# CSV only, custom address
python capture.py --ip 192.168.137.72 --no-plot

# live viewer window (refresh every 2 seconds; Ctrl+C to stop)
python live.py
python live.py --channel 2 --interval 1

# headless test: save 5 frames as PNGs
python live.py --headless --frames 5
```

On exit, `live.py` saves the last frame to `captures/live_last.csv`.

## Files

| File | What it does |
|------|--------------|
| `owon_sds.py` | Client library: TCP socket, SCPI handshake, binary waveform dump parser, volts/time conversion |
| `capture.py` | One-shot capture: config + waveform + measurements -> CSV/PNG |
| `live.py` | Live viewer: repeated captures, updating plot window |
| `captures/` | Output folder (CSVs and PNGs) |

## Protocol notes (reverse-engineered)

- Transport: raw TCP on the configured port (3000). Commands need no terminator.
- Session handshake: send `:SDSLSCPI#` -> scope replies `:SCPION`
  (silent if already in SCPI mode — harmless).
- Waveform dump: send `*CLS`, then `STARTMEMDEPTH`; read a 12-byte header
  (`file_length` u32 LE, `flags` u32 LE at offset 8), then the payload.
- Payload: header + per-channel block `;CH<n>` + 14 little-endian words
  (includes `num_points`, `zero_point`, `voltage_per_point_mv`, plus metadata)
  + one signed int8 sample per point.
- Conversion (validated against the scope's own PKPK/AVG measurements):

  ```
  volts = (count - zero_point) * (voltage_per_point_mv / 1000) * probe_factor
  ```

- Time axis: 10 horizontal divisions at the configured timebase;
  `dt_ms = 10 * timebase_seconds / num_points * 1000`.

## Troubleshooting

- **No connection**: check the scope IP/port in its LAN Set menu and that the
  PC NIC is on the same subnet. This scope never answers DHCP — it must be
  configured manually.
- **Random timeouts**: OWON's SCPI implementation is known to be flaky; the
  scripts auto-reconnect when a frame fails.
- **PKPK mismatch warning**: if the scope's own PKPK measurement and the dump
  disagree by more than ~15%, check the probe attenuation setting.
