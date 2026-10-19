#!/usr/bin/env python3
"""
run_trials.py -- Orquestador del experimento (ejecutar con sudo en la VM, con ONOS y ODL ya levantados).

  sudo python3 testbed/run_trials.py --plan plans/abilene_d2_bgpls --mechanism bgpls \
       --scenarios E1,E3,E4,E5,E6 --trials 30 --seed 42 --wan-delay 10 --out results/run_001

Por cada (escenario, repeticion):
   settle -> t0 = time.time_ns() justo antes de aplicar el evento -> esperar vista esperada -> restaurar.
Salida: <out>/trials.jsonl (una fila por fase de evento), <out>/probe_<ctrl>.jsonl, <out>/bgp.pcap, <out>/env.json
"""
import argparse
import json
import os
import platform
import random
import subprocess
import time

from probe_views import Probe
from trial_logic import choose_link, intra_edges
import run_net

SETTLE_STABLE_S = 10      # la vista debe permanecer igual a la esperada este tiempo antes de iniciar un trial
EVENT_TIMEOUT_S = 90      # tiempo maximo para converger tras un evento
HOLD_S = 9                # = 'timers 3 9' en bgpd.conf


def wait_views(probes, target, stable_s, timeout_s):
    """Espera a que TODAS las sondas muestren 'target' de forma estable. Devuelve (ok, t_ns_fin)."""
    t_start, t_ok = time.time(), None
    while time.time() - t_start < timeout_s:
        if all(p.latest == target for p in probes.values()):
            t_ok = t_ok or time.time()
            if time.time() - t_ok >= stable_s:
                return True, time.time_ns()
        else:
            t_ok = None
        time.sleep(0.05)
    return False, time.time_ns()


def default_ctl(ctrl, action):
    """Comando por defecto para detener/arrancar un controlador. ODL: Karaf en $ODL_HOME (bin/stop, bin/start)."""
    if ctrl == "odl" and os.environ.get("ODL_HOME"):
        return f"{os.environ['ODL_HOME']}/bin/{action}"
    return f"docker {action} {ctrl}"


def sh(cmd):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True)


def log_row(fh, **row):
    fh.write(json.dumps(row, default=sorted) + "\n")
    fh.flush()


