#!/usr/bin/env bash
# Una VM para la campaña ampliada, y por qué hace falta.
#
# La campaña del portátil cabe: 24 celdas, una noche. Lo que no cabe es lo que
# el diseño pide para poder concluir algo — 3 pasadas por celda, dos tiers de
# modelo, cuatro repositorios — porque la varianza medida es del mismo orden que
# los efectos: la misma tarea, condición y modelo dio fallo (27 turnos), acierto
# (17) y acierto (40) en tres pasadas.
#
# La VM va en GCP y no en Azure porque ahí hay `roles/editor`; en la suscripción
# de Azure los permisos son `*/read` y no se puede crear nada.
#
# El modelo NO cambia, y está verificado sirviendo la petición. La pasarela
# enruta `gpt-5.4-mini-kyc-tst` a `azure/gpt-5.4-mini-kyc`, cuyo modelo es
# gpt-5.4-mini versión 2026-03-17; desde la VM se llama al despliegue
# `gpt-5.4-mini`, que responde identificándose como `gpt-5.4-mini-2026-03-17`.
# Misma familia y misma versión.
#
# Lo que sí cambia, y hay que declararlo al publicar: el transporte (con sus
# reintentos y timeouts) y la cuota del despliegue —100 frente a 20—, que afecta
# a la cola y por tanto al reloj, no al resultado. Por eso el transporte se elige
# al arrancar una campaña y no se mezcla dentro de una.
#
# Uso:
#   ./infra/provision-vm.sh crear
#   ./infra/provision-vm.sh credenciales <endpoint-azure> <fichero-con-la-key>
#   ./infra/provision-vm.sh lanzar T0,T1,T2,T3 3 python-stdnum
#   ./infra/provision-vm.sh lanzar T0,T1,T2,T3 3 pint
#   ./infra/provision-vm.sh lanzar T0,T1,T2,T3 3 python-stdnum gpt-5.4 alto
#   ./infra/provision-vm.sh traer      # baja los registros
#   ./infra/provision-vm.sh destruir
#
# Ninguna credencial vive en este fichero: la clave se copia por scp a un
# fichero con permisos 600 en la VM y nunca se pasa por la línea de comandos de
# un proceso remoto, donde la vería cualquier `ps`.

set -euo pipefail

PROYECTO="${ACP_GCP_PROJECT:-data-science-364702}"
ZONA="${ACP_GCP_ZONE:-europe-west1-b}"
NOMBRE="${ACP_VM_NAME:-acp-campana}"
# 8 vCPU porque LibCST es de un solo hilo y las condiciones corren en paralelo:
# una por proceso, cada una con su contenedor. 32 GB sobran, pero el disco no:
# cada celda copia el árbol dos veces.
TIPO="${ACP_VM_TYPE:-e2-standard-8}"
DISCO="${ACP_VM_DISK:-100GB}"
REPO="https://github.com/JaviMaligno/agent-code-practices.git"

ssh_vm() { gcloud compute ssh "$NOMBRE" --project "$PROYECTO" --zone "$ZONA" --command "$1"; }

case "${1:-}" in
crear)
  gcloud compute instances create "$NOMBRE" \
    --project "$PROYECTO" --zone "$ZONA" \
    --machine-type "$TIPO" \
    --image-family debian-12 --image-project debian-cloud \
    --boot-disk-size "$DISCO" --boot-disk-type pd-balanced \
    --scopes cloud-platform --tags "$NOMBRE" \
    --metadata-from-file startup-script=<(cat <<'ARRANQUE'
#!/bin/bash
set -eux
apt-get update
apt-get install -y git python3-venv python3-pip docker.io
systemctl enable --now docker
# El usuario que entra por ssh tiene que poder hablar con el demonio sin sudo:
# la campaña lanza `docker` desde Python.
for u in $(ls /home); do usermod -aG docker "$u" || true; done
ARRANQUE
)
  # La red `default` de este proyecto no deja entrar por el 22: la regla que lo
  # permite desde cualquier sitio exige el tag `https-server` —y ponerlo abriría
  # también 443 y 4000 al mundo— y la de ssh solo admite una lista de IPs fijas.
  # Así que una regla propia, acotada a esta IP y a esta VM, que se borra con ella
  # en vez de tocar reglas compartidas del proyecto.
  MI_IP=$(curl -s https://api.ipify.org)
  gcloud compute firewall-rules create "$NOMBRE-ssh" \
    --project "$PROYECTO" --network default --direction INGRESS \
    --action allow --rules tcp:22 \
    --source-ranges "$MI_IP/32" --target-tags "$NOMBRE" \
    --description "SSH a la VM de la campana. Se borra con la VM." || true
  echo "VM creada ($MI_IP autorizada al 22). El arranque tarda un par de minutos en instalar Docker."
  ;;

preparar)
  # Repo, entorno y el clon del repositorio bajo prueba.
  # libcst solo con rueda: compilarlo pide toolchain de Rust.
  ssh_vm "
    set -eux
    [ -d agent-code-practices ] || git clone $REPO
    cd agent-code-practices
    git pull --ff-only
    python3 -m venv .venv
    .venv/bin/pip install --quiet --upgrade pip
    .venv/bin/pip install --quiet --only-binary :all: -e '.[dev]'
    mkdir -p candidates out
    [ -d candidates/python-stdnum ] || git clone --quiet https://github.com/arthurdejong/python-stdnum.git candidates/python-stdnum
    .venv/bin/python -m pytest -q --ignore=tests/test_docker_integration.py 2>&1 | tail -3
  "
  ;;

