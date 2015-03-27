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

error() { echo "$(date) [ERROR] $@" >&2; exit 1; }
warn() { echo "$(date) [WARNING] $@" >&2; }
info() { echo "$(date) [INFO] $@" >&2; }

info "Starting Image Creator as a Service"

FILENAME=$(basename "$ICAAS_IMAGE_URL")
BASENAME=$(basename "$ICAAS_IMAGE_URL" .zip)
if [[ ! "$FILENAME" =~ ^bitnami-[A-Za-z0-9.-]+\.zip ]]; then
    error "$FILENAME is not a bitnami image"
fi

CONTAINER=${ICAAS_IMAGE_OBJECT%/*}
OBJECT=${ICAAS_IMAGE_OBJECT#*/}

info "Downloading image from: $ICAAS_IMAGE_URL"
curl -L "$ICAAS_IMAGE_URL" > "/var/tmp/$FILENAME"

info "Unpacking zip file"
cd /var/tmp;
rm -rf /var/tmp/$BASENAME;
unzip -o -u /var/tmp/$FILENAME;
rm -f "!$";
cd -

IMAGE=/var/tmp/$BASENAME/$BASENAME.vmdk

info "Starting snf-image-creator"

if [ "$ICAAS_IMAGE_PUBLIC" = "True" ]; then
    public="--public"
fi

host_run=$(mktemp)
echo -e "#!/bin/sh\nrun-parts -v $DIRNAME/host-run" > "$host_run"
chmod +x "$host_run"

snf-mkimage $public -u "$OBJECT" -a "$ICAAS_SERVICE_URL" \
    -t "$ICAAS_SERVICE_TOKEN" -r "$ICAAS_IMAGE_NAME" --container "$CONTAINER" \
    -m DESCRIPTION="$ICAAS_IMAGE_DESCRIPTION" \
    --add-timestamp --host-run="$host_run" "$IMAGE"

info "Image creation finished"

# vim: set sta sts=4 shiftwidth=4 sw=4 et ai :

