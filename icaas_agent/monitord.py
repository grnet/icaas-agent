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
import signal
import daemon
import time
import subprocess
import syslog
import tempfile

from kamaki.clients import ClientError
from kamaki.clients.utils import https
from kamaki.clients.astakos import AstakosClient, AstakosClientError
from kamaki.clients.pithos import PithosClient

from icaas_agent import __version__ as version
from icaas_agent.scripts import get_script
from icaas_agent.report import Report

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

    syslog.syslog(syslog.LOG_INFO, "Reading manifest file: `%s'" % manifest)
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


def do_main_loop(interval, client, name):
    """Main loop of the monitord service"""

    try:
        client.create_container(client.container)
    except ClientError as err:
        if err.status != 202:  # Ignore container already exists errors
            raise
        else:
            syslog.syslog(syslog.LOG_WARNING,
                          "Container: `%s' already exists." % client.container)

    monitor = tempfile.NamedTemporaryFile(prefix='icaas-log-')
    icaas = subprocess.Popen(['/bin/bash', get_script('create_image')],
                             stdout=monitor, stderr=monitor)

    def terminate(signum, frame):
        """Shut down gracefully on a SIGTERM or a SIGINT signal"""
        name = 'SIGINT' if signum == signal.SIGINT else 'SIGTERM'
        syslog.syslog(syslog.LOG_NOTICE,
                      "Gracefully shutting down on a %s signal" % name)
        sys.exit(0)

    signal.signal(signal.SIGTERM, terminate)
    signal.signal(signal.SIGINT, terminate)

    cnt = 0
    while True:
        cnt += 1
        with open(monitor.name, "r") as m:
            client.upload_object(name, m)
        syslog.syslog(syslog.LOG_NOTICE,
                      'uploaded monitoring file for the %d time' % cnt)
        if icaas.poll() is not None:
            if icaas.returncode == 0:
                return True
            else:
                with open(monitor.name, "r") as m:
                    sys.stderr.write("".join(m.readlines()))
                return False

        time.sleep(interval)


def get_args():
    """Get input arguments"""

    parser = argparse.ArgumentParser(description="Monitoring daemon for icaas",
                                     version=version)
    parser.add_argument("-d", "--daemonize", dest="daemonize", default=False,
                        action="store_true",
                        help="detach the process from the shell")
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

    if not os.path.isfile(args.manifest):
        parser.error("Manifest file: `%s' not found. Use -m to specify a "
                     "different path" % args.manifest)

    if args.interval < 1:
        parser.error("Interval must be at least 1")

    return args


def main():
    """Entry point for icaas-monitord"""

    args = get_args()

    manifest = read_manifest(args.manifest)

    if 'status' not in manifest['service']:
        sys.stderr.write('"status" is missing from the service section of the '
                         'manifest')
        sys.exit(3)

    if 'insecure' in manifest and manifest['insecure'].lower() == 'true':
        verify = False
    else:
        verify = True

    report = Report(manifest['service']['status'], verify=verify,
                    log=sys.stderr)

    def missing_key(key, section):
        """missing key message"""
        return "`%s' is missing from the `%s' section of the manifest" % \
            (key, section)

    # Validate the manifest
    for key in 'url', 'token', 'log', 'status':
        if key not in manifest['service']:
            report.error(missing_key(key, 'service'))
            sys.exit(3)

    for key in 'url', 'name', 'object':
        if key not in manifest['image']:
            report.error(missing_key(key, 'image'))
            sys.exit(3)

    service = manifest['service']

    try:
        container, logname = service['log'].split('/', 1)
    except ValueError:
        report.error('Incorrect format for log entry in manifest file')

    # Use the systems certificates
    https.patch_with_certs(CERTS)

    account = AstakosClient(service['url'], service['token'])
    try:
        account.authenticate()
    except AstakosClientError as err:
        report.error("Astakos: %s" % err)
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
        # Export manifest to environment variables
        for section in manifest:
            for key, value in manifest[section].items():
                name = "ICAAS_%s_%s" % (section.upper(), key.upper())
                os.environ[name] = value

        # Use SIGHUP to unblock from the sleep if necessary
        signal.signal(signal.SIGHUP, lambda x, y: None)

        if 'ICAAS_MONITOR_SIGSTOP' in os.environ:
            # Tell service supervisor that we are ready.
            syslog.syslog(
                syslog.LOG_NOTICE, "Stopping with SIGSTOP as the "
                "environment variable ICAAS_MONITOR_SIGSTOP is defined")
            os.kill(os.getpid(), signal.SIGSTOP)
            del os.environ['ICAAS_MONITOR_SIGSTOP']

        if do_main_loop(args.interval, pithos, logname):
            report.success()
        else:
            report.error("Image creation failed. Check the log for more info")
    finally:
        os.unlink(PID)

# vim: set sta sts=4 shiftwidth=4 sw=4 et ai :
