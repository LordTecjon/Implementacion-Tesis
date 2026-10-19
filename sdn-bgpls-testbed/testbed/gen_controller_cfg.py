#!/usr/bin/env python3
"""
gen_controller_cfg.py -- Genera la configuracion BGP-LS de los controladores a partir de plan.json.

  python3 testbed/gen_controller_cfg.py --plan plans/smoke --mode bgpls --out plans/smoke/ctrl

 * OpenDaylight (BGPCEP) -> odl_protocol.xml, odl_topology.xml, odl_neighbor_<router>.xml,
                            apply_odl.sh y delete_odl.sh
   Rutas y cuerpos tomados de la guia de usuario de BGPCEP (documentacion 'latest', 2026):
     - instancia BGP-LS : /rests/data/openconfig-network-instance:network-instances/network-instance=global-bgp/protocols
     - vecino           : .../protocol=openconfig-policy-types:BGP,bgp-example/bgp-openconfig-extensions:bgp/neighbors
     - topologia BGP-LS : /rests/data/network-topology:network-topology  (id bgp-example-linkstate-topology)
   Se usa PUT (crea o reemplaza) para que apply_odl.sh se pueda repetir.
 * ONOS (solo si se vuelve a sumar) -> onos_bgp.json
"""
import argparse
import json
import os
import stat

ap = argparse.ArgumentParser()
ap.add_argument("--plan", required=True)
ap.add_argument("--mode", choices=["bgpls", "poll"], default="bgpls")
ap.add_argument("--hold", type=int, default=9)
ap.add_argument("--out", required=True)
a = ap.parse_args()
plan = json.load(open(os.path.join(a.plan, "plan.json")))
os.makedirs(a.out, exist_ok=True)
ctrls = plan.get("ctrls", ["onos", "odl"])


def peers_for(ctrl):
    out = []
    for r, x in plan["routers"].items():
        if not x["speaker"]:
            continue
        owner = plan["affinity"][str(x["domain"])]
        if a.mode == "bgpls" or owner == ctrl:
            out.append({"router": r, "ip": x["mgmt_ip"], "as": x["as"], "domain": x["domain"]})
    return out

"""
# ------------------------------------------------------------------ ONOS (opcional)
if "onos" in ctrls:
    onos = plan["controllers"]["onos"]
    onos_cfg = {"apps": {"org.onosproject.provider.bgp.cfg": {"bgpapp": {
        "routerId": onos["ip"], "localAs": onos["as"], "maxSession": 64, "lsCapability": True,
        "holdTime": a.hold, "largeAsCapability": True, "flowSpecCapability": "IPV4",
        "bgpPeer": [{"peerIp": p["ip"], "remoteAs": p["as"], "connectMode": "passive"} for p in peers_for("onos")]}}}}
    json.dump(onos_cfg, open(os.path.join(a.out, "onos_bgp.json"), "w"), indent=1)
"""

