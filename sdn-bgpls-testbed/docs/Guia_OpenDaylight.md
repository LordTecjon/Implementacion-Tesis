# Guía: BGP-LS con OpenDaylight (reemplaza a ONOS)

## 0. Qué cambia y por qué
- La imagen de ONOS 2.7 no trae el plugin BGP-LS (solo existe `onos-apps-bgprouter`). OpenDaylight (proyecto BGPCEP) sí soporta BGP-LS de forma nativa y documentada.
- La versión a cambiar: **OpenDaylight Vanadium-SR2** (`karaf-0.23.2`, 28-sep-2026). Se elige Vanadium por ser una versión de servicio ya estabilizada. Requiere **Java 21**.
- Ubuntu: la VM que ya tienes sirve; solo necesita Java 21. Mininet 2.3.1b4 declara soporte para Ubuntu 22.04.
- Con un solo controlador (ODL) solo se puede ejecutar el mecanismo `bgpls`. La línea base `poll` y la heterogeneidad ONOS–ODL quedan **pendientes hasta consulta con asesor**.

## 1. Detener ONOS (libera memoria)
```bash
docker stop onos
```

## 2. Java 21
```bash
sudo apt update && sudo apt -y install openjdk-21-jdk
java -version        # debe indicar 21
```
Si el paquete no existe en tu repositorio, instala un JDK 21 (p. ej., Temurin).

## 3. Descargar y arrancar OpenDaylight
```bash
cd ~ && wget https://nexus.opendaylight.org/content/repositories/opendaylight.release/org/opendaylight/integration/karaf/0.23.2/karaf-0.23.2.tar.gz
tar xzf karaf-0.23.2.tar.gz && cd karaf-0.23.2
./bin/karaf          # abre la consola de Karaf; deja esta terminal ocupada
```

## 4. Instalar los features (dentro de la consola de Karaf)
```bash
feature:install odl-restconf-nb odl-bgpcep-bgp
feature:list -i | grep -E "restconf|bgpcep"
```
Criterio: `feature:list` muestra ambos como instalados.

Comprobar la API REST desde otra terminal:
```bash
curl -s -u admin:admin "http://127.0.0.1:8181/rests/data/bgp-rib:bgp-rib?content=nonconfig" | head -c 400
```
Debe devolver un JSON con una `rib` de nombre `example-bgp-rib` (instancia por defecto). Si da 404, prueba la ruta `/restconf/data/...` en lugar de `/rests/data/...`.

## 5. Generar el plan y la configuración de ODL
Desde la carpeta del proyecto:
```bash
python3 testbed/topo_gen.py --topo grid:3x4 --domains 2 --mode bgpls --controllers odl --out plans/smoke
python3 testbed/gen_controller_cfg.py --plan plans/smoke --mode bgpls --out plans/smoke/ctrl
bash plans/smoke/ctrl/apply_odl.sh
```
`apply_odl.sh` imprime una línea por llamada con el código HTTP. Esperado: `200`, `201` o `204`.

Para quitar los vecinos de un plan anterior antes de usar otro: `bash plans/<plan_anterior>/ctrl/delete_odl.sh`.

Crea: una instancia BGP `bgp-example` (AS 65200, router-id 10.255.0.102, familia LINKSTATE), un vecino pasivo por cada speaker, y el exportador de topología `bgp-example-linkstate-topology`. ODL escucha BGP en el puerto **1790** por defecto, que es el que usan los routers FRR generados.

## 6. Levantar la red
```bash
sudo pkill -f /usr/lib/frr/ ; sudo mn -c ; sudo rm -rf /tmp/frr /tmp/frrcfg
sudo "$(which python3)" testbed/run_net.py --plan plans/smoke --cli
```
En otra terminal, con un speaker de la lista (`python3 -c "import json;p=json.load(open('plans/smoke/plan.json'));print([r for r,x in p['routers'].items() if x['speaker']])"`):
```bash
mininet> <spk> vtysh --vty_socket /tmp/frr/<spk> -c "show bgp summary"
mininet> <spk> vtysh --vty_socket /tmp/frr/<spk> -c "show bgp link-state link-state"
```
Criterio: la sesión con `10.255.0.102` en **Established**. Si sigue en `Active`: `mininet> <spk> vtysh --vty_socket /tmp/frr/<spk> -c "clear bgp *"`.

## 7. Verificar la topología en ODL
```bash
curl -s -u admin:admin "http://127.0.0.1:8181/rests/data/network-topology:network-topology/topology=bgp-example-linkstate-topology?content=nonconfig" \
  | jq '.["network-topology:topology"][0] | {nodos: (.node|length), enlaces: (.link|length)}'
python3 testbed/probe_views.py --ctrl odl --plan plans/smoke/plan.json
```
Criterio: `nodos` = número de routers del plan (12 en `smoke`) y `enlaces` = 2 × enlaces intra-dominio (los enlaces BGP-LS son dirigidos); `probe_views.py` imprime la lista de aristas y debe coincidir con las intra-dominio del plan.
Si el arreglo `node` sale vacío: `GET .../bgp-rib/rib=bgp-example/loc-rib/tables=bgp-linkstate:linkstate-address-family,bgp-linkstate:linkstate-subsequent-address-family/bgp-linkstate:linkstate-routes?content=nonconfig` muestra qué rutas BGP-LS llegaron.

## 8. Piloto
```bash
export ODL_HOME=~/karaf-0.23.2
sudo -E "$(which python3)" testbed/run_trials.py --plan plans/smoke --mechanism bgpls --scenarios E1 --trials 5 --out results/pilot
python3 analysis/compute_metrics.py results/pilot
```
Criterios: todas las filas convergen y ningún `t_proc` es negativo. Para el escenario E4 (caída del controlador) el script usa `$ODL_HOME/bin/stop` y `$ODL_HOME/bin/start`.

## 9. Diagnóstico rápido
| Síntoma | Qué revisar |
|---|---|
| `apply_odl.sh` da 404 | Prueba `/restconf/...` en vez de `/rests/...`; confirma `odl-restconf-nb` instalado |
| `apply_odl.sh` da 400/409 | Lee el cuerpo que imprime el script; puede faltar una política RIB (la guía indica que la política debe existir antes del protocolo) |
| Sesión BGP en `Active` | `ss -ltnp \| grep 1790`; la IP del vecino en ODL debe ser la IP de gestión del speaker; el AS debe coincidir; en Karaf `log:tail` |
| Topología vacía con sesión `Established` | Que `show bgp link-state link-state` del speaker liste nodos; si solo lista uno, usa más speakers (`--speakers N`) |
| IDs que no reconoce la sonda | Los nodos de ODL contienen `router=0000.0000.00NN` (system-id IS-IS), que la sonda traduce con el plan |

## 10. Impacto en la tesis
- **Heterogeneidad ONOS–ODL:** sin ONOS desaparece. Opciones a tomar: (a) estudiar primero solo ODL; (b) más adelante decidir si sumar ONOS **consultare al asesor**.
- **Línea base `poll`:** dependía de dos controladores. Alternativa: sondeo periódico de la LSDB de IS-IS por `vtysh` cada P segundos, o una segunda instancia de ODL.
