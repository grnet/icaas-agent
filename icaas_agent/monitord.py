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
import signal
import daemon
import time
import string
import random
import subprocess
import StringIO
import syslog
import json

from kamaki.clients import ClientError
from kamaki.clients.utils import https
from kamaki.clients.astakos import AstakosClient, AstakosClientError
from kamaki.clients.pithos import PithosClient

from icaas_agent import __version__ as version

CERTS = '/etc/ssl/certs/ca-certificates.crt'
NAME = os.path.basename(sys.argv[0])
PID = '/var/run/%s.pid' % NAME


def error(msg):
    """Print an error message"""
    syslog.syslog(syslog.LOG_ERR, msg)
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

    return {'service': service, 'image': image}


def report_error(url, message):
    """Report an error to the icaas service"""

    error(message)
    data = {'status': "ERROR", 'reason': "%s: %s" % (NAME, message)}
    headers = {'Content-type': 'application/json'}
    request = requests.put(url, data=json.dumps(data), headers=headers)
    return request.ok


def validate_manifest(manifest):
    """Validate the data found in the manifest"""

    msg = "%s is missing from the %s section of the manifest"

    if 'status' not in manifest['service']:
        error(msg % ("status", "service"))
        return False
    status = manifest['service']['status']

    for key in 'url', 'token', 'log', 'status':
        if key not in manifest['service']:
            report_error(status, msg % (key, 'service'))
            return False

    for key in 'url', 'name', 'object':
        if key not in manifest['image']:
            report_error(status, msg % (key, 'image'))
            return False

    return True


def save_manifest(manifest, dest):
    """Save the manifest content in a shell sourcable format"""

    magic = "".join(random.choice(string.ascii_uppercase + string.digits)
                    for _ in xrange(16))

    for section in manifest:
        for key, value in manifest[section].items():
            name = "%s_ICAAS_%s_%s" % (magic, section.upper(), key.upper())
            os.environ[name] = value

    process = subprocess.Popen(['/bin/bash', '-c', 'set'],
                               stdout=subprocess.PIPE)
    output = StringIO.StringIO(process.communicate()[0])

    with open(dest, 'w') as destfh:
        for line in iter(output):
            if line.startswith(magic):
                destfh.write('export %s' % line[17:])


def do_main_loop(monitor, interval, client, name):
    """Main loop of the monitord service"""

    try:
        client.create_container(client.container)
    except ClientError as error:
        if error.status != 202:  # Ignore container already exists errors
            raise error
        else:
            syslog.syslog(syslog.LOG_WARNING,
                          "Container: `%s' already exists." % client.container)

    # Shut down gracefully on a SIGTERM or a SIGINT signal
    def terminate(signum, frame):
        name = 'SIGINT' if signum == signal.SIGINT else 'SIGTERM'
        syslog.syslog(syslog.LOG_NOTICE,
                      "Gracefully shutting down on a %s signal" % name)
        sys.exit(0)

    signal.signal(signal.SIGTERM, terminate)
    signal.signal(signal.SIGINT, terminate)

    cnt = 0
    while True:
        cnt += 1
        with open(monitor, "r") as m:
            client.upload_object(name, m)
        syslog.syslog(syslog.LOG_NOTICE,
                      'uploaded monitoring file: `%s for the %d time' %
                      (monitor, cnt))
        time.sleep(interval)


def main():
    """Entry point for icaas-monitord"""

    parser = argparse.ArgumentParser(description="Monitoring daemon for icaas",
                                     version=version)
    parser.add_argument('file', help='file to monitor')
    parser.add_argument("-d", "--daemonize", dest="daemonize", default=False,
                        action="store_true",
                        help="detach the process from the shell")
    parser.add_argument("-e", "--export-manifest", dest="export",
                        metavar="FILE", default='/var/lib/icaas/manifest.sh',
                        help="write manifest in shell sourceable format on "
                        "this file [%(default)s]")
    parser.add_argument("-m", "--manifest", dest="manifest",
                        metavar="MANIFEST", default="/etc/icaas/manifest.cfg",
                        help="specifies the name of the manifest file. The "
                        "default is %(default)s.")
    parser.add_argument("-i", "--interval", dest="interval", default=5,
                        type=int, metavar="INTERVAL",
                        help="upload the file every %(metavar)s seconds")
    args = parser.parse_args()

    if os.path.isfile(PID):
        error("Prοgram is already running. If this is not the case, please "
              "delete %s" % PID)
        sys.exit(2)

    if not os.path.isfile(args.file):
        parser.error("file to monitor not found")

    args.file = os.path.realpath(args.file)

    if not os.path.isfile(args.manifest):
        parser.error("Manifest file: `%s' not found. Use -m to specify a "
                     "different path" % args.manifest)

    if args.interval < 1:
        parser.error("Interval must be at least 1")

    syslog.syslog(syslog.LOG_INFO,
                  "Reading manifest file: `%s'" % args.manifest)
    manifest = read_manifest(args.manifest)

    if not validate_manifest(manifest):
        sys.exit(3)

    service = manifest['service']
    status = service['status']

    try:
        container, logname = service['log'].split('/', 1)
    except ValueError:
        report_error(status, 'Incorrect format for log entry in manifest file')

    # Use the systems certificates
    https.patch_with_certs(CERTS)

    account = AstakosClient(service['url'], service['token'])
    try:
        account.authenticate()
    except AstakosClientError as err:
        report_error(status, "Astakos: %s" % err)
        sys.exit(3)

    pithos = PithosClient(
        account.get_service_endpoints('object-store')['publicURL'],
        account.token, account.user_info['id'], container)

    if args.daemonize:
        daemon_context = daemon.DaemonContext(stdin=sys.stdin,
                                              stdout=sys.stdout,
                                              stderr=sys.stderr)
        daemon_context.open()

    with open(PID, 'w') as pid:
        pid.write("%d\n" % os.getpid())

    try:
        save_manifest(manifest, args.export)

        # Use SIGHUP to unblock from the sleep if necessary
        signal.signal(signal.SIGHUP, lambda x, y: None)

        if 'ICAAS_MONITOR_SIGSTOP' in os.environ:
            # Tell service supervisor that we are ready.
            syslog.syslog(
                syslog.LOG_NOTICE, "Stopping with SIGSTOP as the "
                "environment variable ICAAS_MONITOR_SIGSTOP is defined")
            os.kill(os.getpid(), signal.SIGSTOP)
            del os.environ['ICAAS_MONITOR_SIGSTOP']

        do_main_loop(args.file, args.interval, pithos, logname)
    finally:
        os.unlink(PID)

# vim: set sta sts=4 shiftwidth=4 sw=4 et ai :