def run(a):
    rng = random.Random(a.seed)
    os.makedirs(a.out, exist_ok=True)
    net, plan, routers = run_net.build(a.plan, wan_delay_ms=a.wan_delay, ctrl_delay_ms=a.ctrl_delay)
    iface = run_net.start(net, plan, a.plan, routers)
    json.dump({"python": platform.python_version(), "kernel": platform.release(), "args": vars(a),
               "frr": sh(f"{run_net.FRR_BIN}/zebra --version | head -1").stdout.strip(),
               "mininet": sh("mn --version").stdout.strip(), "ovs": sh("ovs-vsctl --version | head -1").stdout.strip(),
               "n_routers": plan["n_routers"], "n_domains": plan["n_domains"]},
              open(os.path.join(a.out, "env.json"), "w"), indent=1)
    cap = subprocess.Popen(["tcpdump", "-i", iface, "-nn", "-s", "0", "--time-stamp-precision=nano",
                            "-w", os.path.join(a.out, "bgp.pcap"), "tcp port 179 or tcp port 1790"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    probes = {c: Probe(c, plan["idmap"], os.path.join(a.out, f"probe_{c}.jsonl"), a.probe_ms, a.mechanism, a.poll_period)
              for c in plan["ctrls"]}
    for p in probes.values():
        p.start()
    full = frozenset(intra_edges(plan))
    trials = open(os.path.join(a.out, "trials.jsonl"), "a")
    try:
        ok, _ = wait_views(probes, full, SETTLE_STABLE_S, 300)
        if not ok:
            raise SystemExit("las vistas no alcanzaron la topologia completa: revisa sesiones BGP-LS (guia, Paso 9)")
        for scen in a.scenarios.split(","):
            for k in range(a.trials):
                wait_views(probes, full, SETTLE_STABLE_S, 120)
                base = dict(trial=k, scenario=scen, mechanism=a.mechanism, wan_delay_ms=a.wan_delay,
                            n_domains=plan["n_domains"], n_routers=plan["n_routers"], poll_period_s=a.poll_period)
                if scen == "E0":               # estado estable: tasa basal de mensajes BGP-LS (sin eventos)
                    t0 = time.time_ns()
                    time.sleep(a.steady_s)
                    log_row(trials, **base, phase="steady", t0_ns=t0, t_end_ns=time.time_ns(), converged=True,
                            expected=sorted(sorted(e) for e in full))
                elif scen in ("E1", "E3"):       # E1: enlace intra-dominio; E3: enlace adyacente al borde
                    link = choose_link(plan, rng, border_adjacent=(scen == "E3"))
                    for phase, status in (("down", "down"), ("up", "up")):
                        target = frozenset(intra_edges(plan, [link] if phase == "down" else []))
                        t0 = time.time_ns()
                        net.configLinkStatus(link[0], link[1], status)
                        ok, t1 = wait_views(probes, target, 2, EVENT_TIMEOUT_S)
                        log_row(trials, **base, phase=phase, link=list(link), t0_ns=t0, t_end_ns=t1, converged=ok,
                                expected=sorted(sorted(e) for e in target))
                elif scen == "E5":             # caida del canal de control de un speaker (se detecta por hold-timer)
                    dom = rng.randrange(plan["n_domains"])
                    sp = next(r for r, x in plan["routers"].items() if x["domain"] == dom and x["speaker"])
                    for phase, status in (("down", "down"), ("up", "up")):
                        target = frozenset(intra_edges(plan, removed_domains=[dom] if phase == "down" else []))
                        t0 = time.time_ns()
                        net.configLinkStatus(sp, "s0", status)
                        ok, t1 = wait_views(probes, target, 2, EVENT_TIMEOUT_S)
                        log_row(trials, **base, phase=phase, speaker=sp, domain=dom, t0_ns=t0, t_end_ns=t1,
                                converged=ok, expected=sorted(sorted(e) for e in target))
                elif scen == "E6":             # estres: 'flaps' consecutivos del mismo enlace
                    link = choose_link(plan, rng)
                    t0 = time.time_ns()
                    for _ in range(a.flaps):
                        net.configLinkStatus(link[0], link[1], "down"); time.sleep(a.flap_period)
                        net.configLinkStatus(link[0], link[1], "up"); time.sleep(a.flap_period)
                    ok, t1 = wait_views(probes, full, 2, EVENT_TIMEOUT_S)
                    log_row(trials, **base, phase="flap", link=list(link), flaps=a.flaps, flap_period_s=a.flap_period,
                            t0_ns=t0, t_end_ns=t1, converged=ok, expected=sorted(sorted(e) for e in full))
                elif scen == "E4":             # caida y reinicio de un controlador (disponibilidad)
                    ctrl = rng.choice(plan["ctrls"])
                    sh(os.environ.get(f"{ctrl.upper()}_STOP", default_ctl(ctrl, "stop")))
                    time.sleep(HOLD_S + 2)
                    t0 = time.time_ns()
                    sh(os.environ.get(f"{ctrl.upper()}_START", default_ctl(ctrl, "start")))
                    ok, t1 = wait_views(probes, full, 2, 300)
                    log_row(trials, **base, phase="restart", controller=ctrl, t0_ns=t0, t_end_ns=t1, converged=ok,
                            expected=sorted(sorted(e) for e in full))
                time.sleep(1)
    finally:
        for p in probes.values():
            p.stop()
        cap.terminate()
        trials.close()
        run_net.stop(net, routers)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", required=True)
    ap.add_argument("--mechanism", choices=["bgpls", "poll"], default="bgpls")
    ap.add_argument("--scenarios", default="E1,E3,E5,E4,E6")
    ap.add_argument("--trials", type=int, default=30)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--wan-delay", type=int, default=10)
    ap.add_argument("--ctrl-delay", type=int, default=1)
    ap.add_argument("--probe-ms", type=int, default=50)
    ap.add_argument("--poll-period", type=float, default=1.0)
    ap.add_argument("--steady-s", type=int, default=120)
    ap.add_argument("--flaps", type=int, default=5)
    ap.add_argument("--flap-period", type=float, default=2.0)
    ap.add_argument("--out", required=True)
    run(ap.parse_args())