# ------------------------------------------------------------------ OpenDaylight
if "odl" in ctrls:
    odl = plan["controllers"]["odl"]
    PROTO = ("/rests/data/openconfig-network-instance:network-instances/network-instance=global-bgp/"
             "protocols/protocol=openconfig-policy-types:BGP,bgp-example")
    NEIGH = ("/rests/data/openconfig-network-instance:network-instances/network-instance=global-bgp/"
             "openconfig-network-instance:protocols/protocol=openconfig-policy-types:BGP,bgp-example/"
             "bgp-openconfig-extensions:bgp/neighbors")
    TOPO = "/rests/data/network-topology:network-topology/topology=bgp-example-linkstate-topology"

    def w(name, text):
        with open(os.path.join(a.out, name), "w") as f:
            f.write(text)

    w("odl_protocol.xml", f"""<protocol xmlns="http://openconfig.net/yang/network-instance">
    <name>bgp-example</name>
    <identifier xmlns:x="http://openconfig.net/yang/policy-types">x:BGP</identifier>
    <bgp xmlns="urn:opendaylight:params:xml:ns:yang:bgp:openconfig-extensions">
        <global>
            <config>
                <router-id>{odl['ip']}</router-id>
                <as>{odl['as']}</as>
            </config>
            <afi-safis>
                <afi-safi>
                    <afi-safi-name>LINKSTATE</afi-safi-name>
                </afi-safi>
            </afi-safis>
        </global>
    </bgp>
</protocol>
""")
    w("odl_topology.xml", """<topology xmlns="urn:TBD:params:xml:ns:yang:network-topology">
    <topology-id>bgp-example-linkstate-topology</topology-id>
    <topology-types>
        <bgp-linkstate-topology xmlns="urn:opendaylight:params:xml:ns:yang:odl-bgp-topology-types"></bgp-linkstate-topology>
    </topology-types>
    <rib-id xmlns="urn:opendaylight:params:xml:ns:yang:odl-bgp-topology-config">bgp-example</rib-id>
</topology>
""")
    peers = peers_for("odl")
    for p in peers:
        w(f"odl_neighbor_{p['router']}.xml", f"""<neighbor xmlns="urn:opendaylight:params:xml:ns:yang:bgp:openconfig-extensions">
    <neighbor-address>{p['ip']}</neighbor-address>
    <timers>
        <config>
            <hold-time>{a.hold}</hold-time>
        </config>
    </timers>
    <transport>
        <config>
            <passive-mode>true</passive-mode>
        </config>
    </transport>
    <config>
        <peer-type>EXTERNAL</peer-type>
        <peer-as>{p['as']}</peer-as>
    </config>
    <afi-safis>
        <afi-safi>
            <afi-safi-name>LINKSTATE</afi-safi-name>
        </afi-safi>
    </afi-safis>
</neighbor>
""")

    head = """#!/usr/bin/env bash
# Generado por gen_controller_cfg.py. Variables opcionales: ODL_BASE (por defecto http://127.0.0.1:8181), ODL_AUTH (admin:admin)
ODL="${ODL_BASE:-http://127.0.0.1:8181}"; AUTH="${ODL_AUTH:-admin:admin}"
cd "$(dirname "$0")"
call() {  # metodo ruta [archivo]
  if [ -n "$3" ]; then code=$(curl -s -o /tmp/odl_resp.txt -w '%{http_code}' -u "$AUTH" -X "$1" -H 'Content-Type: application/xml' -d @"$3" "$ODL$2")
  else code=$(curl -s -o /tmp/odl_resp.txt -w '%{http_code}' -u "$AUTH" -X "$1" "$ODL$2"); fi
  echo "$code  $1 $2"
  if [ "$code" = 000 ] || [ "$code" -ge 300 ]; then cat /tmp/odl_resp.txt; echo; fi
}
"""
    apply = head + f'call PUT "{PROTO}" odl_protocol.xml\ncall PUT "{TOPO}" odl_topology.xml\n'
    apply += "".join(f'call PUT "{NEIGH}/neighbor={p["ip"]}" odl_neighbor_{p["router"]}.xml\n' for p in peers)
    delete = head + "".join(f'call DELETE "{NEIGH}/neighbor={p["ip"]}"\n' for p in peers)
    w("apply_odl.sh", apply)
    w("delete_odl.sh", delete)
    for s in ("apply_odl.sh", "delete_odl.sh"):
        os.chmod(os.path.join(a.out, s), os.stat(os.path.join(a.out, s)).st_mode | stat.S_IEXEC)
    json.dump({"router_id": odl["ip"], "local_as": odl["as"], "listen_port": odl["port"], "hold_time_s": a.hold,
               "neighbors": [{"address": p["ip"], "peer_as": p["as"]} for p in peers]},
              open(os.path.join(a.out, "odl_neighbors.json"), "w"), indent=1)

print({c: len(peers_for(c)) for c in ctrls}, "peers ->", a.out)
