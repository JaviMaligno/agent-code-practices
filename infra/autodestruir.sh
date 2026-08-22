#!/bin/bash
# Cuando la campaña acaba: sube los resultados, se borra la máquina y se borra su
# regla de firewall.
#
# El orden no es negociable. Una VM apagada sigue costando el disco y una
# destruida se lleva los datos con ella, así que primero al bucket y solo si eso
# se puede verificar se borra. Si la subida falla, se queda encendida: es más
# barato pagar unas horas de CPU que perder una campaña.
#
# La regla de firewall también se borra, y por eso está aquí: la primera versión
# solo borraba la instancia y dejó una regla permitiendo SSH a una máquina que ya
# no existía. Un agujero abierto en la red compartida del proyecto no es un cabo
# suelto cosmético.
#
# Los 30 minutos de gracia son porque entre bloques hay huecos de varios minutos
# —transformar un árbol, instalar dependencias— y borrar en uno de ellos mataría
# la campaña a mitad.
set -u
BUCKET="$1"
GRACIA=6
LIMPIOS=0
NOMBRE=$(curl -s -H "Metadata-Flavor: Google" http://metadata.google.internal/computeMetadata/v1/instance/name)
ZONA=$(curl -s -H "Metadata-Flavor: Google" http://metadata.google.internal/computeMetadata/v1/instance/zone | awk -F/ '{print $NF}')
cd "$HOME/agent-code-practices" || exit 1

while true; do
  n=$(ps -eo args | grep -c "acp[.]campaig""n")
  if [ "$n" -eq 0 ]; then
    LIMPIOS=$((LIMPIOS + 1)); echo "$(date +%H:%M) sin trabajo ($LIMPIOS/$GRACIA)"
  else
    [ "$LIMPIOS" -gt 0 ] && echo "$(date +%H:%M) vuelve a haber trabajo"
    LIMPIOS=0
  fi
  if [ "$LIMPIOS" -ge "$GRACIA" ]; then
    echo "$(date +%H:%M) subiendo resultados a gs://$BUCKET/$NOMBRE/"
    if gcloud storage cp out/*.jsonl "gs://$BUCKET/$NOMBRE/" 2>&1; then
      subidos=$(gcloud storage ls "gs://$BUCKET/$NOMBRE/" 2>/dev/null | wc -l)
      locales=$(ls out/*.jsonl 2>/dev/null | wc -l)
      echo "  subidos $subidos de $locales ficheros"
      if [ "$subidos" -ge "$locales" ] && [ "$locales" -gt 0 ]; then
        echo "$(date +%H:%M) datos a salvo: borrando la regla y la maquina"
        # La regla primero: después de borrar la instancia ya no hay quien lo haga.
        gcloud compute firewall-rules delete "$NOMBRE-ssh" --quiet 2>/dev/null || true
        gcloud compute instances delete "$NOMBRE" --zone "$ZONA" --quiet
        exit 0
      fi
    fi
    echo "$(date +%H:%M) la subida no se pudo verificar: NO se destruye"
    exit 1
  fi
  sleep 300
done
