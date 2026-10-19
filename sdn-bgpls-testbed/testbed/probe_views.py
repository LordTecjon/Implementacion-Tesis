#!/usr/bin/env python3
"""
probe_views.py -- Sonda de vistas topologicas (northbound REST) de ONOS y OpenDaylight.

Consulta cada --interval-ms la topologia que cada controlador aprendio por BGP-LS, la normaliza a un
conjunto de aristas no dirigidas {frozenset({router_a, router_b})} y registra SOLO los puntos de cambio
(timestamp en ns, reloj de pared de la VM) en un JSONL:
   {"t_ns": ..., "ctrl": "onos", "n_nodes": 8, "edges": [["r1","r2"], ...]}

Normalizacion: los identificadores que devuelve cada controlador para un nodo BGP-LS contienen el
system-id IS-IS (xxxx.xxxx.xxxx) o el router-id (loopback). 'extract_router()' busca esas cadenas en el
id y las traduce con plan['idmap'].  >>> Tras el primer arranque, ejecuta con --dump para ver el JSON
crudo de TU version y ajusta ONOS_*/ODL_* si hace falta. <<<
"""
import argparse
import base64
import json
import os
import re
import sys
import threading
import time
import urllib.request

SYSID = re.compile(r"[0-9a-f]{4}\.[0-9a-f]{4}\.[0-9a-f]{4}", re.I)
IPV4 = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")

ONOS_URL = "http://127.0.0.1:8181/onos/v1/links"           # + /devices para nodos
ONOS_AUTH = ("onos", "rocks")
ODL_BASE = os.environ.get("ODL_BASE", "http://127.0.0.1:8181")
ODL_TOPO = os.environ.get("ODL_TOPO", "bgp-example-linkstate-topology")   # id creado por odl_topology.xml
ODL_URL = f"{ODL_BASE}/rests/data/network-topology:network-topology/topology={ODL_TOPO}?content=nonconfig"
ODL_AUTH = tuple(os.environ.get("ODL_AUTH", "admin:admin").split(":", 1))


def http_json(url, auth, timeout=2.0):
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    tok = base64.b64encode(f"{auth[0]}:{auth[1]}".encode()).decode()
    req.add_header("Authorization", f"Basic {tok}")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def extract_router(text, idmap):
    """Traduce un id de nodo del controlador al nombre de router del plan (o None)."""
    for m in SYSID.findall(text):
        if m.lower() in idmap:
            return idmap[m.lower()]
    for m in IPV4.findall(text):
        if m in idmap:
            return idmap[m]
    return None


def norm_onos(doc, idmap):
    edges = set()
    for l in doc.get("links", []):
        a = extract_router(l["src"]["device"], idmap)
        b = extract_router(l["dst"]["device"], idmap)
        if a and b and a != b:
            edges.add(frozenset((a, b)))
    return edges


def norm_odl(doc, idmap):
    edges = set()
    topo = doc.get("network-topology:topology", [{}])[0]
    for l in topo.get("link", []):
        a = extract_router(l["source"]["source-node"], idmap)
        b = extract_router(l["destination"]["dest-node"], idmap)
        if a and b and a != b:
            edges.add(frozenset((a, b)))
    return edges


FETCH = {"onos": (ONOS_URL, ONOS_AUTH, norm_onos), "odl": (ODL_URL, ODL_AUTH, norm_odl)}


class Probe(threading.Thread):
    """mode='bgpls': la vista es lo que el controlador aprendio por BGP-LS (todos los dominios).
    mode='poll'  : linea base. Vista = lo aprendido por BGP-LS de SUS dominios  U  copia de la vista del otro
                   controlador refrescada cada poll_period_s (sincronizacion periodica por REST)."""

    def __init__(self, ctrl, idmap, out_path, interval_ms=50, mode="bgpls", poll_period_s=1.0):
        super().__init__(daemon=True)
        self.ctrl, self.idmap, self.interval = ctrl, idmap, interval_ms / 1000
        self.mode, self.poll_period = mode, poll_period_s
        self.other = "odl" if ctrl == "onos" else "onos"
        self.remote_copy, self.remote_t = set(), 0.0
        self.out = open(out_path, "w")
        self.stop_flag = threading.Event()
        self.last = None
        self.latest = set()
        self.errors = 0

    def run(self):
        url, auth, norm = FETCH[self.ctrl]
        while not self.stop_flag.is_set():
            t0 = time.time_ns()
            try:
                edges = norm(http_json(url, auth), self.idmap)
                if self.mode == "poll":
                    if time.time() - self.remote_t >= self.poll_period:
                        ourl, oauth, onorm = FETCH[self.other]
                        self.remote_copy = onorm(http_json(ourl, oauth), self.idmap)
                        self.remote_t = time.time()
                    edges = edges | self.remote_copy
                t_ns = (t0 + time.time_ns()) // 2          # punto medio de la peticion (reduce sesgo)
                self.latest = edges
                if edges != self.last:
                    self.last = edges
                    self.out.write(json.dumps({"t_ns": t_ns, "ctrl": self.ctrl,
                                               "edges": sorted(sorted(e) for e in edges)}) + "\n")
                    self.out.flush()
            except Exception:
                self.errors += 1
                if self.latest is not None:        # controlador inaccesible: la vista deja de ser valida
                    self.latest, self.last = None, None
                    self.out.write(json.dumps({"t_ns": time.time_ns(), "ctrl": self.ctrl, "edges": [], "down": True}) + "\n")
                    self.out.flush()
            dt = time.time() - t0 / 1e9
            time.sleep(max(0.0, self.interval - dt))
        self.out.close()

    def stop(self):
        self.stop_flag.set()
        self.join(timeout=3)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--ctrl", choices=list(FETCH), required=True)
    ap.add_argument("--plan", help="plan.json (para idmap)")
    ap.add_argument("--dump", action="store_true", help="imprime el JSON crudo y sale")
    a = ap.parse_args()
    url, auth, norm = FETCH[a.ctrl]
    doc = http_json(url, auth)
    if a.dump:
        print(json.dumps(doc, indent=1)[:6000])
        sys.exit(0)
    idmap = json.load(open(a.plan))["idmap"]
    print(sorted(sorted(e) for e in norm(doc, idmap)))
