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
if [[ ! "$FILENAME" =~ ^bitnami-[A-Za-z0-9._-]+\.zip ]]; then
    error "$FILENAME is not a bitnami image"
fi

TMP=$(mktemp -d /var/tmp/icaas-XXXXXXXX)
add_cleanup rm -rf "$TMP"


if ! curl --output /dev/null --silent --head --fail "$ICAAS_IMAGE_SRC"; then
    error "URL: \`$URL' does not exist!"
fi

info "Checking image size"
header="$(curl -# -L -I "$ICAAS_IMAGE_SRC" -o -)"
if [ -z "$header" ]; then
    error "Unable to find image size"
fi

if grep "HTTP/1.1 404 Not Found" > /dev/null <<< "$header"; then
    error "File: \`$ICAAS_IMAGE_SRC' does not exist"
fi

image_kb="$(grep -i Content-Length <<< "$header" | tail -1 | sed -e 's/.* \([0-9]*\).*$/\1/')"
if [ -z "$image_kb" ]; then
    image_kb=0
fi
image_kb=$(echo "$image_kb" / 1024 | bc)

if [[ ICAAS_PROGRESS_HEURISTIC < 1 ]]; then
    ICAAS_PROGRESS_HEURISTIC=1
fi

estimated_io="$(echo "( $((image_kb)) "'*'" ${ICAAS_PROGRESS_HEURISTIC} )" | bc)"

PROGRESS_STDOUT=$(mktemp --tmpdir icaas-progress-stdout.XXXXXXXXXX)
add_cleanup rm -f "$PROGRESS_STDOUT"
PROGRESS_STDERR=$(mktemp --tmpdir icaas-progress-stderr.XXXXXXXXXX)
add_cleanup rm -f "$PROGRESS_STDERR"

update_status() {
    local details status_json
    details="${*}"
    details="${details//\\/\\\\}"  # Escape backslash for json
    details="${details//\"/\\\"}"  # Escape double quotes for json
    status_json="{\
        \"status\": \"CREATING\",\
        \"details\": \"${details}\",\
        \"agent-progress\": {\
            \"current\": $(get_io_total),\
            \"total\": ${estimated_io} }}"
    curl -i -X PUT "$ICAAS_SERVICE_STATUS" \
         -H "X-ICAAS-Token: $ICAAS_SERVICE_TOKEN" \
         -H 'Content-type: application/json' \
         -d "$status_json" 2>"$PROGRESS_STDERR" >"$PROGRESS_STDOUT"
    if ! grep "^HTTP/1.0 204 NO CONTENT" "$PROGRESS_STDOUT" &>/dev/null; then
        echo "$(date) [ERROR] Progress status update failed!" >&2
        echo "$(date) [ERROR] DATA:"
        echo "$status_json" | sed 's/^/[ERROR] /' >&2
        echo "$(date) [ERROR] STDOUT:" >&2
        cat "$PROGRESS_STDOUT" | sed 's/^/[ERROR] /' >&2
        echo "$(date) [ERROR] STDERR:" >&2
        cat "$PROGRESS_STDERR" | sed 's/^/[ERROR] /'>&2
    fi
}

if [[ ICAAS_PROGRESS_INTERVAL < 5 ]]; then
    ICAAS_PROGRESS_INTERVAL=5
fi

kill_process () {
    local pid=$1
    if [ -n "$pid" ]; then
        kill -TERM "$pid" || true
    fi
}

update_progress_loop () {
    while true; do
        update_status
        sleep "${ICAAS_PROGRESS_INTERVAL}"
    done
}

update_progress_loop &
add_cleanup kill_process $!

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
    -m ALLOW_MOUNTED_TASK_OVERWRITING=yes \
    --add-timestamp --host-run="$host_run" "$IMAGE"

update_status "Image creation finished..."

info "Image creation finished"

# vim: set sta sts=4 shiftwidth=4 sw=4 et ai :
