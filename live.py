#!/usr/bin/env python3
"""Live viewer for an OWON SDS oscilloscope over LAN.

Repeatedly downloads the memory dump and updates a plot window in real time
(one frame every --interval seconds). Ctrl+C or closing the window stops it.

Usage:
    python live.py                          # ventana con CH1, refresco cada 2s
    python live.py --channel 2 --interval 1
    python live.py --headless --frames 5    # sin ventana: guarda 5 PNGs (para probar)

On exit it saves the last frame as captures/live_last.csv
"""

import argparse
import csv
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from owon_sds import OwonScope, OwonError  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description="Visor en vivo OWON SDS via LAN")
    ap.add_argument("--ip", default="192.168.137.72")
    ap.add_argument("--port", type=int, default=3000)
    ap.add_argument("--channel", type=int, default=1, choices=[1, 2])
    ap.add_argument("--interval", type=float, default=2.0, help="segundos entre capturas")
    ap.add_argument("--frames", type=int, default=0, help="0 = infinito")
    ap.add_argument("--headless", action="store_true", help="sin ventana: guarda PNGs")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    outdir = Path(args.out) if args.out else Path(__file__).resolve().parent / "captures"
    outdir.mkdir(parents=True, exist_ok=True)

    import matplotlib
    if args.headless:
        matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    scope = OwonScope(args.ip, args.port)
    scope.connect()
    print("conectado: %s:%d -> %s" % (args.ip, args.port, scope.idn()))

    fig, ax = plt.subplots(figsize=(11, 4))
    line, = ax.plot([], [], linewidth=0.8, color="tab:red")
    ax.set_xlabel("tiempo (ms)")
    ax.set_ylabel("volts (CH%d)" % args.channel)
    ax.grid(True, alpha=0.3)
    if not args.headless:
        plt.ion()
        fig.show()

    last = None
    frame = 0
    try:
        while args.frames == 0 or frame < args.frames:
            t0 = time.time()
            frame += 1
            try:
                dump = scope.dump_waveform()
                ch_data = dump["channels"].get(args.channel)
                if ch_data is None:
                    print("frame %d: CH%d ausente (display OFF?)" % (frame, args.channel))
                else:
                    probe = scope.probe_factor(args.channel)
                    volts = scope.to_volts(ch_data, probe)
                    times = scope.time_axis_ms(ch_data) or list(range(len(volts)))
                    vpp = (max(volts) - min(volts)) * 1000
                    vmean = sum(volts) / len(volts) * 1000

                    line.set_data(times, volts)
                    ax.set_xlim(times[0], times[-1])
                    span = max(volts) - min(volts)
                    margin = span * 0.15 if span > 0 else 0.1
                    ax.set_ylim(min(volts) - margin, max(volts) + margin)
                    ax.set_title("OWON SDS - CH%d - Vpp=%.1f mV  media=%.1f mV - %s"
                                 % (args.channel, vpp, vmean,
                                    time.strftime("%H:%M:%S")))
                    last = (times, volts)

                    if args.headless:
                        png = outdir / ("live_frame_%04d.png" % frame)
                        fig.savefig(png, dpi=100, bbox_inches="tight")
                        print("frame %d: Vpp=%.1f mV -> %s" % (frame, vpp, png))
                    else:
                        fig.canvas.draw_idle()
                        plt.pause(0.01)
                        if not plt.fignum_exists(fig.number):
                            break
            except (OwonError, OSError) as e:
                print("frame %d error: %s -> reconnecting" % (frame, e))
                try:
                    scope.reconnect()
                except OSError as e2:
                    print("reconnect failed: %s" % e2)
                    time.sleep(2.0)

            dt = args.interval - (time.time() - t0)
            if dt > 0:
                time.sleep(dt)
    except KeyboardInterrupt:
        pass
    finally:
        if last is not None:
            csv_path = outdir / "live_last.csv"
            with open(csv_path, "w", newline="") as f:
                w = csv.writer(f)
                w.writerow(["time_ms", "volts_ch%d" % args.channel])
                for t, v in zip(last[0], last[1]):
                    w.writerow(["%.4f" % t, "%.4f" % v])
            print("ultimo frame guardado en: %s" % csv_path)
        scope.close()
        print("listo.")


if __name__ == "__main__":
    main()
