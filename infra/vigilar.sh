#!/bin/bash
# Vigila una tanda sin confundir "no puedo comprobar" con "ya no está".
#
# La primera versión daba por autodestruida una VM cuando `gcloud` fallaba, y
# eso ocurre por cosas que no son la VM: el login caduca, el ssh se cae si la
# máquina está cargada, la red parpadea. Informó de tres máquinas destruidas que
# estaban corriendo.
#
# Ahora un fallo de consulta se dice como fallo de consulta, y solo se declara
# destruida cuando la API responde correctamente y la instancia no aparece.
set -u
VM="$1"; PROYECTO="${2:-data-science-364702}"; ZONA="${3:-europe-west1-b}"
while true; do
  salida=$(gcloud compute instances describe "$VM" --project "$PROYECTO" \
             --zone "$ZONA" --format='value(status)' 2>&1)
  codigo=$?
  if [ $codigo -eq 0 ]; then
    [ -n "$salida" ] && echo "$(date +%H:%M) $VM: $salida"
  elif echo "$salida" | grep -qi "was not found"; then
    echo "$(date +%H:%M) $VM: DESTRUIDA (la API responde y no está)"
    exit 0
  else
    # Login caducado, red, cuota: no se sabe nada de la VM.
    echo "$(date +%H:%M) $VM: NO SE PUDO COMPROBAR — $(echo "$salida" | head -1 | cut -c1-80)"
  fi
  sleep 300
done
