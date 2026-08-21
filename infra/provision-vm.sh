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
#   ./infra/provision-vm.sh lanzar T0,T1,T2,T3
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
    --scopes cloud-platform \
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
  echo "VM creada. El script de arranque tarda un par de minutos en instalar Docker."
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
  # Una condición por proceso, con su registro y su directorio de trabajo: dos
  # procesos sobre el mismo jsonl se entrelazan, y la reanudación lo lee para
  # saber qué falta. `setsid` para que sobreviva al cierre de la sesión ssh.
  for C in ${CONDICIONES//,/ }; do
    ssh_vm "
      cd agent-code-practices && . ~/.acp-env
      mkdir -p out work-$C logs
      setsid nohup .venv/bin/python -m acp.campaign candidates/python-stdnum \
        --tasks tasks/python-stdnum \
        --log out/campana-$C.jsonl \
        --workdir work-$C \
        --model gpt-5.4-mini \
        --conditions $C \
        --runs $PASADAS \
        > logs/$C.log 2>&1 < /dev/null &
      echo '$C lanzada'
    "
  done
  ;;

estado)
  ssh_vm "
    cd agent-code-practices
    echo \"celdas: \$(cat out/campana-T*.jsonl 2>/dev/null | wc -l)\"
    ps -o etime=,args= -C python3 2>/dev/null | grep -c 'acp.campaign' | xargs -I{} echo 'procesos: {}'
    tail -n2 logs/T*.log 2>/dev/null
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
  ;;

*)
  sed -n '1,30p' "$0"
  exit 1
  ;;
esac