credenciales)
  # australiaeast es el recurso donde vive el despliegue; el endpoint regional
  # más la api-key determinan a qué recurso se habla.
  ENDPOINT="${2:-https://australiaeast.api.cognitive.microsoft.com}"
  FICHERO_KEY="${3:-$HOME/.acp-azure-key}"
  # La clave viaja por scp y aterriza en 600. No se pasa como argumento de un
  # comando remoto: eso la deja visible en `ps` de la VM.
  gcloud compute scp "$FICHERO_KEY" "$NOMBRE:.acp-azure-key" \
    --project "$PROYECTO" --zone "$ZONA"
  ssh_vm "chmod 600 ~/.acp-azure-key && echo 'export ACP_MODEL_BACKEND=azure' > ~/.acp-env && echo 'export AZURE_OPENAI_ENDPOINT=$ENDPOINT' >> ~/.acp-env && chmod 600 ~/.acp-env"
  ssh_vm "cd agent-code-practices && . ~/.acp-env && .venv/bin/python -c \"
from acp.model.client import ask
print('el modelo responde:', repr(ask('di solo: ok', model='gpt-5.4-mini', max_tokens=200)))
\""
  ;;

lanzar)
  CONDICIONES="${2:-T0,T1,T2,T3}"
  PASADAS="${3:-3}"
  # El repo bajo prueba es un parámetro porque la VM aguanta varios a la vez:
  # medido, 8 vCPU con load 3,9 y 26 GB libres corriendo cuatro condiciones.
  BAJO_PRUEBA="${4:-python-stdnum}"
  # El par de tiers lo fija §484 del spec: gpt-5.4-mini bajo y gpt-5.4 alto, el
  # mismo par que el artículo previo para que las cifras se puedan comparar.
  MODELO="${5:-gpt-5.4-mini}"
  # La etiqueta separa lo que convive en la máquina. Sin ella, dos tiers del
  # mismo repo y condición nombrarían igual su árbol, pedirían el mismo
  # contenedor y uno mataría el del otro a mitad de celda.
  ETIQUETA="${6:-}"
  SUF=""
  [ -n "$ETIQUETA" ] && SUF="-$ETIQUETA"
  # Una condición por proceso, con su registro y su directorio de trabajo: dos
  # procesos sobre el mismo jsonl se entrelazan, y la reanudación lo lee para
  # saber qué falta. `setsid` para que sobreviva al cierre de la sesión ssh.
  for C in ${CONDICIONES//,/ }; do
    ssh_vm "
      cd agent-code-practices && . ~/.acp-env
      [ -d candidates/$BAJO_PRUEBA ] || git clone --quiet \$(.venv/bin/python -c \"
import json,sys
urls={'python-stdnum':'https://github.com/arthurdejong/python-stdnum.git',
      'pint':'https://github.com/hgrecco/pint.git',
      'sqlglot':'https://github.com/tobymao/sqlglot.git',
      'holidays':'https://github.com/vacanza/holidays.git'}
print(urls['$BAJO_PRUEBA'])\") candidates/$BAJO_PRUEBA
      mkdir -p out work-$BAJO_PRUEBA-$C$SUF logs
      setsid nohup .venv/bin/python -m acp.campaign candidates/$BAJO_PRUEBA \
        --tasks tasks/$BAJO_PRUEBA \
        --log out/campana-$BAJO_PRUEBA-$C$SUF.jsonl \
        --workdir work-$BAJO_PRUEBA-$C$SUF \
        --model $MODELO \
        --label '$ETIQUETA' \
        --conditions $C \
        --runs $PASADAS \
        > logs/$BAJO_PRUEBA-$C$SUF.log 2>&1 < /dev/null &
      echo '$BAJO_PRUEBA $C $MODELO lanzada'
    "
  done
  ;;

estado)
  ssh_vm "
    cd agent-code-practices
    for f in out/campana-*.jsonl; do
      [ -f \"\$f\" ] && echo \"  \$(basename \$f): \$(wc -l < \$f) celdas\"
    done
    # ps -C python3 no vale (las comillas invertidas aqui dentro las ejecuta el
    # shell remoto): el proceso es .venv/bin/python, y no encontrarlo hacia
    # leer una campana viva como si no corriera. El patron va partido para
    # que este grep no se cuente a si mismo.
    echo \"procesos: \$(ps -eo args | grep -c 'acp[.]campaig''n')\"
    docker ps --format '{{.Names}}' 2>/dev/null | grep '^acp-' | sed 's/^/  /'
    grep -hE '^\\[T[0-3]\\]' logs/T*.log 2>/dev/null | tail -n4
  "
  ;;

traer)
  mkdir -p out/vm
  gcloud compute scp --recurse "$NOMBRE:agent-code-practices/out/*.jsonl" out/vm/ \
    --project "$PROYECTO" --zone "$ZONA"
  echo "registros en out/vm/"
  ;;

destruir)
  gcloud compute instances delete "$NOMBRE" --project "$PROYECTO" --zone "$ZONA" --quiet
  # La regla no sirve para nada sin la VM, y dejarla es dejar un agujero abierto
  # en la red compartida del proyecto.
  gcloud compute firewall-rules delete "$NOMBRE-ssh" --project "$PROYECTO" --quiet || true
  ;;

*)
  sed -n '1,30p' "$0"
  exit 1
  ;;
esac
