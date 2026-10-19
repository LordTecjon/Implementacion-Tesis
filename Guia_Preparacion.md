# Guía: Preparacion de VM Ubuntu 22.04

## 0. Pasos a seguir (editable)
- Version recomendada: **OpenDaylight Vanadium-SR2**, ya que es una version del servicio ya estabilizada. Requiere entre otros aspectos **Java 21**.
- Otra herramienta instalar es **Mininet** especificamente la version 2.3.1b4.

## 1.  Actualizacion de la lista de paquetes y versiones disponibles en los repositorios de Ubuntu
```bash
sudo apt update
```

## 2. Instalacion de actualizaciones disponibles para los parquetes que ya estan instalados
```bash
sudo apt upgrade
```

## 3. Herramientas basicas
```bash
sudo apt install -y git
sudo apt install -y curl
sudo apt install -y wget
sudo apt install -y unzip
sudo apt install -y tar
sudo apt install -y ca-certificates
sudo apt install -y gnupg
sudo apt install -y build-essential
sudo apt install -y pkg-config
```

## 4. Herramientas de red
```bash
sudo apt install -y iproute2
sudo apt install -y iputils-ping
sudo apt install -y net-tools
sudo apt install -y tcpdump
sudo apt install -y tshark
sudo apt install -y jq
```

## 5. Editores de texto
```bash
sudo apt install -y vim
sudo apt install -y nano
```

## 6. Python 3
```bash
sudo apt install -y python3
sudo apt install -y python3-pip
sudo apt install -y python3-venv
```

## 7. Open vSwitch y Docker
```bash
sudo apt install -y openvswitch-switch
sudo apt install -y docker.io
```
## 8. Mininet 2.3.1b4
```bash
git clone https://github.com/mininet/mininet
cd mininet
git checkout 2.3.1b4
./util/install.sh -n
```
## Prueba rapida
```bash
sudo mn --test pingall
```
## 9. Instalar Java 21
```bash
sudo apt install -y openjdk-21-jdk
```
En Ubuntu suele estar bajo `/usr/lib/jvm/`, pero usaremos la ruta de la VM.

Configura JAVA_HOME automáticamente:

```bash
JAVA_BIN="$(readlink -f "$(which java)")"
JAVA_HOME_PATH="$(dirname "$(dirname "$JAVA_BIN")")"

echo "JAVA_HOME=$JAVA_HOME_PATH"

echo "export JAVA_HOME=$JAVA_HOME_PATH" >> ~/.bashrc
echo 'export PATH=$JAVA_HOME/bin:$PATH' >> ~/.bashrc

source ~/.bashrc
```
verificar
```bash
echo "$JAVA_HOME"
java -version
javac -version
```
## 10. OpenDaylight Vanadium
```bash
cd ~/sdn-lab/odl
wget https://nexus.opendaylight.org/content/repositories/opendaylight.release/org/opendaylight/integration/karaf/0.23.1/karaf-0.23.1.tar.gz
```
comprobar
```bash
ls -lh karaf-0.23.1.tar.gz
```
## descomprimir e iniciar
```bash
tar -xzf karaf-0.23.1.tar.gz
cd karaf-0.23.1
```
iniciar
```bash
./bin/karaf
```