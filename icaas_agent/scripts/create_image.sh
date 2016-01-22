#!/bin/bash
#
# Copyright (C) 2015 GRNET S.A.
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

set -e

NAME="$0"
DIRNAME=$(dirname $NAME)

CLEANUP=( )

cleanup() {
    trap - EXIT
    for cmd in "${CLEANUP[@]}"; do
        $cmd
    done
}

trap cleanup EXIT

add_cleanup() {
    local cmd=""
    for arg; do cmd+=$(printf "%q " "$arg"); done
    CLEANUP=( "$cmd" "${CLEANUP[@]}" )
}

error() { echo "$(date) [ERROR] $@" >&2; exit 1; }
warn() { echo "$(date) [WARNING] $@" >&2; }
info() { echo "$(date) [INFO] $@" >&2; }

DISK_IO_BASE=0

${DIRNAME}/tcpcount.sh stop icaas || true
${DIRNAME}/tcpcount.sh start icaas -r 0.0.0.0/0

get_disk_io_written () {
    iostat | sed -ne '/Device/,$s/.* \([0-9]*\)$/\1 + \\/p;$a 0 - '"${DISK_IO_BASE}" | bc
}

DISK_IO_BASE=$(get_disk_io_written)

get_net_io_total () {
    echo $(${DIRNAME}/tcpcount.sh show | cut -d' ' -f7) / 1024 | bc
}

get_io_total () {
    echo $(get_disk_io_written) + $(get_net_io_total) | bc
}

info "Starting Image Creator as a Service"

FILENAME=$(basename "$ICAAS_IMAGE_SRC")
BASENAME=$(basename "$ICAAS_IMAGE_SRC" .zip)
if [[ ! "$FILENAME" =~ ^bitnami-[A-Za-z0-9.-]+\.zip ]]; then
    error "$FILENAME is not a bitnami image"
fi

TMP=$(mktemp -d /var/tmp/icaas-XXXXXXXX)
add_cleanup rm -rf "$TMP"

image_kb="$(curl -L -I "$ICAAS_IMAGE_SRC" -o - | grep -i Content-Length | tail -1 | sed -e 's/.* \([0-9]*\).*$/\1/')"
if [ -z "$image_kb" ]; then
    image_kb=0
fi
image_kb=$(echo "$image_kb" / 1024 | bc)

if [[ ICAAS_PROGRESS_HEURISTIC < 1 ]]; then
    ICAAS_PROGRESS_HEURISTIC=1
fi

estimated_io="$(echo "( $((image_kb)) "'*'" ${ICAAS_PROGRESS_HEURISTIC} )" | bc)"

update_status() {
    details="${*}"
    details="${details//\\/\\\\}"  # Escape backslash for json
    details="${details//\"/\\\"}"  # Escape double quotes for json
    status_json="{\
        \"status\": \"CREATING\",\
        \"details\": \"${details}\",\
        \"agent-progress\": {\
            \"current\": $(get_io_total),\
            \"total\": ${estimated_io} }}"
    curl -i "$ICAAS_SERVICE_STATUS" -H "X-ICAAS-Token: $ICAAS_SERVICE_TOKEN" \
        -H 'Content-type: application/json' -X PUT \
        -d "$status_json"
}

if [[ ICAAS_PROGRESS_INTERVAL < 5 ]]; then
    ICAAS_PROGRESS_INTERVAL=5
fi

kill_update_progress_loop () {
    if [ -n "$LOOP_PID" ]; then
        kill -TERM "$LOOP_PID" || true
    fi
}

update_progress_loop () {
    while true; do
        update_status
        sleep "${ICAAS_PROGRESS_INTERVAL}"
    done
}

update_progress_loop &
LOOP_PID=$!
add_cleanup kill_update_progress_loop

info "Downloading image from: $ICAAS_IMAGE_SRC"
update_status "Downloading image file..."
curl -L "$ICAAS_IMAGE_SRC" > "$TMP/$FILENAME"

info "Unpacking zip file"
update_status "Unpacking image archive..."
unzip -o -u "$TMP/$FILENAME" -d "$TMP"

IMAGE="$(ls -1 $TMP/$BASENAME/*.vmdk | tail -1)"

info "Starting snf-image-creator"

if [ "$ICAAS_IMAGE_PUBLIC" = "True" ]; then
    public="--public"
fi

host_run=$(mktemp)
echo -e "#!/bin/sh\nrun-parts -v $DIRNAME/host_run" > "$host_run"
add_cleanup rm -f "$host_run"
chmod +x "$host_run"

update_status "Creating image..."
snf-mkimage $public -u "${ICAAS_IMAGE_OBJECT}" -a "$ICAAS_SYNNEFO_URL" \
    -t "$ICAAS_SYNNEFO_TOKEN" -r "$ICAAS_IMAGE_NAME" \
    --container "${ICAAS_IMAGE_CONTAINER}" \
    -m DESCRIPTION="$ICAAS_IMAGE_DESCRIPTION" \
    -m EXCLUDE_TASK_DeleteSSHKeys=yes \
    --add-timestamp --host-run="$host_run" "$IMAGE"

update_status "Image creation finished..."

info "Image creation finished"

# vim: set sta sts=4 shiftwidth=4 sw=4 et ai :
