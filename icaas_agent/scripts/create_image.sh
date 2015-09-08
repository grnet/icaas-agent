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

info "Starting Image Creator as a Service"

FILENAME=$(basename "$ICAAS_IMAGE_SRC")
BASENAME=$(basename "$ICAAS_IMAGE_SRC" .zip)
if [[ ! "$FILENAME" =~ ^bitnami-[A-Za-z0-9.-]+\.zip ]]; then
    error "$FILENAME is not a bitnami image"
fi

CONTAINER=${ICAAS_IMAGE_OBJECT%%/*}
OBJECT=${ICAAS_IMAGE_OBJECT#*/}

TMP=$(mktemp -d /var/tmp/icaas-XXXXXXXX)
add_cleanup rm -rf "$TMP"

info "Downloading image from: $ICAAS_IMAGE_SRC"
curl -L "$ICAAS_IMAGE_SRC" > "$TMP/$FILENAME"

info "Unpacking zip file"
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

snf-mkimage $public -u "$OBJECT" -a "$ICAAS_SYNNEFO_URL" \
    -t "$ICAAS_SYNNEFO_TOKEN" -r "$ICAAS_IMAGE_NAME" --container "$CONTAINER" \
    -m DESCRIPTION="$ICAAS_IMAGE_DESCRIPTION" \
    -m EXCLUDE_TASK_DeleteSSHKeys=yes \
    --add-timestamp --host-run="$host_run" "$IMAGE"

info "Image creation finished"

# vim: set sta sts=4 shiftwidth=4 sw=4 et ai :

