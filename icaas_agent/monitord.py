#!/usr/bin/env python
# -*- coding: utf-8 -*-
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

"""This module is the entrance point for the program that monitors the progress
made and uploads the result to a pithos deployment.
"""
from __future__ import print_function

import argparse
import sys
import os
import ConfigParser
import requests

from kamaki.clients.utils import https
from kamaki.clients.astakos import AstakosClient, AstakosClientError

from icaas_agent import __version__ as version

CERTS = '/etc/ssl/certs/ca-certificates.crt'


def error(msg):
    """Print an error message"""
    sys.stderr.write("Error: %s\n" % msg)


def read_manifest(manifest):
    """Read the manifest file"""

    config = ConfigParser.ConfigParser()

    if len(config.read(manifest)) == 0:
        error("Manifest file: `%s' is not parsable" % manifest)
        sys.exit(2)

    assert 'service' in config.sections()
    assert 'image' in config.sections()

    service = {}
    for key, value in config.items('service'):
        service[key] = value

    image = {}
    for key, value in config.items('image'):
        image[key] = value

    return service, image


def report_error(url, message):
    """Report an error to the icaas service"""

    error(message)
    r = requests.put(url, data={'status': "ERROR", 'reason': message})
    return r.ok


def validate_manifest(service, image):
    """Validate the data found in the manifest"""

    msg = "%s is missing from the %s section of the manifest"
    if 'status' not in service:
        error(msg % ("status", "service"))
        return False

    for key in 'url', 'token', 'log', 'path', 'status':
        if key not in service:
            report_error(service['status'], msg % (key, 'service'))
            return False

    if 'url' not in image:
        report_error(service['status'], msg % ('url', 'image'))
        return False

    if 'name' not in image:
        report_error(service['status'], msg % ('name', 'image'))
        return False

    # service:proxy, image:description, image:public are optional
    return True


def main():
    """Entry point for icaas-monitord"""

    parser = argparse.ArgumentParser(description="Monitoring daemon for icaas",
                                     version=version)
    parser.add_argument('file', help='file to monitor')
    parser.add_argument("-d", "--daemonize", dest="daemonize", default=False,
                        action="store_true",
                        help="detach the process from the shell")
    parser.add_argument("-m", "--manifest", dest="manifest",
                        metavar="MANIFEST", default="/.icaas_manifest",
                        help="specifies the name of the manifest file. The "
                        "default is %(default)s.")
    parser.add_argument("-i", "--interval", dest="interval", default=5,
                        type=int, metavar="INTERVAL",
                        help="upload the file every %(metavar)s seconds")
    args = parser.parse_args()

    if not os.path.isfile(args.file):
        parser.error("file to monitor not found")

    if not os.path.isfile(args.manifest):
        parser.error("Manifest file: `%s' not found. Use -m to specify a "
                     "different path" % args.manifest)

    if args.interval < 1:
        parser.error("Interval must be at least 1")

    service, image = read_manifest(args.manifest)

    if not validate_manifest(service, image):
        sys.exit(3)

    # Use the systems certificates
    https.patch_with_certs(CERTS)

    account = AstakosClient(service['url'], service['token'])

    try:
        account.authenticate()
    except AstakosClientError as err:
        report_error(service['status'], "Astakos: %s" % err)
        sys.exit(3)

# vim: set sta sts=4 shiftwidth=4 sw=4 et ai :
