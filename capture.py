#!/usr/bin/env python3
"""One-shot capture from an OWON SDS oscilloscope over LAN.

Downloads the full memory dump for the selected channel(s), converts to volts,
saves CSV (always) and a PNG plot (if matplotlib is available), and prints a
consistency check against the scope's own PKPK measurement.

Usage:
    python capture.py                          # CH1 -> captures/ch1_<ts>.csv + png
    python capture.py --channel both           # CH1 + CH2 (CH2 must be ON)
    python capture.py --channel 2 --no-plot
"""

import argparse
import csv
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from owon_sds import OwonScope, OwonError  # noqa: E402


def do_capture(scope, channel, outdir, make_plot=True):
    dump = scope.dump_waveform()
    cfg = scope.get_config(channel)
    probe = scope.probe_factor(channel)

    if channel not in dump["channels"]:
        print("CH%d not present in payload (display OFF?). Skipping." % channel)
        return None

    ch_data = dump["channels"][channel]
    volts = scope.to_volts(ch_data, probe)
    times = scope.time_axis_ms(ch_data)

    ts = time.strftime("%Y%m%d_%H%M%S")
    csv_path = outdir / ("ch%d_%s.csv" % (channel, ts))
    with open(csv_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["time_ms", "volts_ch%d" % channel])
        for i, v in enumerate(volts):
            t = times[i] if times else i
            w.writerow(["%.4f" % t, "%.4f" % v])

    vmin, vmax = min(volts), max(volts)
    vmean = sum(volts) / len(volts)
    print("CH%d: %d puntos | Vpp=%.1f mV | media=%.1f mV | min=%.1f max=%.1f mV"
          % (channel, len(volts), (vmax - vmin) * 1000, vmean * 1000,
             vmin * 1000, vmax * 1000))
    print("  config: %s | %s | sonda %s | base %s"
          % (cfg["ch_scale"], cfg["ch_coupling"], cfg["ch_probe"], cfg["timebase"]))

    # Consistency check vs the scope's own measurement
    try:
        meas = scope.get_measurements(channel)
        pkpk = meas["PKPK"]["volts"]
        if pkpk is not None:
            delta = abs((vmax - vmin) - pkpk) * 1000
            flag = "OK" if delta < max(20.0, pkpk * 1000 * 0.15) else "CHECK"
            print("  PKPK scope=%.1f mV vs dump=%.1f mV -> %s"
                  % (pkpk * 1000, (vmax - vmin) * 1000, flag))
    except OwonError as e:
        print("  (measurement check skipped: %s)" % e)

    print("  CSV: %s" % csv_path)
    return {"channel": channel, "times": times, "volts": volts,
            "csv": csv_path, "ts": ts, "probe": probe, "cfg": cfg}


def main():
    ap = argparse.ArgumentParser(description="Captura unica OWON SDS via LAN")
    ap.add_argument("--ip", default="192.168.137.72")
    ap.add_argument("--port", type=int, default=3000)
    ap.add_argument("--channel", default="1", choices=["1", "2", "both"])
    ap.add_argument("--out", default=None, help="carpeta de salida (default: ./captures junto al script)")
    ap.add_argument("--no-plot", action="store_true", help="no generar PNG (solo CSV)")
    args = ap.parse_args()

    outdir = Path(args.out) if args.out else Path(__file__).resolve().parent / "captures"
    outdir.mkdir(parents=True, exist_ok=True)

    channels = [1, 2] if args.channel == "both" else [int(args.channel)]

    scope = OwonScope(args.ip, args.port)
    try:
        scope.connect()
        print("conectado: %s:%d -> %s" % (args.ip, args.port, scope.idn()))

        results = []
        for ch in channels:
            try:
                r = do_capture(scope, ch, outdir, make_plot=not args.no_plot)
            except (OwonError, OSError) as e:
                print("CH%d error: %s -> reconnecting & retry" % (ch, e))
                scope.reconnect()
                r = do_capture(scope, ch, outdir, make_plot=not args.no_plot)
            if r:
                results.append(r)

        if results and not args.no_plot:
            try:
                import matplotlib
                matplotlib.use("Agg")
                import matplotlib.pyplot as plt

                fig, axes = plt.subplots(len(results), 1, figsize=(11, 3.2 * len(results)),
                                         squeeze=False)
                for ax, r in zip(axes[:, 0], results):
                    t = r["times"] if r["times"] else list(range(len(r["volts"])))
                    ax.plot(t, r["volts"], linewidth=0.8, color="tab:red")
                    ax.set_title("OWON SDS - CH%d - %s" % (r["channel"], r["ts"]))
                    ax.set_xlabel("tiempo (ms)")
                    ax.set_ylabel("volts (sonda %s)" % (r["cfg"].get("ch_probe", "?")))
                    ax.grid(True, alpha=0.3)
                fig.tight_layout()
                png = outdir / ("capture_%s.png" % results[0]["ts"])
                fig.savefig(png, dpi=110, bbox_inches="tight")
                print("PNG: %s" % png)
            except ImportError:
                print("matplotlib no disponible: solo CSV generado")
    finally:
        scope.close()


if __name__ == "__main__":
    main()
